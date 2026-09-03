"""Results: plot data, the feature table, database search, and downloads."""

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import plotdata, storage
from ..core import dbsearch, getfragdb
from ..deps import get_results, get_run
from ..plotdata import feature as featuredata
from ..results import RunResults

router = APIRouter(prefix='/api/runs/{run_id}', tags=['results'])


@router.get('/plots')
def plot_catalogue(results: RunResults = Depends(get_results)):
    """Which result tabs this run offers, and the parameters it ran with."""
    params = results.params()
    return {
        'plots': plotdata.catalogue(params),
        'params': params,
        'groups': results.biolgroups(),
        'blank_group': results.blank_group(),
        'feature_sets': [
            {'name': entry['name'], 'colour': entry['colour'], 'key': entry['key'],
             'count': len(entry.get('ions', []))}
            for entry in results.groupsets()
        ],
    }


@router.post('/plots/{name}')
def plot_payload(name: str, options: dict | None = None,
                 results: RunResults = Depends(get_results)):
    """Build one plot's data.

    POST rather than GET because plot options are structured (view, method,
    thresholds, per-column table filters) and several are long enough to be
    awkward in a query string. Nothing here mutates the run.
    """
    spec = plotdata.BUILDERS.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f'unknown plot {name!r}')
    if not spec.available(results.params()):
        missing = ', '.join(spec.requires)
        raise HTTPException(
            status_code=409,
            detail=f'"{spec.title}" needs {missing} enabled; re-run with it turned on')
    try:
        return plotdata.build(name, results, options or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/features')
def feature_table(options: dict | None = None,
                  results: RunResults = Depends(get_results)):
    """Paged/filtered/sorted feature table (the desktop's search tree)."""
    try:
        return featuredata.feature_table(results, options or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Compound ids are taken as a query parameter, never a path segment.
# Progenesis ids embed a slash (e.g. "1.46_438.1982m/z"), which no amount of
# percent-encoding survives in a path -- Starlette decodes before matching, so
# the id would split across route segments and 404.
CompoundParam = Query(..., min_length=1, max_length=512, description='Feature (Compound) id')


@router.get('/feature')
def feature_detail(compound: str = CompoundParam,
                   results: RunResults = Depends(get_results)):
    try:
        return featuredata.feature_detail(results, compound)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/feature/abundance')
def feature_abundance(compound: str = CompoundParam,
                      results: RunResults = Depends(get_results)):
    """Per-group abundances for one feature.

    A feature that did not pass filtering comes back as ``found: false`` with
    a reason rather than a 404 -- it is the expected answer for most features
    in a run, not a failure.
    """
    try:
        return featuredata.abundance(results, compound)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError:
        return {'plot': 'abundance', 'feature': compound, 'found': False,
                'reason': 'No group statistics were calculated for this feature.',
                'groups': [], 'points': []}


@router.get('/feature/spectrum')
def feature_spectrum(compound: str = CompoundParam, run=Depends(get_run),
                     results: RunResults = Depends(get_results)):
    """MS2 spectrum, if a fragment database was supplied for this run."""
    request = run.read_request() or {}
    frag_name = (request.get('inputs') or {}).get('fragments_name')
    if not frag_name:
        raise HTTPException(status_code=404,
                            detail='no fragment database was supplied for this run')
    fragpath = run.rawdata / frag_name
    if not fragpath.exists():
        raise HTTPException(status_code=404, detail='fragment database is missing')
    fragdb = _load_fragdb(fragpath)
    return featuredata.spectrum(results, fragdb, compound)


_FRAGDB_CACHE = {}


def _load_fragdb(path: Path):
    """Parse a fragment database once per (path, mtime).

    Parsing an MSP is slow enough that re-doing it on every feature click is
    noticeable, and a run's fragment file never changes after the run starts.
    """
    key = (str(path), path.stat().st_mtime_ns)
    cached = _FRAGDB_CACHE.get(key)
    if cached is None:
        cached = getfragdb.importfrag(str(path))
        _FRAGDB_CACHE.clear()      # one run's database at a time is plenty
        _FRAGDB_CACHE[key] = cached
    return cached


@router.get('/feature/matches')
def feature_matches(compound: str = CompoundParam,
                    results: RunResults = Depends(get_results)):
    """NPAtlas mass matches for one feature, within the run's ppm window."""
    params = results.params()
    detail = featuredata.feature_detail(results, compound)
    mass = detail['fields'].get('m/z')
    if mass is None:
        raise HTTPException(status_code=422, detail=f'{compound} has no m/z')
    try:
        atlas = featuredata.load_atlas(params.get('kingdom', ''), params.get('genus', ''))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    matches = featuredata.matches_for_mass(atlas, mass, params.get('ppmthresh', 10))
    return {
        'feature': compound,
        'mz': mass,
        'ppm_threshold': params.get('ppmthresh', 10),
        'kingdom': params.get('kingdom', ''),
        'genus': params.get('genus', ''),
        'fields': [{'key': key, 'label': label} for key, label in featuredata.ATLAS_FIELDS],
        'matches': matches,
    }


@router.post('/dbsearch')
def run_database_search(results: RunResults = Depends(get_results)):
    """Search every filtered feature against NPAtlas and store the hit counts.

    Calls the vendored ``dbsearch.search_npatlas`` unmodified -- the same
    function ``MainWindow.fulldbsearch`` calls -- which writes a ``hits``
    column back into ``iondict.csv``. That is what fills the feature table's
    Hits column, exactly as opening the desktop's Feature Info tab does.

    Kept as an explicit action rather than run automatically at the end of
    every analysis: it costs seconds per run, needs a ~33MB reference table
    loaded, and plenty of runs never need it.
    """
    params = results.params()
    try:
        atlas = featuredata.load_atlas(params.get('kingdom', ''), params.get('genus', ''))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    hitdb, _ = dbsearch.search_npatlas(
        results.outputdir, results.stem, atlas, float(params.get('ppmthresh', 10)))
    # iondict.csv changed on disk; the results cache is mtime-keyed, so the
    # next read picks it up without an explicit invalidation.
    return {
        'searched': len(hitdb),
        'matched': sum(1 for frame in hitdb.values() if len(frame)),
        'ppm_threshold': params.get('ppmthresh', 10),
        'kingdom': params.get('kingdom', ''),
        'genus': params.get('genus', ''),
    }


@router.get('/structure')
def structure(smiles: str = Query(..., min_length=1, max_length=2000)):
    """2D structure SVG for a SMILES string (epam.indigo, when installed)."""
    try:
        svg = featuredata.render_structure(smiles)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - an unparseable SMILES is user input
        raise HTTPException(status_code=422,
                            detail=f'could not render structure: {exc}') from exc
    return Response(content=svg, media_type='image/svg+xml',
                    headers={'Cache-Control': 'public, max-age=86400'})


# Downloadable outputs, keyed by the suffix the pipeline writes. Mirrors the
# desktop's Outputs table (docs/user-guide/outputs.md).
DOWNLOADS = {
    'formatted': ('{stem}_formatted.csv', 'Reformatted peak table'),
    'merged': ('{stem}_merged.csv', 'Formatted data with mispicked peaks merged'),
    'filtered': ('{stem}_filtered.csv', 'Peak table after all filtering'),
    'groupaverages': ('{stem}_groupaverages.csv', 'Group averages (long format)'),
    'summarydata': ('{stem}_summarydata.csv', 'Group statistics (wide format)'),
    'iondict': ('iondict.csv', 'Per-feature data for every feature'),
    'analysisinfo': ('analysisinfo.txt', 'Run summary and parameters'),
    'session': ('{stem}.mpct', 'Session file (opens in the MPACT desktop app)'),
}

# Exports whose extension follows the input file, resolved by glob.
GLOB_DOWNLOADS = {
    'filtered_source': ('{stem}_filtered_source.*', 'Filtered peak table in its source format'),
    'reindexed': ('*_reindexed.*', 'Fragment database re-indexed for GNPS2'),
}


def _resolve_download(results: RunResults, key: str):
    if key in DOWNLOADS:
        pattern, _ = DOWNLOADS[key]
        return results.outputdir / pattern.format(stem=results.stem)
    if key in GLOB_DOWNLOADS:
        pattern, _ = GLOB_DOWNLOADS[key]
        matches = sorted(results.outputdir.glob(pattern.format(stem=results.stem)))
        return matches[0] if matches else None
    return None


@router.get('/downloads')
def list_downloads(results: RunResults = Depends(get_results)):
    """Which output files this run actually produced."""
    out = []
    for key, (_, description) in list(DOWNLOADS.items()) + list(GLOB_DOWNLOADS.items()):
        path = _resolve_download(results, key)
        if path is not None and path.exists():
            out.append({'key': key, 'name': path.name, 'description': description,
                        'size': path.stat().st_size})
    return out


@router.get('/downloads/{key}')
def download(key: str, results: RunResults = Depends(get_results)):
    path = _resolve_download(results, key)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=f'{key} was not produced by this run')
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')


@router.get('/downloads.zip')
def download_all(results: RunResults = Depends(get_results)):
    """Every output file in one archive, streamed rather than buffered."""
    paths = []
    for key in list(DOWNLOADS) + list(GLOB_DOWNLOADS):
        path = _resolve_download(results, key)
        if path is not None and path.exists():
            paths.append(path)

    def generate():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, arcname=path.name)
        buffer.seek(0)
        yield from buffer

    return StreamingResponse(
        generate(), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{results.stem}_mpact.zip"'})
