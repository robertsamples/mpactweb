"""Upload library: the web stand-in for the desktop's File Selection tab.

Uploads are stored per session and reused across runs. That is the GNPS-style
half of the design -- upload a peak table once, then re-select it for as many
parameter sweeps as you like -- while the picker UX on top of it stays the
desktop's four labelled slots.

Every upload is inspected on arrival (format detection, column check, entry
count) so mistakes surface at upload time rather than three tabs later.
"""

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

import time

from .. import config, demo, inspect as inspection, mpctsession, msdial, storage
from ..deps import check_storage, get_file, get_role, get_session
from ..schemas import AnalysisSettings, FileOut, TableIn

router = APIRouter(prefix='/api/files', tags=['files'])

# Accepted extensions per slot. Peak tables are .csv (Progenesis / MZmine /
# Metaboscape / MS-DIAL) or .txt (MS-DIAL's tab-separated export).
ALLOWED_SUFFIXES = {
    'peaktable': {'.csv', '.txt'},
    'samplelist': {'.csv'},
    'metadata': {'.csv'},
    'fragments': {'.msp', '.mgf'},
}


def _normalized(session, record):
    """Rewrite an uploaded MS-DIAL peak table into the layout the pipeline
    reads (see ``msdial.py``) and rename the record if that changed its
    suffix. An export that can't be laid out is kept as uploaded; the
    inspection that follows reports why."""
    try:
        converted = inspection.normalize_peaktable(session.path_for(record))
    except msdial.MsdialError:
        return record
    if converted is None:
        return record
    return session.update_file(record.id, name=converted.name,
                               size=converted.stat().st_size)


def _as_out(record):
    return FileOut(id=record.id, kind=record.kind, name=record.name, size=record.size,
                   uploaded_at=record.uploaded_at, fmt=record.fmt, detail=record.detail)


@router.get('', response_model=list[FileOut])
def list_files(session: storage.Session = Depends(get_session)):
    """Everything in this session's library, newest first."""
    return [_as_out(record) for record in
            sorted(session.files(), key=lambda r: r.uploaded_at, reverse=True)]


@router.post('', response_model=FileOut)
async def upload_file(
    kind: str = Form(...),
    file: UploadFile = File(...),
    session: storage.Session = Depends(get_session),
    role: str = Depends(get_role),
):
    if kind not in storage.KINDS:
        raise HTTPException(status_code=400,
                            detail=f'kind must be one of {", ".join(storage.KINDS)}')

    name = storage.safe_filename(file.filename)
    suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if suffix not in ALLOWED_SUFFIXES[kind]:
        allowed = ', '.join(sorted(ALLOWED_SUFFIXES[kind]))
        raise HTTPException(status_code=400,
                            detail=f'a {kind} must be one of: {allowed} (got "{suffix or name}")')

    # A workspace already at its quota takes nothing more; one under it is
    # checked again below with the file's real size.
    await run_in_threadpool(check_storage, session, role)
    record = session.add_file(kind=kind, name=name, size=0)
    target = session.path_for(record)
    written = 0
    try:
        with target.open('wb') as handle:
            while chunk := await file.read(config.UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f'upload exceeds the {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit')
                handle.write(chunk)
    except Exception:
        # Never leave a half-written file in the library claiming to be usable.
        session.remove_file(record.id)
        raise

    try:
        await run_in_threadpool(check_storage, session, role)
    except HTTPException:
        session.remove_file(record.id)
        raise

    # Off the event loop. Laying out and converting a large peak table takes
    # seconds, and this route is async (it has to be, to stream the upload):
    # done inline, every other request on the server -- status polls, plots,
    # other people's uploads -- stopped until one person's file was inspected.
    try:
        record = await run_in_threadpool(_finish_upload, session, record, written)
    except Exception:
        session.remove_file(record.id)
        raise
    return _as_out(record)


def _finish_upload(session, record, size):
    """Record an uploaded file's size, lay out an MS-DIAL export, inspect it."""
    record = session.update_file(record.id, size=size)
    if record.kind == 'peaktable':
        record = _normalized(session, record)
    detail = inspection.inspect_file(session, record)
    return session.update_file(record.id, detail=detail, fmt=detail.get('format'))


@router.delete('/{file_id}')
def delete_file(file_id: str, session: storage.Session = Depends(get_session)):
    """Remove a library file. Runs already started keep their own copies."""
    if not session.remove_file(file_id):
        raise HTTPException(status_code=404, detail=f'file {file_id} not found')
    return {'deleted': file_id}


@router.get('/{file_id}/download')
def download_file(file_id: str, session: storage.Session = Depends(get_session)):
    record = get_file(session, file_id)
    return FileResponse(session.path_for(record), filename=record.name,
                        media_type='application/octet-stream')


