"""Results: plot data, the feature table, database search, and downloads."""

import csv
import functools
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import config, ions, plotdata, storage
from .. import results as resultstore
from ..core import csvcache, dbsearch, getfragdb
from ..deps import get_results, get_run
from ..plotdata import analogues as analoguedata
from ..plotdata import feature as featuredata
from ..plotdata import formula as formuladata
from ..plotdata import hits as hitdata
from ..plotdata import msms as msmsdata
from ..results import Memo, RunResults
from ..schemas import FlagUpdate


def _json_default(value):
    """numpy scalars and arrays a builder let through, as plain JSON."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f'{type(value).__name__} is not JSON serialisable')


def to_json(payload):
    """A payload as compact JSON bytes.

    Plot and table payloads are lists of tens of thousands of numbers.
    Returning the dict and letting FastAPI encode it walks every one of them
    in Python first (``jsonable_encoder``) and only then serialises; going
    straight to ``json.dumps`` is the same output for a fraction of the time.
    ``allow_nan=False`` keeps the strictness that path had: a NaN a builder
    failed to clean is an error here, not invalid JSON in the browser.
    """
    return json.dumps(payload, separators=(',', ':'), allow_nan=False,
                      default=_json_default).encode('utf-8')


def json_response(payload):
    body = payload if isinstance(payload, bytes) else to_json(payload)
    return Response(content=body, media_type='application/json')


# Finished plot payloads, as the bytes that go on the wire. A payload is a
# pure function of the run's output files and the request's options, so it is
# keyed on both; a file rewritten in place (the database search rewrites
# iondict.csv) changes the key. Reloading the page, coming back to a tab with
# its earlier options, or opening a second window then costs a dictionary
# lookup instead of another clustering or ordination.
_PLOT_CACHE = Memo(limit=256, size_of=len, max_bytes=lambda: config.PLOT_CACHE_BYTES)


def _run_stamp(results):
    """Identity of everything a plot builder reads for this run."""
    stamp = [str(results.outputdir)]
    for path in (results.iondict_path, results.filtered_path, results.summary_path,
                 results.groupaverages_path, results.outputdir / 'groupsets.json',
                 results.outputdir / 'runmeta.json', results.run.root / 'params.json'):
        try:
            stat = path.stat()
            stamp.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamp.append(None)
    return tuple(stamp)

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
    options = options or {}
    try:
        key = (name, json.dumps(options, sort_keys=True, default=str),
               config.PVCLUST_NBOOT, config.MAX_SCATTER_POINTS, config.MAX_HEATMAP_CELLS,
               config.MAX_HEATMAP_ROWS, _run_stamp(results))
    except (TypeError, ValueError):
        key = None
    try:
        if key is None:
            return json_response(plotdata.build(name, results, options))
        return json_response(_PLOT_CACHE.get(
            key, lambda: to_json(plotdata.build(name, results, options))))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/features')
def feature_table(options: dict | None = None, run=Depends(get_run),
                  results: RunResults = Depends(get_results)):
    """Paged/filtered/sorted feature table (the desktop's search tree)."""
    options = options or {}
    fragpath = _fragment_path(run)
    ms2, stamp = _ms2_restriction(fragpath, options)
    try:
        table = featuredata.feature_table(results, options, ms2_ids=ms2, ms2_stamp=stamp)
        table['has_fragments'] = fragpath is not None
        return json_response(table)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _ms2_restriction(fragpath, options):
    """The "MS/MS required" id set and the identity of the file it is from."""
    if fragpath is None or not options.get('ms2_only'):
        return None, None
    return _fragment_ids(fragpath), _file_stamp(fragpath)


@router.post('/features/export')
def feature_export(options: dict | None = None, run=Depends(get_run),
                   results: RunResults = Depends(get_results)):
    """The feature table as a CSV: the view the options describe, or with
    ``ids`` just those features. Takes the table's own options, so what is
    exported is what is on screen, in the same order."""
    options = options or {}
    ids = options.get('ids')
    if ids is not None and (not isinstance(ids, list) or len(ids) > featuredata.MAX_ROWS):
        raise HTTPException(status_code=422, detail='ids must be a list of feature ids')
    ms2, stamp = _ms2_restriction(_fragment_path(run), options)
    try:
        header, rows = featuredata.export_table(results, options, ms2_ids=ms2,
                                                ms2_stamp=stamp, ids=ids)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(header)
    writer.writerows([['' if value is None else value for value in row] for row in rows])
    name = f'{results.stem}_{"selected" if ids is not None else "features"}.csv'
    # With a byte-order mark, so a spreadsheet reads the Greek letters and
    # primes in compound names as what they are.
    return Response(content=buffer.getvalue().encode('utf-8-sig'), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


@router.get('/flags')
def get_flags(results: RunResults = Depends(get_results)):
    """The features flagged in this run, and the colour of each."""
    return {'flags': results.flags()}


@router.post('/flags')
def set_flags(payload: FlagUpdate, results: RunResults = Depends(get_results)):
    """Flag ``ids`` with ``colour``, or clear their flags when it is null.

    Flags are kept with the run, so they are there from another browser and
    are written into the feature export. Ids this run does not have are
    ignored rather than stored.
    """
    path = results.flags_path
    with resultstore.exclusive(path):       # two tabs flagging at once
        flags = results.flags()
        if payload.colour is None:
            for compound in payload.ids:
                flags.pop(compound, None)
        else:
            known = results.feature_positions()
            for compound in payload.ids:
                if compound in known:
                    flags[compound] = payload.colour
        if flags:
            storage.write_text_atomic(path, json.dumps(flags))
        else:
            path.unlink(missing_ok=True)
    return {'flags': flags}


@router.get('/taxa')
def taxa(level: str = Query(..., max_length=16), q: str = Query('', max_length=200),
         results: RunResults = Depends(get_results)):
    """Taxon names at one level, for the hit filter's suggestions."""
    try:
        return {'level': level, 'names': hitdata.suggest_taxa(results.params(), level, q)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('/substructure/check')
def substructure_check(query: str = Query(..., min_length=1, max_length=2000)):
    """Whether a substructure query parses."""
    try:
        hitdata.parse_query(query)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'ok': True}


# Compound ids are taken as a query parameter, never a path segment.
# Progenesis ids embed a slash (e.g. "1.46_438.1982m/z"), which no amount of
# percent-encoding survives in a path -- Starlette decodes before matching, so
# the id would split across route segments and 404.
CompoundParam = Query(..., min_length=1, max_length=512, description='Feature (Compound) id')


@router.get('/feature')
def feature_detail(compound: str = CompoundParam,
                   results: RunResults = Depends(get_results)):
    return _detail(results, compound)


def _detail(results, compound):
    """A feature's fields, or a 404 for an id this run does not have.

    Every per-feature route starts here. Left uncaught, the "not in this run"
    error surfaced as a 500 on the routes that did not happen to catch it --
    which is what a selection carried over from another run produced.
    """
    try:
        return featuredata.feature_detail(results, compound)
    except featuredata.UnknownFeature as exc:
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


def _file_stamp(path: Path):
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _fragment_path(run):
    request = run.read_request() or {}
    name = (request.get('inputs') or {}).get('fragments_name')
    path = run.rawdata / name if name else None
    return path if path is not None and path.exists() else None


# Parsed fragment databases and the ids that have a spectrum in each, for the
# runs most recently looked at. These held a single entry, which is right for
# one person and wrong for a room: with two people clicking through different
# runs, every click evicted the other's database and re-parsed its own.
_FRAGDB_CACHE = Memo(limit=6)
_FRAGMENT_IDS = Memo(limit=32)


def _fragment_ids(path: Path):
    """Compound ids with a non-empty spectrum in the fragment database."""
    def build():
        ids = set()
        for compound, entry in getattr(_load_fragdb(path), 'ions', {}).items():
            pattern = np.asarray(getattr(entry, 'pattern', []), dtype=float)
            if pattern.ndim == 2 and pattern.shape[0] > 0:
                ids.add(str(compound))
        return frozenset(ids)
    return _FRAGMENT_IDS.get(_file_stamp(path), build)


def _load_fragdb(path: Path):
    """Parse a fragment database once per (path, mtime).

    Parsing an MSP is slow enough that re-doing it on every feature click is
    noticeable, and a run's fragment file never changes after the run starts.
    Two requests for the same database share one parse.
    """
    _FRAGDB_CACHE.limit = max(1, config.FRAGDB_CACHE_ENTRIES)
    return _FRAGDB_CACHE.get(_file_stamp(path),
                             lambda: getfragdb.importfrag(str(path)))


def _feature_pattern(run, compound):
    """The feature's MS/MS spectrum as stored, or None if the run has no
    fragment file or the feature no spectrum in it."""
    fragpath = _fragment_path(run)
    if fragpath is None:
        return None
    entry = getattr(_load_fragdb(fragpath), 'ions', {}).get(str(compound))
    if entry is None:
        return None
    pattern = np.asarray(entry.pattern, dtype=float)
    return pattern if pattern.ndim == 2 and pattern.shape[0] else None


@router.get('/feature/matches')
def feature_matches(compound: str = CompoundParam,
                    filter: str = Query('', max_length=4000),
                    run=Depends(get_run),
                    results: RunResults = Depends(get_results)):
    """NPAtlas mass matches for one feature, within the run's ppm window.
    ``filter`` is an optional JSON hit-filter spec; each match then carries
    ``passes``.

    When the feature has an MS/MS spectrum, each match also says how much
    of it the match's formula can account for (``fragments``, ``score``),
    and ``fragments`` on the response holds the fragment formulae per
    candidate parent, for labelling the spectrum."""
    params = results.params()
    try:
        spec = hitdata.normalise(json.loads(filter)) if filter else None
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    detail = _detail(results, compound)
    mass = detail['fields'].get('m/z')
    if mass is None:
        raise HTTPException(status_code=422, detail=f'{compound} has no m/z')
    try:
        atlas = featuredata.atlas_for(params)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    matches = featuredata.matches_for_mass(atlas, mass, params.get('ppmthresh', 10),
                                           ions.run_ions(params))
    for match in matches:
        match['compound_smiles'] = hitdata.canonical_smiles(match.get('compound_smiles'))
    try:
        matches = hitdata.annotate_matches(params, matches, spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pattern = _feature_pattern(run, compound)
    fragments = {} if pattern is None else formuladata.score_matches(
        matches, pattern, mass, params.get('ppmthresh', 10),
        key_of=lambda match: ((match['compound_molecular_formula'], match['adduct'])
                              if match.get('compound_molecular_formula') else None),
        ppm_of=lambda match: match['ppm'])
    return {
        'feature': compound,
        'mz': mass,
        'fragments': fragments,
        'ppm_threshold': params.get('ppmthresh', 10),
        'kingdom': params.get('kingdom', ''),
        'genus': params.get('genus', ''),
        'taxon_level': params.get('taxon_level', ''),
        'taxon': params.get('taxon', ''),
        'ions': ions.run_ions(params),
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

    That function is written around NPAtlas's two positive-ion columns. A
    run searching any other set of ion forms (negative mode, or a different
    choice of adducts) goes through ``featuredata.search_atlas``, which
    counts and writes the same way for the forms given.

    Kept as an explicit action rather than run automatically at the end of
    every analysis: it costs seconds per run, needs a ~33MB reference table
    loaded, and plenty of runs never need it.
    """
    params = results.params()
    try:
        atlas = featuredata.atlas_for(params)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # The search rewrites iondict.csv in place. Held exclusively so that a
    # second search of the same run (the automatic one on first opening
    # Feature Info and a click on the button can overlap) queues behind this
    # one instead of interleaving two writes of the same file, and so that a
    # table refresh waits for the rewrite rather than parsing half of it.
    labels = tuple(ions.run_ions(params))
    ppm = float(params.get('ppmthresh', 10))
    with resultstore.exclusive(results.iondict_path):
        try:
            if labels == ions.LEGACY_SEARCH:
                hitdb, _ = dbsearch.search_npatlas(results.outputdir, results.stem, atlas, ppm)
                searched = len(hitdb)
                matched = sum(1 for frame in hitdb.values() if len(frame))
            else:
                searched, matched = featuredata.search_atlas(
                    results.outputdir, results.stem, atlas, ppm, labels)
        finally:
            # The vendored search memoises its reads by path alone and only
            # drops them on success; after a failure the next search would
            # start from the stale copies.
            csvcache.invalidate()
    # iondict.csv changed on disk; the results cache is mtime-keyed, so the
    # next read picks it up without an explicit invalidation.
    return {
        'searched': searched,
        'matched': matched,
        'ions': list(labels),
        'ppm_threshold': params.get('ppmthresh', 10),
        'kingdom': params.get('kingdom', ''),
        'genus': params.get('genus', ''),
        'taxon_level': params.get('taxon_level', ''),
        'taxon': params.get('taxon', ''),
    }


@router.post('/msmssearch')
def run_msms_search(options: dict | None = None, run=Depends(get_run),
                    results: RunResults = Depends(get_results)):
    """MS/MS search of every filtered feature's spectrum against the
    MS-DIAL library. ``min_score`` (cosine) and ``match_precursor``."""
    options = options or {}
    fragpath = _fragment_path(run)
    if fragpath is None:
        raise HTTPException(status_code=404, detail='no fragment database was supplied for this run')
    try:
        min_score = float(options.get('min_score', msmsdata.DEFAULT_MIN_SCORE))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail='min_score must be a number') from exc
    if not 0 < min_score <= 1:
        raise HTTPException(status_code=422, detail='min_score must be between 0 and 1')
    try:
        # One search of a run at a time; a second request waits its turn.
        with resultstore.exclusive(results.outputdir / msmsdata.RESULTS_FILE):
            record = msmsdata.search(results, _load_fragdb(fragpath), min_score,
                                     bool(options.get('match_precursor', True)))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'searched': record['searched'], 'features': record['features'],
            'with_candidates': record['with_candidates'],
            'matched': sum(1 for found in record['hits'].values() if found),
            'min_score': record['min_score'], 'match_precursor': record['match_precursor']}


@router.get('/feature/msms')
def feature_msms(compound: str = CompoundParam,
                 filter: str = Query('', max_length=4000),
                 run=Depends(get_run),
                 results: RunResults = Depends(get_results)):
    """A feature's MS/MS library matches from the run's last MS/MS search.
    ``filter`` is an optional JSON hit-filter spec; each match then carries
    ``passes``. Matches made on the precursor also carry the fragment
    formulae their own formula gives the spectrum, as ``/feature/matches``
    does; an analogue's formula is not the feature's, so those do not."""
    detail = _detail(results, compound)
    mass = detail['fields'].get('m/z')
    try:
        spec = hitdata.normalise(json.loads(filter)) if filter else None
        payload = msmsdata.matches(results, compound, mass)
        hitdata.annotate_library_matches(payload['matches'], spec)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload['fragments'] = {}
    pattern = _feature_pattern(run, compound) if payload.get('match_precursor') else None
    if pattern is not None and mass is not None:
        payload['fragments'] = formuladata.score_matches(
            payload['matches'], pattern, mass, results.params().get('ppmthresh', 10),
            key_of=lambda match: ((match['formula'],
                                   match['feature_ion'] or match['precursortype'])
                                  if match.get('formula') else None),
            ppm_of=lambda match: abs(1e6 * (match['delta_mz'] or 0.0) / float(mass)))
    return payload


@router.post('/analogues')
def feature_analogues(options: dict | None = None,
                      results: RunResults = Depends(get_results)):
    """Features that differ from ``compound`` by small formula steps (CH2,
    H2, O, NH, and pairs of them). ``units``, ``combine`` and ``tolerance``
    (Da) are optional."""
    options = options or {}
    compound = options.get('compound')
    if not isinstance(compound, str) or not compound:
        raise HTTPException(status_code=422, detail='compound is required')
    try:
        return analoguedata.find(results, compound, units=options.get('units'),
                                 combine=bool(options.get('combine', True)),
                                 tolerance=options.get('tolerance'))
    except featuredata.UnknownFeature as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/structure')
def structure(smiles: str = Query(..., min_length=1, max_length=2000)):
    """2D structure SVG for a SMILES string (epam.indigo, when installed)."""
    try:
        svg = _structure_svg(smiles)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - an unparseable SMILES is user input
        raise HTTPException(status_code=422,
                            detail=f'could not render structure: {exc}') from exc
    return Response(content=svg, media_type='image/svg+xml',
                    headers={'Cache-Control': 'public, max-age=86400'})


@functools.lru_cache(maxsize=512)
def _structure_svg(smiles):
    """Rendered structures, kept: laying out a macrocycle takes a noticeable
    fraction of a second, and the same handful of matches is drawn again each
    time someone steps back through a feature's hit list. (A failed render
    raises and is not cached.)"""
    return featuredata.render_structure(smiles)


# Downloadable outputs, keyed by the suffix the pipeline writes. Mirrors the
# desktop's Outputs table (docs/user-guide/outputs.md).
DOWNLOADS = {
    'formatted': ('{stem}_formatted.csv', 'Reformatted peak table'),
    'merged': ('{stem}_merged.csv', 'Formatted data with mispicked peaks merged'),
    'filtered': ('{stem}_filtered.csv', 'Peak table after all filtering'),
    'filtered_raw': ('{stem}_filtered_raw.csv',
                     'Peak table after all filtering, before sample normalization'),
    'normfactors': ('{stem}_normfactors.csv',
                    'Sample normalization factors (relative signal of each sample)'),
    'groupaverages': ('{stem}_groupaverages.csv', 'Group averages (long format)'),
    'summarydata': ('{stem}_summarydata.csv', 'Group statistics (wide format)'),
    'iondict': ('iondict.csv', 'Per-feature data for every feature'),
    'analysisinfo': ('analysisinfo.txt', 'Run summary and parameters'),
    'adduct_attribution': ('adduct_attribution.csv',
                           'Adduct groups: each kept feature and the ion forms removed for it'),
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

    return StreamingResponse(
        _zip_stream(paths), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{results.stem}_mpact.zip"',
                 # Already compressed. Declaring an encoding is what makes the
                 # gzip middleware leave the stream alone; without it the
                 # archive was deflated a second time on its way out, which
                 # cost more than building it and saved nothing.
                 'Content-Encoding': 'identity'})


class _ZipSink:
    """Write-only target for ``ZipFile`` that hands back what was written.

    It has no ``tell``/``seek``, which is how ``zipfile`` knows to write a
    forward-only archive (sizes in a trailing data descriptor)."""

    def __init__(self):
        self._chunks = []

    def write(self, data):
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def drain(self):
        chunks, self._chunks = self._chunks, []
        return chunks


# Faster than the default level 6 for an archive only slightly larger.
_ZIP_LEVEL = 3


def _zip_stream(paths):
    """The archive, yielded as it is built.

    The whole thing used to be assembled in memory before the first byte was
    sent: the download sat silent for as long as compression took, and a
    large run's outputs were held in RAM once per person downloading.
    """
    sink = _ZipSink()
    with zipfile.ZipFile(sink, 'w', zipfile.ZIP_DEFLATED, compresslevel=_ZIP_LEVEL) as archive:
        for path in paths:
            try:
                source = open(path, 'rb')
            except OSError:
                continue        # deleted since it was listed
            # ZIP64 only where a file needs it: some unzip tools still stumble on it.
            big = path.stat().st_size > 2 ** 31
            with source, archive.open(path.name, 'w', force_zip64=big) as entry:
                while chunk := source.read(1 << 20):
                    entry.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
