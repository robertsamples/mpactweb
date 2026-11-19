"""Request/response models.

``AnalysisSettings`` is a field-for-field mirror of what
``MainWindow.enumerate_inputs`` (desktop ``main.py``) reads off the File
Selection / Filtering Settings / Analysis Settings tabs, with the same
defaults as the Qt Designer widgets so a fresh web form and a fresh desktop
launch agree. Names match the ``analysis_parameters`` attribute they become
(``relfil``, ``cvthresh``, ``blnkgrp``, ...) rather than being renamed to
something more web-ish -- the mapping in ``params.py`` stays inspectable
against the desktop code that way.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import ions

Kind =Literal['peaktable', 'samplelist', 'metadata', 'fragments']


class GroupSetIn(BaseModel):
    """One "Plot Feature Set" -- the web form of ``groupsets.GroupSet``.

    ``src``/``incl``/``excl`` are the desktop's "May be in" / "Must be in" /
    (implicitly) everything left in "Available groups". The desktop keeps
    unassigned groups in ``excl``; the web editor does the same so the
    presence/absence rule is identical.
    """
    name: str = 'New Feature Set'
    src: list[str] = Field(default_factory=list)
    incl: list[str] = Field(default_factory=list)
    excl: list[str] = Field(default_factory=list)
    colour: str = '#000000'

    @field_validator('colour')
    @classmethod
    def _check_colour(cls, value):
        value = str(value).strip()
        if not value.startswith('#'):
            value = '#' + value
        if len(value) != 7 or any(c not in '0123456789abcdefABCDEF' for c in value[1:]):
            raise ValueError('colour must be a #rrggbb hex string')
        return value.lower()


class AnalysisSettings(BaseModel):
    """Everything the pipeline needs beyond the input files themselves."""
    model_config = ConfigDict(extra='forbid')

    # --- Filtering Settings: mispicked peaks -----------------------------
    relfil: bool = True                     # checkBox_mp
    merge: bool = True                      # checkBox_merge
    ringingwin: float = 0.5                 # lineEdit_ringwin
    isopeakwin: float = 0.01                # lineEdit_isowin (also -> dimerpeakwin)
    RTwin: float = 0.005                    # lineEdit_rtwin
    maxisowin: float = 4.0                  # combo_maxisoshift (items "0".."5")

    # --- Filtering Settings: blank filtering / group parsing -------------
    blnkfltr: bool = True                   # checkBox_blankfilter
    blnkgrp: str = ''                       # combo_blankfil_name
    blankfilparam: Literal['relative', 'absolute'] = 'relative'
    blankfilthresh: float = 0.01            # rel 0.01 / abs 100

    # --- Filtering Settings: CV filtering --------------------------------
    CVfil: bool = True                      # checkBox_cv
    cvthresh: float = 0.2                   # lineEdit_cvthresh
    cvparam: Literal['average CV', 'median CV'] = 'median CV'   # radioButton_medcv default
    # Beta (filter.cvfilter): samples whose mean is below cvgate_frac of the
    # feature's highest sample mean are left out of its CV. On here; the
    # desktop default, and the published method, is off.
    cvgate: bool = True
    cvgate_frac: float = 0.01

    # --- Filtering Settings: in-source deconvolution ---------------------
    decon: bool = True                      # checkBox_decon
    deconthresh: float = 0.95               # lineEdit_insourcethresh

    # --- Ion mode --------------------------------------------------------
    # Which ions the features are: read by the adduct filter and the database
    # search. The desktop's only equivalent is ``mode`` in adduct_config.txt.
    ionmode: Literal['pos', 'neg'] = 'pos'

    # --- Beta filters ----------------------------------------------------
    # A run with none of them on is the published pipeline. The desktop reads
    # the same switches from adduct_config.txt, where all three start off;
    # here the mass defect filter starts on and the other two off. A saved
    # session or an earlier run that predates them gets these defaults.
    #
    # Mass defect filter (filter.mdfilter).
    mdfil: bool = True
    md_halogen: bool = False
    # Isotope filter (filter.relationalfilter_beta). While on it runs in place
    # of the mispicked-peak correction above, whose settings are kept.
    relfil_beta: bool = False
    iso_ppm: float = 3.0
    iso_max_n: int = 3
    iso_ratio_max: float = 0.7
    ring_ppm: float = 800.0
    ring_ratio_max: float = 0.01
    dimer_tol: float = 0.02
    # Adduct filter (filter.adductgroup). While on it runs in place of the
    # in-source deconvolution above, whose settings are kept.
    adductgrp: bool = False
    adduct_ppm: float = 3.0
    adduct_rt_win: float = 0.02
    # Forms the adduct filter groups, as labels from ions.FORMS. One list for
    # both modes; a run uses the ones that belong to its ion mode.
    adducts: list[str] = Field(
        default_factory=lambda: ions.default_selection(ions.GROUPING_DEFAULT))
    # Cross-sample correlation two ion forms must show (Pearson r above
    # corr_cut with p below corr_p). The pipeline has one pair of values for
    # both the isotope and the adduct filter.
    corr_cut: float = 0.75
    corr_p: float = 0.05

    # Beta sample normalization (stats.samplenormalize), after filtering:
    # 'pqn' (probabilistic quotient) or 'sum' (total signal).
    samplenorm: Literal['none', 'pqn', 'sum'] = 'none'
    # Groups left unscaled and out of the factors, besides the blank group
    # (which always is): a media blank, say.
    samplenorm_skip: list[str] = Field(default_factory=list)

    # --- Analysis Settings: calculations ---------------------------------
    FC: bool = True                         # checkBox_fc
    Ttest: bool = True                      # checkBox_ttest
    FDR: bool = True                        # checkBox_FDR
    expgrp: str = ''                        # combo_expgrp   -> statstgrps[0]
    ctrgrp: str = ''                        # combo_ctrgrp   -> statstgrps[1]

    # --- Analysis Settings: which plots to generate ----------------------
    # These stay meaningful on the web even though rendering is client-side:
    # the pipeline reads FC/Ttest to decide whether to compute fold change and
    # p/q values at all, and the flags gate which result tabs are offered.
    PCA: bool = True                        # checkBox_pca
    Dendrogram: bool = True                 # checkBox_dend
    MZRTplt: bool = True                    # checkBox_mzrt
    KMD: bool = True                        # checkBox_kmd
    FC3Dplt: bool = True                    # checkBox_3dfc
    Volcanoplt: bool = True                 # checkBox_volcano

    # --- Feature Info: database search -----------------------------------
    ppmthresh: float = 10.0                 # lineEdit_ppmthresh
    kingdom: str = ''                       # combo_kingdom ('', Bacterium, Fungus)
    genus: str = ''                         # lineEdit_genus
    # Web-only: narrow the search at any rank (kingdom ... species). When set,
    # it supersedes kingdom/genus, which are still filled for the desktop.
    taxon_level: str = ''
    taxon: str = ''
    # Ion forms a feature's m/z is tried as, as labels from ions.FORMS. One
    # list for both modes, like ``adducts``. The positive defaults are the
    # pair the desktop search has built in.
    search_adducts: list[str] = Field(
        default_factory=lambda: ions.default_selection(ions.SEARCH_DEFAULT))

    # --- Plot Feature Sets ------------------------------------------------
    # Empty means "use the desktop's implicit default": one set covering every
    # non-blank group ('Features not in blanks' / 'All features'). See
    # params.default_groupsets.
    groupsets: list[GroupSetIn] = Field(default_factory=list)

    @field_validator('ringingwin', 'isopeakwin', 'RTwin', 'cvthresh', 'ppmthresh',
                     'iso_ppm', 'ring_ppm', 'dimer_tol', 'adduct_ppm', 'adduct_rt_win')
    @classmethod
    def _positive(cls, value, info):
        if value <= 0:
            raise ValueError(f'{info.field_name} must be greater than 0')
        return value

    @field_validator('deconthresh', 'iso_ratio_max', 'ring_ratio_max', 'corr_cut', 'corr_p',
                     'cvgate_frac')
    @classmethod
    def _correlation_range(cls, value, info):
        if not 0 < value <= 1:
            raise ValueError(f'{info.field_name} must be in (0, 1]')
        return value

    @field_validator('iso_max_n')
    @classmethod
    def _isotope_count_range(cls, value):
        if not 1 <= value <= 5:
            raise ValueError('iso_max_n must be between 1 and 5')
        return value

    @field_validator('adducts', 'search_adducts')
    @classmethod
    def _known_ion_forms(cls, value, info):
        unknown = [label for label in value if not ions.known(label)]
        if unknown:
            raise ValueError(f'{info.field_name}: unknown ion form(s) {", ".join(unknown)}')
        return list(dict.fromkeys(value))

    @field_validator('maxisowin')
    @classmethod
    def _iso_shift_range(cls, value):
        if not 0 <= value <= 5:
            raise ValueError('maxisowin must be between 0 and 5')
        return value


class RunInputs(BaseModel):
    """Library file ids for the four File Selection slots."""
    model_config = ConfigDict(extra='forbid')

    peaktable: str
    samplelist: str
    metadata: str
    fragments: Optional[str] = None


def _clean_label(value):
    """A run label as stored: one line, trimmed, or None for none."""
    if value is None:
        return None
    value = ' '.join(str(value).split())[:120]
    return value or None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    inputs: RunInputs
    settings: AnalysisSettings = Field(default_factory=AnalysisSettings)
    label: Optional[str] = None

    _label = field_validator('label')(_clean_label)


class FlagUpdate(BaseModel):
    """Flag features with a colour, or (``colour`` null) take their flags off."""
    model_config = ConfigDict(extra='forbid')

    ids: list[str] = Field(max_length=100_000)
    colour: Optional[str] = None

    @field_validator('colour')
    @classmethod
    def _check_colour(cls, value):
        if value is None:
            return None
        value = str(value).strip().lower()
        if len(value) != 7 or value[0] != '#' or any(c not in '0123456789abcdef'
                                                     for c in value[1:]):
            raise ValueError('colour must be a #rrggbb hex string')
        return value


class RunLabel(BaseModel):
    """A new label for an existing run."""
    model_config = ConfigDict(extra='forbid')

    label: Optional[str] = None

    _label = field_validator('label')(_clean_label)


class LoginRequest(BaseModel):
    """Credentials from the sign-in form.

    ``username`` is unused in shared-code mode, where there is one secret and
    no accounts, so it is optional rather than two separate request shapes.
    """
    model_config = ConfigDict(extra='forbid')

    username: str = ''
    password: str = ''


class EmailStart(BaseModel):
    """Ask for a sign-in code."""
    model_config = ConfigDict(extra='forbid')

    email: str = Field(max_length=320)


class EmailVerify(BaseModel):
    """Sign in with the code that was sent."""
    model_config = ConfigDict(extra='forbid')

    email: str = Field(max_length=320)
    code: str = Field(max_length=32)


class AccountUpdate(BaseModel):
    """An administrator's change to an account. ``role`` null puts it back
    to the role its address gives it; leave a field out to leave it alone."""
    model_config = ConfigDict(extra='forbid')

    email: str = Field(max_length=320)
    role: Optional[Literal['internal', 'external', 'blocked']] = None
    reset_role: bool = False
    approve: bool = False


class AllowlistEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')

    entry: str = Field(max_length=320)


class FileOut(BaseModel):
    id: str
    kind: str
    name: str
    size: int
    uploaded_at: float
    fmt: Optional[str] = None
    detail: dict = Field(default_factory=dict)


class TableIn(BaseModel):
    """A sample list or metadata table edited in the browser."""
    model_config = ConfigDict(extra='forbid')

    kind: Literal['samplelist', 'metadata']
    name: str = ''
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[str]] = Field(max_length=50_000)


class JobStatus(BaseModel):
    run_id: str
    state: Literal['queued', 'running', 'complete', 'failed', 'cancelled']
    stage: str = ''
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    label: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
