"""Server configuration, all overridable by environment variable.

Every setting is a module-level name that the rest of the app reads at call
time (``config.DATA_ROOT``, never ``from config import DATA_ROOT``). That
indirection is what makes :func:`reload` able to change the running
configuration -- see its docstring for why that is needed.
"""

import os
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_ROOT.parent

# Session cookie name -- not environment-derived, so it lives outside reload().
SESSION_COOKIE = 'mpact_session'


def _path(env, default):
    return Path(os.environ.get(env, default)).expanduser().resolve()


def _int(env, default):
    try:
        return int(os.environ.get(env, default))
    except (TypeError, ValueError):
        return default


def _bool(env, default=False):
    raw = os.environ.get(env)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _resolve():
    """Read every setting from the current environment."""
    # Where sessions, uploads and run outputs live. One directory per session,
    # so a deployment can wipe a session by removing a single subtree.
    data_root = _path('MPACT_DATA_ROOT', SERVER_ROOT / 'var')
    reference_dir = _path('MPACT_REFERENCE_DIR', SERVER_ROOT / 'data')

    return {
        'DATA_ROOT': data_root,
        'SESSIONS_DIR': data_root / 'sessions',

        # The static front end.
        'WEB_ROOT': _path('MPACT_WEB_ROOT', REPO_ROOT / 'web'),

        # Reference data. npatlas.tsv is ~33MB and is fetched by
        # scripts/fetch_reference_data.py rather than committed.
        'REFERENCE_DIR': reference_dir,
        'NPATLAS_PATH': Path(os.environ.get('MPACT_NPATLAS_PATH',
                                            reference_dir / 'npatlas.tsv')),
        # Compounds NPAtlas lacks (SIDERITE siderophores, the lab's own
        # compound list), in NPAtlas's columns. Ships with the code rather
        # than the reference volume, and is searched together with NPAtlas.
        'NPATLAS_SUPPLEMENTAL_PATH': _path('MPACT_NPATLAS_SUPPLEMENTAL_PATH',
                                           SERVER_ROOT / 'data' / 'npatlas_supplemental.tsv'),
        # Hit-filter annotations, built by scripts/build_hit_annotations.py.
        'TAXONOMY_PATH': _path('MPACT_TAXONOMY_PATH',
                               SERVER_ROOT / 'data' / 'npatlas_taxonomy.tsv'),
        'SIDERITE_KEYS_PATH': _path('MPACT_SIDERITE_KEYS_PATH',
                                    SERVER_ROOT / 'data' / 'siderite_inchikeys.txt'),

        # The demo dataset ships *inside* the deployment, so it is resolved
        # separately from REFERENCE_DIR -- which in Docker is a mounted
        # volume that the image's own files are not part of.
        'DEMO_DIR': _path('MPACT_DEMO_DIR', SERVER_ROOT / 'data' / 'demo'),

        # Upload limits. A peak table with thousands of features across
        # hundreds of injections is genuinely large, so the default ceiling is
        # generous; lower it for a shared deployment.
        'MAX_UPLOAD_BYTES': _int('MPACT_MAX_UPLOAD_BYTES', 512 * 1024 * 1024),
        'UPLOAD_CHUNK_BYTES': 1024 * 1024,

        # Analysis jobs run in their own OS process (see jobs.py). This caps
        # how many run at once across all sessions.
        'MAX_CONCURRENT_JOBS': _int('MPACT_MAX_CONCURRENT_JOBS', 2),
        'JOB_TIMEOUT_SECONDS': _int('MPACT_JOB_TIMEOUT_SECONDS', 3600),

        # Plot payload guards: scatter plots stream every surviving feature to
        # the browser, which is fine for tens of thousands of points but not
        # millions.
        'MAX_SCATTER_POINTS': _int('MPACT_MAX_SCATTER_POINTS', 200_000),
        'MAX_HEATMAP_CELLS': _int('MPACT_MAX_HEATMAP_CELLS', 2_000_000),

        # SECURE should be on behind HTTPS.
        'SESSION_COOKIE_SECURE': _bool('MPACT_SESSION_COOKIE_SECURE', False),
        'SESSION_MAX_AGE_SECONDS': _int('MPACT_SESSION_MAX_AGE_SECONDS',
                                        60 * 60 * 24 * 30),

        # Bootstrap (PvClust) dendrograms are expensive; nboot matches the
        # desktop's hardcoded 1000. parallel=False because the server already
        # isolates each request -- see plotdata/dendrogram.py.
        'PVCLUST_NBOOT': _int('MPACT_PVCLUST_NBOOT', 1000),

        # --- authentication (see auth.py) --------------------------------
        # 'none' is right for localhost and for the tests; anything with a
        # public address wants 'code' (one shared access code) or 'users'
        # (named accounts, each with their own workspace).
        'AUTH_MODE': os.environ.get('MPACT_AUTH', 'none').strip().lower(),
        'ACCESS_CODE': os.environ.get('MPACT_ACCESS_CODE', ''),
        'USERS_FILE': _path('MPACT_USERS_FILE', data_root / 'users.txt'),
        'AUTH_MAX_AGE_SECONDS': _int('MPACT_AUTH_MAX_AGE_SECONDS',
                                     60 * 60 * 24 * 14),
        # Set this only when a reverse proxy you control (Caddy, nginx) is the
        # single way in. It makes the sign-in throttle read X-Forwarded-For,
        # which is a client-supplied header and a lie whenever the app is
        # reachable directly.
        'TRUST_PROXY': _bool('MPACT_TRUST_PROXY', False),

        'DEV_MODE': _bool('MPACT_DEV', False),
    }


def reload():
    """Re-resolve every environment-derived setting.

    A spawned analysis process imports this module while it still holds the
    *inherited* environment, and only afterwards applies the settings the
    parent handed it. Without a re-resolve at that point, parent and child
    could disagree about where the data root is, and the child would look for
    the run in the wrong place -- which is precisely how a run fails with
    "run request missing".

    Tests use it for the same reason: to point a fresh data root at an
    already-imported app, so module import order can't leak configuration
    from one test module into another.
    """
    globals().update(_resolve())


reload()