@router.get('/{file_id}/injections')
def peaktable_injections(file_id: str, session: storage.Session = Depends(get_session)):
    """Every injection name in a peak table, for building a sample list."""
    record = get_file(session, file_id, 'peaktable')
    try:
        names = inspection.peaktable_injections(session, record)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f'could not read injections: {exc}') from exc
    return {'names': names}


def _table_record(session, file_id):
    record = get_file(session, file_id)
    if record.kind not in inspection.REQUIRED_COLUMNS:
        raise HTTPException(status_code=400,
                            detail='only sample lists and metadata files are tables')
    return record


@router.get('/{file_id}/table')
def read_table(file_id: str, session: storage.Session = Depends(get_session)):
    """A sample list or metadata file as columns + rows of strings."""
    record = _table_record(session, file_id)
    try:
        columns, rows = inspection.read_table(session.path_for(record))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f'could not read CSV: {exc}') from exc
    return {'id': record.id, 'kind': record.kind, 'name': record.name,
            'columns': columns, 'rows': rows}


def _save_table(session, record, payload):
    try:
        inspection.write_table(session.path_for(record), record.kind,
                               payload.columns, payload.rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    path = session.path_for(record)
    detail = inspection.inspect_file(session, record)
    return session.update_file(record.id, size=path.stat().st_size, detail=detail,
                               uploaded_at=time.time())


@router.put('/{file_id}/table', response_model=FileOut)
def replace_table(file_id: str, payload: TableIn,
                  session: storage.Session = Depends(get_session)):
    """Overwrite a sample list or metadata file with an edited table. Runs
    already started keep their own copies, so this never changes a result."""
    record = _table_record(session, file_id)
    if record.kind != payload.kind:
        raise HTTPException(status_code=400, detail=f'file {file_id} is a {record.kind}')
    return _as_out(_save_table(session, record, payload))


@router.post('/table', response_model=FileOut)
def create_table(payload: TableIn, session: storage.Session = Depends(get_session)):
    """Save a table built in the browser as a new library file."""
    name = storage.safe_filename(payload.name or f'{payload.kind}.csv')
    if not name.lower().endswith('.csv'):
        name = name.rsplit('.', 1)[0] + '.csv' if '.' in name else name + '.csv'
    record = session.add_file(kind=payload.kind, name=name, size=0)
    try:
        record = _save_table(session, record, payload)
    except Exception:
        session.remove_file(record.id)
        raise
    return _as_out(record)


@router.get('/demo')
def demo_info(role: str = Depends(get_role)):
    """The bundled demo datasets this account may use, and whether each is
    installed here."""
    datasets = [dataset.describe() for dataset in demo.DATASETS if dataset.visible_to(role)]
    return {
        'available': any(d['available'] for d in datasets),
        'default': demo.DEFAULT if any(d['id'] == demo.DEFAULT for d in datasets)
                   else (datasets[0]['id'] if datasets else None),
        'datasets': datasets,
    }


@router.post('/demo', response_model=list[FileOut])
def load_demo(payload: dict | None = Body(default=None),
              session: storage.Session = Depends(get_session),
              role: str = Depends(get_role)):
    """Copy a demo dataset into this session's library.

    Copied rather than referenced, so the demo behaves exactly like an upload
    from here on -- re-selectable, re-runnable, and deletable -- and so a run
    still converts its own copy of the peak table without touching the
    pristine bundled one. A dataset without a sample list or metadata (the
    CHM350 ones) returns only the files it has.

    Calling it twice returns the existing entries instead of duplicating
    them, so a double click doesn't fill the library with copies.
    """
    try:
        dataset = demo.get((payload or {}).get('id'))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f'no demo dataset {exc}') from exc
    if not dataset.visible_to(role):
        # As if it did not exist: an outside account is not told what the
        # internal datasets are called.
        raise HTTPException(status_code=404, detail=f"no demo dataset '{dataset.id}'")
    check_storage(session, role)
    if not dataset.available():
        raise HTTPException(
            status_code=503,
            detail=f'the {dataset.name} demo dataset is not installed on this server '
                   f'(missing: {", ".join(dataset.missing())})')

    existing = {(record.kind, record.name): record for record in session.files()}
    loaded = []
    for kind, filename in dataset.files:
        name = storage.safe_filename(demo.library_name(filename))
        already = existing.get((kind, name))
        if already is not None:
            loaded.append(already)
            continue
        record = session.add_file(kind=kind, name=name, size=0)
        try:
            demo.copy_into(filename, session.path_for(record))
            record = session.update_file(record.id,
                                         size=session.path_for(record).stat().st_size)
            if kind == 'peaktable':
                record = _normalized(session, record)
        except Exception:
            session.remove_file(record.id)
            raise
        detail = inspection.inspect_file(session, record)
        loaded.append(session.update_file(record.id, detail=detail, fmt=detail.get('format')))

    return [_as_out(record) for record in loaded]


