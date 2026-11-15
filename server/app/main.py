"""MPACT Web -- FastAPI application.

Serves the single-page front end and the JSON API in front of the vendored
MPACT desktop pipeline. See ``docs/architecture.md`` for why the analysis
runs server-side rather than in the browser.
"""

import asyncio
import contextlib
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from . import auth, config, jobs, storage
from .routers import authroutes, files, results, runs

os.environ.setdefault('MPLBACKEND', 'Agg')


PRUNE_INTERVAL_SECONDS = 24 * 60 * 60


async def _prune_periodically():
    """Reclaim expired sessions once a day.

    Pruning only at startup is fine for a laptop and useless for a server
    that stays up for a term: uploads and run outputs are tens of megabytes
    each, and nothing else ever deletes them.
    """
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            removed = await asyncio.to_thread(storage.prune_sessions)
            if removed:
                print(f'pruned {removed} expired session(s)')
        except Exception:  # noqa: BLE001 - a sweep failing must not stop the server
            traceback.print_exc()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_dirs()
    removed = storage.prune_sessions()
    if removed:
        print(f'pruned {removed} expired session(s)')
    jobs.manager.start()
    from .core import rdkit_available
    from .plotdata.feature import atlas_available
    if config.PREWARM and rdkit_available and atlas_available():
        from .plotdata import hits
        hits.prewarm()
    sweeper = asyncio.create_task(_prune_periodically())
    try:
        yield
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await jobs.manager.shutdown()


app = FastAPI(
    title='MPACT - Metabolomics Peak Analysis Computational Tool',
    description='Web front end for the MPACT metabolomics pipeline',
    version='1.0.0',
    lifespan=lifespan,
)

# Plot payloads are JSON arrays of floats, which compress hard: the heatmap
# for ~1000 features is 2.7 MB raw and 1.1 MB gzipped, and Plotly itself is
# 4.3 MB raw and 1.2 MB gzipped. Without this every plot pays full freight on
# the wire, which is most of why a big heatmap felt slow to appear.
#
# Level 4, not the middleware's default of 9: on a 600 KB payload of numbers
# level 9 spends ~240 ms to produce 201 KB where level 4 spends ~9 ms to
# produce 209 KB. At 9 the compression cost more than building most plots.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)

# Routes that must answer before anyone is signed in: the health check (used
# by container orchestrators, which have no cookies) and the login endpoints
# themselves. Everything else under /api/ needs a valid token whenever
# authentication is switched on.
_OPEN_PATHS = ('/api/health', '/api/auth/')


class AuthMiddleware(BaseHTTPMiddleware):
    """Identify the caller, and turn away unauthenticated API requests.

    A middleware rather than a per-route dependency on purpose: a route added
    later is protected because it exists, not because someone remembered to
    decorate it. The static front end stays open so the login page can load;
    it holds no data of its own.
    """

    async def dispatch(self, request, call_next):
        token = request.cookies.get(auth.AUTH_COOKIE)
        request.state.user = auth.read_token(token) if token else None

        path = request.url.path
        api = path.startswith('/api/')
        if (auth.required() and api
                and not path.startswith(_OPEN_PATHS)
                and not request.state.user):
            return JSONResponse(status_code=401,
                                content={'detail': 'sign in to use this server'})

        # The upload routes count bytes as they copy the file into the
        # library, but by then the form parser has already spooled the whole
        # body to disk. A request that declares itself over the limit is
        # refused here instead, before any of it is read.
        if api and request.method in ('POST', 'PUT'):
            declared = request.headers.get('content-length', '')
            if declared.isdigit() and int(declared) > config.MAX_UPLOAD_BYTES + _FORM_OVERHEAD_BYTES:
                return JSONResponse(status_code=413, content={
                    'detail': f'upload exceeds the '
                              f'{config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit'})

        response = await call_next(request)
        if 'cache-control' not in response.headers:
            response.headers['Cache-Control'] = _cache_policy(path)
        return response


# Room for the multipart boundaries and the form's other fields.
_FORM_OVERHEAD_BYTES = 1024 * 1024


def _cache_policy(path):
    """What a browser may do with a response that set no policy of its own.

    Static files carry an ETag and a Last-Modified but said nothing about
    freshness, which leaves it to the browser's guess -- typically a tenth of
    the file's age. After an update that meant some of the front end's
    modules came from the new deployment and some from the cache, for hours.
    ``no-cache`` still caches; it only makes the browser ask, and an
    unchanged file costs a 304. The images never change under a given name.

    API responses are a workspace's data and its downloads (a run's
    ``iondict.csv`` is rewritten by a database search), so they are not kept.
    """
    if path.startswith('/api/'):
        return 'no-store'
    if path.startswith('/assets/'):
        return 'public, max-age=86400'
    return 'no-cache'


app.add_middleware(AuthMiddleware)

app.include_router(authroutes.router)
app.include_router(files.router)
app.include_router(runs.router)
app.include_router(results.router)


@app.get('/api/health')
def health():
    """Server capability report, used by the front end to hide what is
    unavailable (structure rendering, database search) instead of letting the
    user hit an error."""
    from .core import rdkit_available, structures_available
    from .plotdata.feature import atlas_available
    from .plotdata.msms import available as msms_available
    return {
        'status': 'ok',
        'npatlas': atlas_available(),
        'structures': structures_available,
        'chemistry': rdkit_available,
        'msms': msms_available(),
        'max_upload_mb': config.MAX_UPLOAD_BYTES // (1024 * 1024),
        'max_concurrent_jobs': config.MAX_CONCURRENT_JOBS,
    }


@app.get('/api/taxa')
def taxa(level: str, q: str = ''):
    """Taxon names in the whole atlas, for the setup form's suggestions."""
    from fastapi import HTTPException

    from .plotdata.hits import suggest_taxa
    if len(level) > 16 or len(q) > 200:
        raise HTTPException(status_code=422, detail='query too long')
    try:
        return {'level': level, 'names': suggest_taxa({}, level, q)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Return JSON for API failures so the client can show the real message.

    Without this, an unhandled exception on an /api/ route yields an HTML
    error page that the front end's ``response.json()`` chokes on, hiding the
    actual cause behind a parse error.
    """
    traceback.print_exc()
    detail = f'{type(exc).__name__}: {exc}' if config.DEV_MODE else 'internal server error'
    return JSONResponse(status_code=500, content={'detail': detail})


# --- static front end -------------------------------------------------------
# Mounted last so it can't shadow an /api/ route.

if config.WEB_ROOT.is_dir():
    app.mount('/src', StaticFiles(directory=config.WEB_ROOT / 'src'), name='src')
    app.mount('/styles', StaticFiles(directory=config.WEB_ROOT / 'styles'), name='styles')
    assets_dir = config.WEB_ROOT / 'assets'
    if assets_dir.is_dir():
        app.mount('/assets', StaticFiles(directory=assets_dir), name='assets')
    vendor_dir = config.WEB_ROOT / 'vendor'
    if vendor_dir.is_dir():
        app.mount('/vendor', StaticFiles(directory=vendor_dir), name='vendor')

    @app.get('/')
    def index():
        return FileResponse(config.WEB_ROOT / 'index.html')

    @app.get('/favicon.ico')
    def favicon():
        icon = config.WEB_ROOT / 'favicon.ico'
        if icon.exists():
            return FileResponse(icon)
        return JSONResponse(status_code=404, content={'detail': 'no favicon'})
