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

Kind = Literal['peaktable', 'samplelist', 'metadata', 'fragments']


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

    # --- Filtering Settings: in-source deconvolution ---------------------
    decon: bool = True                      # checkBox_decon
    deconthresh: float = 0.95               # lineEdit_insourcethresh

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

    # --- Plot Feature Sets ------------------------------------------------
    # Empty means "use the desktop's implicit default": one set covering every
    # non-blank group ('Features not in blanks' / 'All features'). See
    # params.default_groupsets.
    groupsets: list[GroupSetIn] = Field(default_factory=list)

    @field_validator('ringingwin', 'isopeakwin', 'RTwin', 'cvthresh', 'ppmthresh')
    @classmethod
    def _positive(cls, value, info):
        if value <= 0:
            raise ValueError(f'{info.field_name} must be greater than 0')
        return value

    @field_validator('deconthresh')
    @classmethod
    def _correlation_range(cls, value):
        if not 0 < value <= 1:
            raise ValueError('deconthresh must be in (0, 1]')
        return value

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


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    inputs: RunInputs
    settings: AnalysisSettings = Field(default_factory=AnalysisSettings)
    label: Optional[str] = None


class LoginRequest(BaseModel):
    """Credentials from the sign-in form.

    ``username`` is unused in shared-code mode, where there is one secret and
    no accounts, so it is optional rather than two separate request shapes.
    """
    model_config = ConfigDict(extra='forbid')

    username: str = ''
    password: str = ''


class FileOut(BaseModel):
    id: str
    kind: str
    name: str
    size: int
    uploaded_at: float
    fmt: Optional[str] = None
    detail: dict = Field(default_factory=dict)


class JobStatus(BaseModel):
    run_id: str
    state: Literal['queued', 'running', 'complete', 'failed', 'cancelled']
    stage: str = ''
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    label: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
