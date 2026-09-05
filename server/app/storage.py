"""On-disk layout for sessions, the upload library, and analysis runs.

The desktop app points the pipeline at files the user picked from their own
filesystem and an output directory they chose. The web app has to invent
both, and the shape below is what makes the desktop pipeline run unmodified:

    sessions/<session>/                     one browser session
        library.json                        upload index (see FileRecord)
        library/<file_id>/<original name>    uploaded files, kept for reuse
        runs/<run_id>/
            request.json                    the submitted RunRequest
            status.json                     JobStatus, polled by the browser
            log.txt                         pipeline stdout/stderr
            rawdata/                        inputs, under their original names
            output/                         == desktop's "output directory"
                <peaktable stem>/           == analysis_params.outputdir
                    plots/                  created for parity; unused server-side

Uploads live in the *library* and are copied into a run's ``rawdata/`` when
that run starts, so re-running with different parameters never re-uploads,
and an in-flight run can't be disturbed by the user deleting a library file.
That upload-once-then-select flow is the GNPS-style half of the design; the
file-picker UX on top of it is the desktop's.
"""

import json
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import config

# Input roles, matching the desktop's File Selection tab.
KINDS = ('peaktable', 'samplelist', 'metadata', 'fragments')

_SAFE_NAME = re.compile(r'[^A-Za-z0-9._-]+')


def new_id(prefix=''):
    return prefix + secrets.token_hex(8)


def safe_filename(name, fallback='upload.csv'):
    """Reduce a client-supplied filename to something safe to place on disk.

    The peak table's *stem* becomes a directory name and is embedded in every
    output filename by the pipeline itself, so this has to be conservative:
    no separators, no traversal, no reserved Windows device names.
    """
    name = Path(str(name or '')).name
    name = _SAFE_NAME.sub('_', name).strip('._') or fallback
    stem, dot, ext = name.rpartition('.')
    reserved = {'con', 'prn', 'aux', 'nul', 'clock$'} | {
        f'{d}{i}' for d in ('com', 'lpt') for i in range(1, 10)}
    if (stem or name).lower() in reserved:
        name = f'_{name}'
    return name[:180]


@dataclass
class FileRecord:
    """One uploaded file in a session's library."""
    id: str
    kind: str
    name: str
    size: int
    uploaded_at: float
    fmt: Optional[str] = None          # detected peak-table format, if any
    detail: dict = field(default_factory=dict)   # per-kind inspection summary

    @property
    def dirname(self):
        return self.id


class Session:
    """A session directory plus its library index."""

    def __init__(self, session_id):
        self.id = session_id
        self.root = config.SESSIONS_DIR / session_id
        self.library_dir = self.root / 'library'
        self.runs_dir = self.root / 'runs'
        self.index_path = self.root / 'library.json'

    # --- lifecycle --------------------------------------------------------

    @classmethod
    def create(cls):
        session = cls(new_id('s_'))
        session.library_dir.mkdir(parents=True, exist_ok=True)
        session.runs_dir.mkdir(parents=True, exist_ok=True)
        session._write_index([])
        return session

    @classmethod
    def at_id(cls, session_id):
        """The session with exactly this id, created if it does not exist yet.

        ``load_or_create`` mints a *random* id when the directory is missing,
        which is right for a stale browser cookie and wrong for a named
        account's workspace: that id is derived from who you are, so the first
        visit has to land in it rather than somewhere new.
        """
        session = cls(session_id)
        if session.root.is_dir():
            session.touch()
            return session
        session.library_dir.mkdir(parents=True, exist_ok=True)
        session.runs_dir.mkdir(parents=True, exist_ok=True)
        session._write_index([])
        return session

    @classmethod
    def load_or_create(cls, session_id):
        """Resolve a client-supplied session id, or mint a fresh session.

        A syntactically invalid or unknown id yields a new session rather than
        an error: the id comes from a cookie that may be stale (server wiped,
        session expired), and the user shouldn't be stuck with a dead app.
        """
        if session_id and re.fullmatch(r's_[0-9a-f]{16}', session_id):
            session = cls(session_id)
            if session.root.is_dir():
                session.touch()
                return session
        return cls.create()

    def touch(self):
        try:
            os.utime(self.root, None)
        except OSError:  # pragma: no cover - best effort
            pass

    # --- library index ----------------------------------------------------

    def _write_index(self, records):
        payload = [asdict(r) if isinstance(r, FileRecord) else r for r in records]
        tmp = self.index_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.index_path)   # atomic: a crash mid-write can't truncate the index

    def files(self):
        if not self.index_path.exists():
            return []
        try:
            raw = json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        records = []
        for item in raw:
            known = {f: item.get(f) for f in FileRecord.__dataclass_fields__}
            known.setdefault('detail', {})
            records.append(FileRecord(**{k: v for k, v in known.items() if v is not None
                                         or k in ('fmt',)}))
        return records

    def file(self, file_id):
        for record in self.files():
            if record.id == file_id:
                return record
        return None

    def path_for(self, record):
        return self.library_dir / record.dirname / record.name

    def add_file(self, kind, name, size, fmt=None, detail=None):
        record = FileRecord(id=new_id('f_'), kind=kind, name=safe_filename(name),
                            size=size, uploaded_at=time.time(), fmt=fmt,
                            detail=detail or {})
        (self.library_dir / record.dirname).mkdir(parents=True, exist_ok=True)
        records = self.files()
        records.append(record)
        self._write_index(records)
        return record

    def update_file(self, file_id, **fields):
        records = self.files()
        for record in records:
            if record.id == file_id:
                for key, value in fields.items():
                    setattr(record, key, value)
                self._write_index(records)
                return record
        return None

    def remove_file(self, file_id):
        records = self.files()
        keep = [r for r in records if r.id != file_id]
        if len(keep) == len(records):
            return False
        self._write_index(keep)
        shutil.rmtree(self.library_dir / file_id, ignore_errors=True)
        return True

    # --- runs -------------------------------------------------------------

    def runs(self):
        if not self.runs_dir.is_dir():
            return []
        return sorted((Run(self, p.name) for p in self.runs_dir.iterdir() if p.is_dir()),
                      key=lambda r: r.created_at, reverse=True)

    def new_run(self):
        run = Run(self, new_id('r_'))
        run.rawdata.mkdir(parents=True, exist_ok=True)
        run.output_root.mkdir(parents=True, exist_ok=True)
        return run

    def run(self, run_id):
        if not re.fullmatch(r'r_[0-9a-f]{16}', run_id or ''):
            return None
        run = Run(self, run_id)
        return run if run.root.is_dir() else None


