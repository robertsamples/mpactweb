"""Plot-data builders: the web replacement for the desktop's ``plotting.py``.

Every entry here reads a completed run's output files and returns plain JSON
for the browser to draw. None of them render anything server-side, and none
of them re-implement analysis: where a computation exists in the vendored
core (``ordination``, ``pvclust``, ``clusterpurity``, ``qualityscore``,
``treefilters``), it is called rather than reproduced.

``BUILDERS`` maps the URL name to the builder and records which
``analysis_parameters`` flag has to be on for that tab to exist -- the same
gating the desktop applies when deciding which plot widgets to create.
"""

from . import heatmap as _heatmap
from . import dendrogram as _dendrogram
from . import multivariate as _multivariate
from . import quality as _quality
from . import scatter as _scatter
from . import volcano as _volcano


class PlotSpec:
    """One plot tab: its builder, its display name, and its gating flag."""

    __slots__ = ('name', 'title', 'builder', 'requires')

    def __init__(self, name, title, builder, requires=()):
        self.name = name
        self.title = title
        self.builder = builder
        self.requires = tuple(requires)

    def available(self, params):
        """True if this tab's prerequisites were enabled for the run."""
        return all(bool(params.get(flag)) for flag in self.requires)


BUILDERS = {spec.name: spec for spec in (
    # --- Data Review -----------------------------------------------------
    PlotSpec('summary', 'Summary', _quality.summary),
    PlotSpec('quality', 'Feature plot', _scatter.quality),
    PlotSpec('cvplot', 'CV plot', _quality.cvplot, requires=('CVfil',)),
    PlotSpec('treemap', 'Tree map', _quality.treemap),
    # --- Group Analysis --------------------------------------------------
    PlotSpec('upset', 'Sets', _quality.upset),
    PlotSpec('samplecorr', 'Sample Correlations', _multivariate.samplecorr),
    # --- Clustering / multivariate ---------------------------------------
    PlotSpec('dendrogram', 'Dendrogram', _dendrogram.dendrogram, requires=('Dendrogram',)),
    PlotSpec('ordination', 'Multivariate', _multivariate.ordination_plot, requires=('PCA',)),
    PlotSpec('heatmap', 'Heatmap', _heatmap.heatmap),
    # --- Feature-space views ---------------------------------------------
    PlotSpec('mzrt', 'm/z vs RT', _scatter.mzrt, requires=('MZRTplt',)),
    PlotSpec('massdefect', 'Mass Defect', _scatter.massdefect, requires=('KMD',)),
    PlotSpec('fc3d', '3D Fold Change', _scatter.fc3d, requires=('FC3Dplt', 'FC')),
    PlotSpec('volcano', 'Volcano', _volcano.volcano, requires=('Volcanoplt', 'FC', 'Ttest')),
    # --- Tables ----------------------------------------------------------
    PlotSpec('groupaverages', 'Group averages', _quality.group_averages),
)}


def build(name, results, options=None):
    spec = BUILDERS.get(name)
    if spec is None:
        raise KeyError(name)
    return spec.builder(results, options or {})


def catalogue(params):
    """Which plot tabs this run offers, in the order the UI shows them."""
    return [
        {'name': spec.name, 'title': spec.title, 'available': spec.available(params),
         'requires': list(spec.requires)}
        for spec in BUILDERS.values()
    ]
