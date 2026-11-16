/* Display names for the per-feature fields in `iondict`.
 *
 * The pipeline's column names are internal (`kmd`, `pass_relfil`, `logmax`),
 * so both places that show a feature's fields -- the details rail beside the
 * plots and the Feature Info detail pane -- render them through this table.
 * The order here is the order they are shown in: identity first, then
 * statistics, then which filters the feature passed.
 */

export const FEATURE_FIELDS = [
  ['m/z', 'm/z', 4],
  ['Retention time (min)', 'Retention time (min)', 3],
  ['kmd', 'Mass defect', 4],
  ['max', 'Max abundance', 0],
  ['logmax', 'log10 max abundance', 3],
  ['fc', 'Fold change', 3],
  ['logfc', 'log2 fold change', 3],
  ['-logp', '-log10 p', 3],
  ['-logq', '-log10 q (FDR)', 3],
  ['median CV', 'Median CV', 3],
  ['average CV', 'Mean CV', 3],
  ['hits', 'Database hits', 0],
  ['adduct_form', 'Ion form (beta adduct filter)', null],
  ['groups', 'Detected in', null],
];

// Boolean pass/fail columns, shown together as a filter summary.
export const FILTER_FIELDS = [
  ['pass_mdfil', 'Mass defect filter (beta)'],
  ['pass_relfil', 'Mispicked-peak filter'],
  ['pass_blnkfil', 'Blank filter'],
  ['pass_cvfil', 'CV filter'],
  ['pass_insource', 'In-source filter'],
  ['pass_adduct', 'Adduct filter (beta)'],
];

/** True/false rendered as something readable at a glance. */
export const passLabel = (value) => {
  if (value === null || value === undefined || value === '') return '--';
  const truthy = value === true || value === 'true' || value === 'True' || value === 1;
  return truthy ? 'passed' : 'filtered out';
};
