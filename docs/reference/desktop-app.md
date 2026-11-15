# Coming from the desktop app

The web version runs the same analysis pipeline as the MPACT desktop
application. The pipeline code is copied unchanged and verified by checksum, so
the same inputs and settings give the same filtered data and statistics. What
changed is everything around it.

The desktop app is no longer developed. Its last release stays available; see
[Installation](../getting-started/installation.md#standalone-desktop-app).

## What stays the same

- Inputs: Progenesis QI, MZmine, MS-DIAL and Metaboscape peak tables, sample
  list, metadata and an optional MSP/MGF fragment database.
- The four filters and their parameters.
- Plot Feature Sets, with the same Must be in / May be in / Available groups
  rules.
- Every output file, including the `.mpct` session file.
- `.mpct` files move both ways: a session file from the desktop opens with
  **Open session file...**, and one downloaded from the web opens in the
  desktop app.

## What is different

| Desktop | Web |
|---|---|
| Install Anaconda and run `run.bat` or Spyder | Open a browser; or [install a server](../self-hosting/index.md) |
| Output written to a folder you choose | Files kept on the server; download from [Downloads](../results/downloads.md) |
| Reload an analysis from a `.mpct` file | Every analysis stays in [Runs](../setup/running.md#previous-runs) |
| Hand-written sample list and metadata | Optional [sheet editor](../setup/file-selection.md#creating-the-sample-list-and-metadata) builds both from injection names |
| Group presence threshold on Analysis Settings | Moved to [Filtering Settings](../setup/filtering.md#blank-filtering-and-group-parsing), beside blank filtering |
| Toggles for fold change, t-test and FDR | Always computed |
| Compound details in a separate window | [Details panel](../getting-started/interface.md#the-details-panel) beside every plot |
| One highlighted feature | Multiple selection, shared by the table and every plot |
| Per-plot options dialogs | Controls above each plot |
| NPAtlas hits | NPAtlas plus MS-DIAL MS/MS library search, and filters by taxon, substructure and compound class |
| Fixed axis ranges on m/z vs RT | Ranges fit the data |
| "Collapse Replicates" checkbox on the ordination | **Points:** Individual Injections or Biological Replicates |
| | [Flags](../results/index.md#flags) to mark features, saved with the run |

## Plot names

| Desktop tab | Web tab |
|---|---|
| Data Review: Summary, CV Plot, Feature Plot, Treemap | [Data Review](../results/data-review.md) |
| Group Analysis: UpSet Plot, Sample Correlation Matrix | [Group Analysis](../results/group-analysis.md): Sets, Sample Correlations |
| Group Analysis: Dendrogram | [Dendrogram](../results/dendrogram.md) (its own tab) |
| Multivariate Analysis | [Multivariate](../results/multivariate.md) |
| m/z vs RT, Mass Defect, 3D, Volcano, Heatmap | Same names |
| Feature Info pane, Hits tab, Analysis Info | [Feature Info](../results/feature-info.md); analysis info is on [Data Review](../results/data-review.md#summary) |