class Run:
    """One analysis run's directory tree."""

    def __init__(self, session, run_id):
        self.session = session
        self.id = run_id
        self.root = session.runs_dir / run_id
        self.rawdata = self.root / 'rawdata'
        self.output_root = self.root / 'output'   # the desktop's "output directory"
        self.request_path = self.root / 'request.json'
        self.status_path = self.root / 'status.json'
        self.log_path = self.root / 'log.txt'

    @property
    def created_at(self):
        try:
            return self.root.stat().st_ctime
        except OSError:
            return 0.0

    # --- request / status -------------------------------------------------

    def write_request(self, payload):
        self.request_path.write_text(json.dumps(payload, indent=2, default=str))

    def read_request(self):
        try:
            return json.loads(self.request_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def write_status(self, status):
        tmp = self.status_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(status, indent=2, default=str))
        tmp.replace(self.status_path)

    def read_status(self):
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def append_log(self, text):
        with self.log_path.open('a', encoding='utf-8', errors='replace') as handle:
            handle.write(text)

    def read_log(self, tail_bytes=64 * 1024):
        if not self.log_path.exists():
            return ''
        with self.log_path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            return handle.read().decode('utf-8', errors='replace')

    # --- pipeline paths ---------------------------------------------------

    def peaktable_path(self):
        """The peak table inside ``rawdata/``. Recorded in the request, but
        derivable so results survive a request.json that predates a field."""
        request = self.read_request() or {}
        name = (request.get('inputs') or {}).get('peaktable_name')
        if name:
            candidate = self.rawdata / name
            if candidate.exists():
                return candidate
        for path in sorted(self.rawdata.glob('*')):
            if path.is_file() and path.suffix.lower() in ('.csv', '.txt', '.tsv'):
                return path
        return None

    def outputdir(self):
        """``analysis_params.outputdir``: ``<output root>/<peak table stem>``.

        Mirrors what ``MainWindow.enumerate_inputs`` does with the user's
        chosen output directory, because every path the pipeline writes (and
        every path the result readers look for) is derived from it.
        """
        peaktable = self.peaktable_path()
        if peaktable is None:
            return None
        return self.output_root / peaktable.stem

    def is_complete(self):
        status = self.read_status() or {}
        return status.get('state') == 'complete'


def iter_sessions():
    if not config.SESSIONS_DIR.is_dir():
        return []
    return [Session(p.name) for p in config.SESSIONS_DIR.iterdir() if p.is_dir()]


def ensure_dirs():
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    config.REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


def prune_sessions(max_age_seconds=config.SESSION_MAX_AGE_SECONDS):
    """Delete session directories untouched for longer than ``max_age_seconds``.

    Called at startup. Uploaded peak tables are large and a public deployment
    would otherwise accumulate them indefinitely.
    """
    cutoff = time.time() - max_age_seconds
    removed = 0
    for session in iter_sessions():
        try:
            if session.root.stat().st_mtime < cutoff:
                shutil.rmtree(session.root, ignore_errors=True)
                removed += 1
        except OSError:  # pragma: no cover
            continue
    return removed
