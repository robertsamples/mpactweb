"""MPACT Web -- FastAPI application.

Serves the single-page front end and the JSON API in front of the vendored
MPACT desktop pipeline. See ``docs/architecture.md`` for why the analysis
runs server-side rather than in the browser.
"""

import contextlib
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from . import auth, config, jobs, storage
from .routers import authroutes, files, results, runs

os.environ.setdefault('MPLBACKEND', 'Agg')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_dirs()
    removed = storage.prune_sessions()
    if removed:
        print(f'pruned {removed} expired session(s)')
    jobs.manager.start()
    try:
        yield
    finally:
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
app.add_middleware(GZipMiddleware, minimum_size=1024)

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
        if (auth.required() and path.startswith('/api/')
                and not path.startswith(_OPEN_PATHS)
                and not request.state.user):
            return JSONResponse(status_code=401,
                                content={'detail': 'sign in to use this server'})
        return await call_next(request)


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
    from .core import indigo_available
    return {
        'status': 'ok',
        'npatlas': config.NPATLAS_PATH.exists(),
        'structures': indigo_available,
        'max_upload_mb': config.MAX_UPLOAD_BYTES // (1024 * 1024),
        'max_concurrent_jobs': config.MAX_CONCURRENT_JOBS,
    }


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Return JSON for API failures so the client can show the real message.

    Without this, an unhandled exception on an /api/ route yields an HTML
    error page that the front end's ``response.json()`` chokes on, hiding the
    actual cause behind a parse error.
    """
    import traceback
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
