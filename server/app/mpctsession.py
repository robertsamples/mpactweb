"""Read a desktop ``.mpct`` session file back into a web session.

The desktop's Save writes one pickle holding the analysis parameters, the
Plot Feature Sets, and a copy of every input file (``MainWindow.write_save``);
its File > Open reads that pickle, drops the input files back onto disk and
pushes every parameter back into the form (``UIFunctions.loadsession``). This
module is the web half of exactly that, so a ``.mpct`` produced by either app
reopens in either app.

Two things differ from the desktop, both forced by the fact that the file
arrives over HTTP from someone we cannot vouch for:

* **Unpickling is restricted.** ``pickle.load`` on an untrusted file is
  arbitrary code execution. ``_SessionUnpickler`` allows only the classes a
  genuine save file contains, and refuses everything else, so a hostile
  ``.mpct`` fails to load rather than running.
* **Paths are made portable.** Save files pickle ``pathlib.WindowsPath``
  objects, which cannot even be constructed on Linux. They are unpickled as
  the matching *pure* path, which can, and only their file names are used.
"""

import io
import pickle
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import ions
from .core import groupsets
from .schemas import AnalysisSettings

# Extensions offered in the file picker. The format is `.mpct`; `.mpact` is
# accepted too because that is what people call it.
SUFFIXES = {'.mpct', '.mpact'}


class SessionError(Exception):
    """The file is not a readable MPACT session."""


class LegacyQuery:
    """Stand-in for ``main.query``, the pre-``GroupSet`` feature-set shape.

    Save files pickle that class as ``main.query`` when the desktop was
    imported as a module, and as ``__main__.query`` when it was launched as a
    script -- which is how the desktop is normally run, so script-launched
    saves are the common case. Neither module exists on the server, nor is
    wanted there; the attributes are restored onto this object instead and
    handed to ``GroupSet.from_legacy``, the same path the desktop takes for
    those files.
    """

    def __init__(self):
        self.name = ''
        self.src = ''
        self.incl = ''
        self.excl = ''
        self.colour = '#000000'


# Classes a real save file legitimately contains. Anything outside this table
# is refused: the point is that loading a .mpct can only ever rebuild data.
_ALLOWED = {
    ('MSFaST', 'analysis_parameters'),
    ('groupsets', 'GroupSet'),
    ('groupsets', 'GroupSetModel'),
    ('collections', 'OrderedDict'),
    ('copyreg', '_reconstructor'),
    ('pathlib', 'Path'),
    ('pathlib', 'PurePath'),
    ('pathlib', 'PurePosixPath'),
    ('pathlib', 'PureWindowsPath'),
    ('numpy', 'dtype'),
    ('numpy', 'ndarray'),
    ('numpy', 'matrix'),
    ('numpy', 'bool_'),
}

# Plain data types, which pandas and numpy name explicitly while rebuilding an
# index or an array. Everything that *does* something -- eval, exec, getattr,
# open, __import__ -- is absent by design.
_ALLOWED_BUILTINS = {
    'bool', 'bytearray', 'bytes', 'complex', 'dict', 'float', 'frozenset',
    'int', 'list', 'object', 'range', 'set', 'slice', 'str', 'tuple',
}

# A path written on one platform has to load on the other: WindowsPath cannot
# be instantiated on Linux, nor PosixPath on Windows. The pure forms can be
# built anywhere, and `.name` is all this module ever asks of them.
_PATH_SUBSTITUTES = {
    ('pathlib', 'WindowsPath'): PureWindowsPath,
    ('pathlib', 'PosixPath'): PurePosixPath,
}

# pandas and numpy reach for a scattering of private reconstruction helpers
# when rebuilding a DataFrame; allowing their packages wholesale (rather than
# naming each one, which changes between releases) still keeps the boundary
# where it matters: no os, no subprocess, no builtins.eval.
_ALLOWED_PACKAGES = ('pandas', 'numpy')


