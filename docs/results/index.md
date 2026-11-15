# Working with results

Every result tab shares the same selection, the same details panel and the same
plot toolbar. This page covers what they have in common; the pages that follow
describe each tab.

## Selecting features

A selected feature is selected everywhere. Pick a point on the volcano plot and
it is highlighted in yellow on the m/z vs RT plot, the heatmap and the Feature
Info table, and its details fill the panel on the right.

| To | Do this |
|---|---|
| Select one feature | Click a point, heatmap cell or table row |
| Add or remove a feature | ++ctrl++ + click |
| Select a region of a plot | Box or lasso tool in the plot toolbar; hold ++shift++ to add |
| Select a range of table rows | ++shift++ + click, or ++shift++ + arrow keys |
| Select every listed row | ++ctrl+a++ in the Feature Info table |
| Step through the table | ++up++ and ++down++ |
| Clear the selection | **Clear selection** in the status bar |

With more than one feature selected, the top of the details panel lists them.
Click a name to show that feature's details, **×** to drop it, or export the
whole selection as a CSV. Selecting a set of features in the table and opening
the volcano plot is a quick way to make a figure that marks them.

Plots of samples rather than features (the ordination and the sample
correlation matrix) select an injection instead. The status bar then shows the
injection and its biological group.

## Flags

A flag is a coloured mark you put on a feature to keep track of it, such as
"confirmed", "check MS2" or "candidate". Flags are saved with the run, so they
are still there next time you open it.

- In Feature Info, select features, pick a colour and click **Flag** (or
  **Unflag**). Clicking a row's cell in the **Flag** column flags just that
  row.
- In the details panel, click the circle next to the feature's name.

With **Flags in plots** turned on in Feature Info, flagged features are marked
in their flag colour on every feature plot, the same way the selection is
marked in yellow.
Flags are also a column in the exported CSV.

## Table rows only

Most feature plots have a **Table rows only** checkbox (in Feature Info it is
called **Plot table rows only**). When it is on, plots draw only the features
currently listed in the Feature Info table. Use the table's filters to narrow
the list, for example to features with an NPAtlas hit from one genus, and the
plots follow.

## Saving plots

Choose a format at the right of the plot toolbar (SVG, PNG, JPEG or WebP) and
click the save button. The image is what you see, including your current zoom.
SVG is best for figures.
