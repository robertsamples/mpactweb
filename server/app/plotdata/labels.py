"""Shared leaf/column labelling and replicate-collapsing helpers.

The dendrogram, heatmap and sample-correlation tabs all offer the same
"Use Sample/Group Names" toggle, producing ``<Biolgroup>_b<BioRep>_s<TechRep>``
style short labels instead of raw injection filenames. The desktop implements
that separately in each plot class; the rule is identical in all three, so it
lives once here -- built on ``ordination.replicate_label_components``, which
is vendored unmodified and is where the BioRep/TechRep numbering actually
comes from.
"""

from ..core import ordination

# View names, matching the desktop's combo boxes verbatim.
TECHNICAL = 'Technical Replicates'
BIOLOGICAL = 'Biological Replicates'
INJECTIONS = 'Individual Injections'
GROUPS = 'Biological Groups'


def display_labels(raw_header, names, view):
    """Short display labels for ``names`` under ``view``.

    ``names`` are Injection names for the per-injection views, Sample names
    when technical replicates are collapsed, and Biolgroup names when both
    are collapsed (nothing left to shorten, so they pass through).
    """
    components = ordination.replicate_label_components(raw_header)
    if view == GROUPS:
        return list(names)
    if view in (BIOLOGICAL,):
        per_sample = components.drop_duplicates('Sample').set_index('Sample')
        out = []
        for name in names:
            if name in per_sample.index:
                row = per_sample.loc[name]
                out.append(f"{row['Biolgroup']}_b{row['BioRep']}")
            else:
                out.append(str(name))
        return out
    out = []
    for name in names:
        if name in components.index:
            row = components.loc[name]
            out.append(f"{row['Biolgroup']}_b{row['BioRep']}_s{row['TechRep']}")
        else:
            out.append(str(name))
    return out


def group_of(raw_header, names, view):
    """The Biolgroup each label belongs to, for legend colouring."""
    header = raw_header
    if view == GROUPS:
        return {str(name): str(name) for name in names}
    if view == BIOLOGICAL:
        mapping = header.drop_duplicates('Sample').set_index('Sample')['Biolgroup'].to_dict()
    else:
        mapping = header.set_index('Injection')['Biolgroup'].to_dict()
    return {str(name): str(mapping.get(name, '')) for name in names}
