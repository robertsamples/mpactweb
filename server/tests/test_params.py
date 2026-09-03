"""Unit tests for the parameter mapping and the vendored-core boundary.

``params.build_params`` is the web equivalent of the desktop's
``MainWindow.enumerate_inputs``. If it drifts, the pipeline runs with
different settings than the form showed -- silently, since every value is a
plausible number. These tests pin the derived values that are easy to get
wrong: the implicit default feature set, the graph-filter token list, the
output directory shape, and the fields the desktop derives rather than
reads.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import params as params_builder          # noqa: E402
from app.schemas import AnalysisSettings, GroupSetIn   # noqa: E402


class FakeRun:
    """Just the bit of ``storage.Run`` that ``build_params`` touches."""

    def __init__(self, tmp_path):
        self.output_root = tmp_path / 'output'
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path


@pytest.fixture
def inputs(tmp_path):
    raw = tmp_path / 'rawdata'
    raw.mkdir()
    for name in ('peaks.csv', 'samples.csv', 'metadata.csv'):
        (raw / name).write_text('placeholder')
    return {
        'peaktable': raw / 'peaks.csv',
        'samplelist': raw / 'samples.csv',
        'metadata': raw / 'metadata.csv',
    }


GROUPS = ['Blanks', 'Control', 'Treated']


def build(tmp_path, inputs, **overrides):
    settings = AnalysisSettings(**overrides)
    return params_builder.build_params(FakeRun(tmp_path), settings, GROUPS, inputs)


# --- implicit default feature sets ---------------------------------------

def test_default_groupset_with_blank_filtering(tmp_path, inputs):
    """Blank filtering on: the desktop creates 'Features not in blanks',
    which may be in every non-blank group and must not be in the blank."""
    _, model = build(tmp_path, inputs, blnkfltr=True, blnkgrp='Blanks',
                     expgrp='Control', ctrgrp='Treated')
    assert len(model) == 1
    groupset = model[0]
    assert groupset.name == 'Features not in blanks'
    assert sorted(groupset.src) == ['Control', 'Treated']
    assert groupset.excl == ['Blanks']
    assert groupset.incl == []


def test_default_groupset_without_blank_filtering(tmp_path, inputs):
    """Blank filtering off: 'All features' may be in anything, excludes nothing."""
    _, model = build(tmp_path, inputs, blnkfltr=False, expgrp='Control', ctrgrp='Treated')
    assert len(model) == 1
    groupset = model[0]
    assert groupset.name == 'All features'
    assert sorted(groupset.src) == sorted(GROUPS)
    assert groupset.excl == []


def test_explicit_groupsets_are_preserved(tmp_path, inputs):
    supplied = [GroupSetIn(name='Treated only', src=['Treated'], incl=['Treated'],
                           excl=['Blanks', 'Control'], colour='#ff8800')]
    params, model = build(tmp_path, inputs, groupsets=supplied, blnkfltr=False,
                          expgrp='Control', ctrgrp='Treated')
    assert len(model) == 1
    assert model[0].name == 'Treated only'
    assert model[0].colour == '#ff8800'
    assert len(params.querylist) == 1


# --- graph filters --------------------------------------------------------

def test_graphfilters_token_order(tmp_path, inputs):
    """The desktop appends cv, then rel, then insource -- and these tokens
    are merged into every feature set's exclusion list, so their order and
    presence change which features are plotted."""
    params, _ = build(tmp_path, inputs, CVfil=True, relfil=True, decon=True,
                      blnkfltr=False, expgrp='Control', ctrgrp='Treated')
    assert params.graphfilters == ['cv', 'rel', 'insource']


def test_graphfilters_reflect_disabled_filters(tmp_path, inputs):
    params, _ = build(tmp_path, inputs, CVfil=False, relfil=True, decon=False,
                      blnkfltr=False, expgrp='Control', ctrgrp='Treated')
    assert params.graphfilters == ['rel']
    # deconthresh is only set when in-source filtering is on, matching the
    # desktop -- the pipeline never reads it otherwise.
    assert not hasattr(params, 'deconthresh')


def test_graphfilters_are_merged_into_groupset_exclusions(tmp_path, inputs):
    params, _ = build(tmp_path, inputs, CVfil=True, relfil=False, decon=False,
                      blnkfltr=True, blnkgrp='Blanks', expgrp='Control', ctrgrp='Treated')
    key = params.querylist[0]
    assert 'cv' in params.querydict[key].excl
    assert 'Blanks' in params.querydict[key].excl


# --- derived values -------------------------------------------------------

def test_output_directory_is_named_after_the_peak_table(tmp_path, inputs):
    """Every output path the pipeline writes -- and every path the results
    layer later looks for -- hangs off this directory."""
    params, _ = build(tmp_path, inputs, blnkfltr=False, expgrp='Control', ctrgrp='Treated')
    assert params.outputdir.name == 'peaks'
    assert params.outputdir.parent.name == 'output'
    assert params.outputdir.is_dir()
    assert (params.outputdir / 'plots').is_dir()


def test_isotope_window_drives_the_dimer_window(tmp_path, inputs):
    """One control feeds both fields on the desktop; a mismatch would change
    the mispicked-peak filter's behaviour."""
    params, _ = build(tmp_path, inputs, isopeakwin=0.02, blnkfltr=False,
                      expgrp='Control', ctrgrp='Treated')
    assert params.isopeakwin == 0.02
    assert params.dimerpeakwin == 0.02