class _SessionUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        substitute = _PATH_SUBSTITUTES.get((module, name))
        if substitute is not None:
            return substitute
        if module in ('main', '__main__') and name == 'query':
            return LegacyQuery
        if module == 'builtins' and name in _ALLOWED_BUILTINS:
            return super().find_class(module, name)
        root = module.split('.')[0]
        if (module, name) in _ALLOWED or root in _ALLOWED_PACKAGES:
            return super().find_class(module, name)
        raise SessionError(
            f'this session file wants to load {module}.{name}, which MPACT '
            'session files do not contain. It may be corrupt, or not a '
            'session file at all.')


def load(data):
    """Unpickle session bytes, or raise ``SessionError``."""
    try:
        payload = _SessionUnpickler(io.BytesIO(data)).load()
    except SessionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SessionError(f'could not read this session file: {exc}') from exc

    if not isinstance(payload, dict) or 'parameters' not in payload:
        raise SessionError('this file is not an MPACT session file '
                           '(no analysis parameters inside).')
    return payload


# --- parameters -> AnalysisSettings ----------------------------------------

def _number(params, attr, fallback):
    try:
        return float(getattr(params, attr))
    except (AttributeError, TypeError, ValueError):
        return fallback


def _flag(params, attr, fallback=True):
    value = getattr(params, attr, None)
    return fallback if value is None else bool(value)


def _ionmode(params):
    """The web's ``ionmode``, or the desktop config file's ``mode``."""
    mode = getattr(params, 'ionmode', None) or getattr(params, 'adduct_mode', None) or 'pos'
    return 'neg' if str(mode).strip().lower().startswith('neg') else 'pos'


def _samplenorm(params):
    """The beta sample normalization method; 'none' for a session without it."""
    method = str(getattr(params, 'samplenorm', 'none') or 'none').strip().lower()
    return method if method in ('pqn', 'sum') else 'none'


def _ion_forms(params, attr, table):
    """A saved selection of ion forms, less any this version does not know;
    the defaults when the session has none (every desktop session)."""
    saved = getattr(params, attr, None)
    if not isinstance(saved, (list, tuple)):
        return ions.default_selection(table)
    return [label for label in dict.fromkeys(saved) if ions.known(label)]