@router.post('/session')
async def import_session(
    file: UploadFile = File(...),
    session: storage.Session = Depends(get_session),
    role: str = Depends(get_role),
):
    """Open a saved ``.mpct`` session: the desktop's File > Open.

    A save file carries its own copies of the peak table, sample list,
    metadata and fragment database, so importing one fills the library and
    all four slots as well as every filtering and analysis setting. The files
    land in the library like any other upload, which means the imported
    session can then be edited and re-run rather than only replayed.
    """
    name = storage.safe_filename(file.filename or 'session.mpct')
    suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if suffix not in mpctsession.SUFFIXES:
        allowed = ', '.join(sorted(mpctsession.SUFFIXES))
        raise HTTPException(status_code=400,
                            detail=f'a session file must be one of: {allowed} '
                                   f'(got "{suffix or name}")')

    blob = bytearray()
    while chunk := await file.read(config.UPLOAD_CHUNK_BYTES):
        blob.extend(chunk)
        if len(blob) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f'session file exceeds the '
                       f'{config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit')

    await run_in_threadpool(check_storage, session, role, len(blob))
    # Unpickling, writing and inspecting the files is all blocking work; see
    # upload_file for why it must not run on the event loop.
    return await run_in_threadpool(_import_session, session, name, bytes(blob))


def _import_session(session, name, blob):
    warnings = []
    try:
        payload = mpctsession.load(blob)
        settings = mpctsession.settings_from(payload['parameters'], warnings)
        files = mpctsession.input_files(payload, warnings)
    except mpctsession.SessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Validate through the same schema a run request uses, so an import can
    # never leave the form in a state the Run button would reject.
    try:
        settings = AnalysisSettings(**settings).model_dump()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f'the settings in this session file are not usable: {exc}') from exc

    # An identical file already in the library is reused rather than copied
    # again, so importing the same session twice does not fill the library.
    existing = {(record.kind, record.name, record.size): record
                for record in session.files()}
    inputs, loaded = {}, []
    for kind, filename, content in files:
        safe = storage.safe_filename(filename)
        already = existing.get((kind, safe, len(content)))
        if already is None:
            record = session.add_file(kind=kind, name=safe, size=len(content))
            session.path_for(record).write_bytes(content)
            if kind == 'peaktable':
                record = _normalized(session, record)
            detail = inspection.inspect_file(session, record)
            record = session.update_file(record.id, detail=detail,
                                         fmt=detail.get('format'))
        else:
            record = already
        inputs[kind] = record.id
        loaded.append(record)

    for kind in ('peaktable', 'samplelist', 'metadata'):
        if kind not in inputs:
            warnings.append(f'no {kind} in this session file; select one before running')

    return {
        'name': name,
        'files': [_as_out(record) for record in loaded],
        'inputs': inputs,
        'settings': settings,
        'warnings': warnings,
    }


@router.post('/groups')
def biological_groups(
    payload: dict,
    response: Response,
    session: storage.Session = Depends(get_session),
):
    """Biological groups for a peak table + sample list + metadata triple.

    Called as soon as all three slots are filled, to populate the blank /
    experimental / control pickers and the Plot Feature Set editor -- the same
    moment ``MainWindow.getgroups`` runs on the desktop.
    """
    peaktable = get_file(session, payload.get('peaktable', ''), 'peaktable')
    samplelist = get_file(session, payload.get('samplelist', ''), 'samplelist')
    metadata = get_file(session, payload.get('metadata', ''), 'metadata')

    try:
        groups, unresolved, warnings = inspection.biological_groups(
            session, peaktable, samplelist, metadata)
    except ValueError as exc:
        # The pipeline's own "Data read failure: Check input files" message --
        # nearly always a Sample_Code/Injection column mismatch.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f'could not derive biological groups: {exc}') from exc

    if unresolved:
        warnings = list(warnings) + [
            f'{len(unresolved)} injection(s) in the peak table are not in the sample '
            'list and metadata yet, so the group list is incomplete and the analysis '
            'cannot run until they are added']

    return {
        'groups': [str(group) for group in groups],
        'unresolved': unresolved[:50],
        'unresolved_total': len(unresolved),
        'warnings': warnings,
        'suggested': inspection.suggest_groups(groups),
    }