def test_group_average_and_error_propagation_are_forced_on(tmp_path, inputs):
    """The desktop has no widget for either; the group-level plots need both."""
    params, _ = build(tmp_path, inputs, blnkfltr=False, expgrp='Control', ctrgrp='Treated')
    assert params.grpave is True
    assert params.prperr is True


def test_blank_group_defaults_when_filtering_is_on_without_a_group(tmp_path, inputs):
    """Matches the desktop's final fallback in enumerate_inputs."""
    params, _ = build(tmp_path, inputs, blnkfltr=True, blnkgrp='',
                      expgrp='Control', ctrgrp='Treated')
    assert params.blnkgrp == 'Blanks'


def test_blank_group_cleared_when_filtering_is_off(tmp_path, inputs):
    params, _ = build(tmp_path, inputs, blnkfltr=False, blnkgrp='Blanks',
                      expgrp='Control', ctrgrp='Treated')
    assert params.blnkgrp == ''


def test_stats_groups_order(tmp_path, inputs):
    """statstgrps is [experimental, control] -- reversing it flips the sign of
    every fold change."""
    params, _ = build(tmp_path, inputs, blnkfltr=False, expgrp='Treated', ctrgrp='Control')
    assert params.statstgrps == ['Treated', 'Control']


def test_every_pipeline_attribute_is_present(tmp_path, inputs):
    """The pipeline reads these off ``analysis_parameters`` directly; a
    missing one is an AttributeError deep inside a run, not a clear error."""
    params, _ = build(tmp_path, inputs, blnkfltr=True, blnkgrp='Blanks',
                      expgrp='Control', ctrgrp='Treated')
    required = [
        'filename', 'samplelistfilename', 'extractmetadatafilename', 'outputdir',
        'fragfilename', 'querylist', 'querydict', 'graphfilters',
        'relfil', 'merge', 'CVfil', 'decon', 'blnkfltr', 'blnkgrp',
        'blankfilparam', 'blankfilthresh', 'cvthresh', 'cvparam',
        'RTwin', 'ringingwin', 'isopeakwin', 'dimerpeakwin', 'maxisowin',
        'grpave', 'prperr', 'statstgrps', 'ppmthresh',
        'FC', 'Ttest', 'FDR', 'PCA', 'Dendrogram', 'MZRTplt', 'KMD',
        'FC3Dplt', 'Volcanoplt',
    ]
    missing = [name for name in required if not hasattr(params, name)]
    assert not missing, f'analysis_parameters is missing: {missing}'


def test_params_snapshot_is_json_safe(tmp_path, inputs):
    """The snapshot is written to params.json and read back by the results
    layer, so Paths must already be strings."""
    import json
    params, model = build(tmp_path, inputs, blnkfltr=False,
                          expgrp='Control', ctrgrp='Treated')
    snapshot = params_builder.params_to_json(params, model)
    json.dumps(snapshot)   # raises if anything is unserialisable
    assert isinstance(snapshot['outputdir'], str)
    assert snapshot['groupsets'][0]['name'] == 'All features'


# --- schema validation ----------------------------------------------------

@pytest.mark.parametrize('field,value', [
    ('cvthresh', 0), ('ringingwin', -1), ('ppmthresh', 0),
    ('deconthresh', 0), ('deconthresh', 1.5), ('maxisowin', 9),
])
def test_out_of_range_settings_are_rejected(field, value):
    with pytest.raises(Exception):
        AnalysisSettings(**{field: value})


def test_colour_is_normalised():
    assert GroupSetIn(colour='FF8800').colour == '#ff8800'
    with pytest.raises(Exception):
        GroupSetIn(colour='not-a-colour')


def test_unknown_settings_are_rejected():
    """extra='forbid' catches a renamed field instead of silently ignoring it
    and running with the default."""
    with pytest.raises(Exception):
        AnalysisSettings(cvthreshold=0.3)