def settings_from(params, warnings):
    """Map a pickled ``analysis_parameters`` onto ``AnalysisSettings`` fields.

    The inverse of ``params.build_params``, and deliberately as forgiving as
    the desktop's ``loadsession``: a field a given save file happens not to
    carry falls back to the schema default rather than failing the import.
    """
    filters = list(getattr(params, 'graphfilters', []) or [])

    cvparam = str(getattr(params, 'cvparam', '') or '')
    blankfilparam = str(getattr(params, 'blankfilparam', '') or '')
    stats_groups = list(getattr(params, 'statstgrps', []) or [])

    maxiso = _number(params, 'maxisowin', 4.0)
    if not 0 <= maxiso <= 5:
        warnings.append(f'max isotopic shift in the session file was {maxiso:g}; '
                        'reset to 4')
        maxiso = 4.0

    settings = {
        # Filtering Settings
        'relfil': 'rel' in filters or _flag(params, 'relfil'),
        'merge': _flag(params, 'merge'),
        'ringingwin': _number(params, 'ringingwin', 0.5),
        'isopeakwin': _number(params, 'isopeakwin', 0.01),
        'RTwin': _number(params, 'RTwin', 0.005),
        'maxisowin': maxiso,

        'blnkfltr': _flag(params, 'blnkfltr'),
        'blnkgrp': str(getattr(params, 'blnkgrp', '') or ''),
        'blankfilparam': 'absolute' if 'absolute' in blankfilparam else 'relative',
        'blankfilthresh': _number(params, 'blankfilthresh', 0.01),

        'CVfil': 'cv' in filters or _flag(params, 'CVfil'),
        'cvthresh': _number(params, 'cvthresh', 0.2),
        'cvparam': 'average CV' if 'average' in cvparam else 'median CV',
        # A session from before the cutoff gets the form's default, like the
        # beta filters below.
        'cvgate': _flag(params, 'cvgate', AnalysisSettings.model_fields['cvgate'].default),
        'cvgate_frac': _number(params, 'cvgate_frac', 0.01),
        'samplenorm': _samplenorm(params),
        'samplenorm_skip': [str(g) for g in getattr(params, 'samplenorm_skip', None) or []],

        'decon': 'insource' in filters or _flag(params, 'decon'),
        'deconthresh': _number(params, 'deconthresh', 0.95),

        # Ion mode and the beta filters. A session saved before they existed
        # carries none of them and gets the form's defaults (the mass defect
        # filter on, the other two off); one that has them keeps what it says.
        'ionmode': _ionmode(params),
        'mdfil': _flag(params, 'mdfil', AnalysisSettings.model_fields['mdfil'].default),
        'md_halogen': _flag(params, 'md_halogen', False),
        'relfil_beta': str(getattr(params, 'relfil_mode', '') or '').lower() == 'beta',
        'iso_ppm': _number(params, 'iso_ppm', 3.0),
        'iso_max_n': int(_number(params, 'iso_max_n', 3)),
        'iso_ratio_max': _number(params, 'iso_ratio_max', 0.7),
        'ring_ppm': _number(params, 'ring_ppm', 800.0),
        'ring_ratio_max': _number(params, 'ring_ratio_max', 0.01),
        'dimer_tol': _number(params, 'dimer_tol', 0.02),
        'adductgrp': _flag(params, 'adductgrp', False),
        'adduct_ppm': _number(params, 'adduct_ppm', 3.0),
        'adduct_rt_win': _number(params, 'adduct_rt_win', 0.02),
        'adducts': _ion_forms(params, 'adducts', ions.GROUPING_DEFAULT),
        'corr_cut': _number(params, 'corr_cut', 0.75),
        'corr_p': _number(params, 'corr_p', 0.05),

        # Analysis Settings
        'FC': _flag(params, 'FC'),
        'Ttest': _flag(params, 'Ttest'),
        'FDR': _flag(params, 'FDR'),
        'expgrp': str(stats_groups[0]) if len(stats_groups) > 0 else '',
        'ctrgrp': str(stats_groups[1]) if len(stats_groups) > 1 else '',

        'PCA': _flag(params, 'PCA'),
        'Dendrogram': _flag(params, 'Dendrogram'),
        'MZRTplt': _flag(params, 'MZRTplt'),
        'KMD': _flag(params, 'KMD'),
        'FC3Dplt': _flag(params, 'FC3Dplt'),
        'Volcanoplt': _flag(params, 'Volcanoplt'),

        # Feature Info
        'ppmthresh': _number(params, 'ppmthresh', 10.0),
        'kingdom': str(getattr(params, 'kingdom', '') or ''),
        'genus': str(getattr(params, 'genus', '') or ''),
        'taxon_level': str(getattr(params, 'taxon_level', '') or ''),
        'taxon': str(getattr(params, 'taxon', '') or ''),
        'search_adducts': _ion_forms(params, 'search_adducts', ions.SEARCH_DEFAULT),

        'groupsets': groupsets_from(params, warnings),
    }

    # The beta filters' numbers are only ever typed into a text file on the
    # desktop, so one out of range is put back to its default rather than
    # failing the import.
    defaults = AnalysisSettings()
    for key in ('iso_ppm', 'ring_ppm', 'dimer_tol', 'adduct_ppm', 'adduct_rt_win'):
        if settings[key] <= 0:
            warnings.append(f'{key} in the session file was {settings[key]:g}; '
                            f'reset to {getattr(defaults, key):g}')
            settings[key] = getattr(defaults, key)
    for key in ('iso_ratio_max', 'ring_ratio_max', 'corr_cut', 'corr_p'):
        if not 0 < settings[key] <= 1:
            warnings.append(f'{key} in the session file was {settings[key]:g}; '
                            f'reset to {getattr(defaults, key):g}')
            settings[key] = getattr(defaults, key)
    if not 1 <= settings['iso_max_n'] <= 5:
        warnings.append(f"iso_max_n in the session file was {settings['iso_max_n']}; reset to 3")
        settings['iso_max_n'] = 3

    # A threshold of zero would fail validation on the way back in; the
    # desktop simply shows whatever it loaded, so say what happened.
    for key, floor in (('ringingwin', 0.5), ('isopeakwin', 0.01), ('RTwin', 0.005),
                       ('cvthresh', 0.2), ('ppmthresh', 10.0)):
        if settings[key] <= 0:
            warnings.append(f'{key} in the session file was {settings[key]:g}; '
                            f'reset to {floor:g}')
            settings[key] = floor
    if not 0 < settings['deconthresh'] <= 1:
        warnings.append('deconvolution threshold in the session file was '
                        f"{settings['deconthresh']:g}; reset to 0.95")
        settings['deconthresh'] = 0.95
    return settings


