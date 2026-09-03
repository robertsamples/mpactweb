"""MPACT Web -- FastAPI application.

Serves the single-page front end and the JSON API in front of the vendored
MPACT desktop pipeline. See ``docs/architecture.md`` for why the analysis
runs server-side rather than in the browser.
"""

import contextlib
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs, storage
from .routers import files, results, runs

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