def groupsets_from(params, warnings):
    """Plot Feature Sets from the pickled ``queries`` list.

    ``GroupSetModel.from_legacy_list`` is the desktop's own reader and takes
    both shapes: current ``GroupSet`` objects and the older ``query`` objects
    (as ``LegacyQuery`` here).
    """
    queries = getattr(params, 'queries', None) or []
    if not queries:
        return []
    try:
        model = groupsets.GroupSetModel.from_legacy_list(queries)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f'could not read the Plot Feature Sets from this session: {exc}')
        return []
    return [{'name': str(item.name), 'src': [str(g) for g in item.src],
             'incl': [str(g) for g in item.incl], 'excl': [str(g) for g in item.excl],
             'colour': str(item.colour or '#000000')}
            for item in model]


# --- the input files -------------------------------------------------------

# (savedata key, parameters attribute holding the original name, library kind)
INPUTS = (
    ('peaktable', 'filename', 'peaktable'),
    ('samplelist', 'samplelistfilename', 'samplelist'),
    ('extractmetadata', 'extractmetadatafilename', 'metadata'),
)


def _name_of(params, attr, fallback):
    value = getattr(params, attr, None)
    if not value:
        return fallback
    name = PureWindowsPath(str(value)).name if '\\' in str(value) else Path(str(value)).name
    return name or fallback


def input_files(payload, warnings):
    """The input files a session file carries, as ``(kind, name, bytes)``.

    The three tables were pickled as header-less DataFrames read straight off
    the CSVs, so writing them back with no header and no index reproduces the
    original file byte for byte. The peak table in a save is MPACT's own
    canonical layout, which is why re-importing one does not re-run format
    conversion on it.
    """
    params = payload['parameters']
    files = []

    for key, attr, kind in INPUTS:
        frame = payload.get(key)
        if frame is None:
            warnings.append(f'this session file carries no {kind}; '
                            'select one before running')
            continue
        name = _name_of(params, attr, f'{kind}.csv')
        try:
            text = frame.to_csv(header=False, index=False, lineterminator='\n')
        except TypeError:      # pandas < 1.5 spells it line_terminator
            text = frame.to_csv(header=False, index=False, line_terminator='\n')
        except AttributeError as exc:
            raise SessionError(f'the {kind} inside this session file is not a '
                               f'table ({exc})') from exc
        files.append((kind, name, text.encode('utf-8')))

    fragments = payload.get('fragdb')
    fragname = _name_of(params, 'fragfilename', '')
    if isinstance(fragments, str) and fragments != 'None' and fragname:
        files.append(('fragments', fragname, fragments.encode('utf-8')))

    return files
