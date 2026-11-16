/* Citation and credits.
 *
 * Shown under the sign-in card. The landing page (deploy/landing/index.html)
 * is a separate static file with the same text; keep the two in sync.
 */

import { el } from './util.js';

const DOI = 'https://doi.org/10.1021/acs.analchem.2c04632';

/** The MPACT paper citation, with a DOI link. */
export function citation() {
  return el('span', { class: 'citation' }, [
    'Samples, R. M.; Puckett, S. P.; Balunas, M. J. Metabolomics Peak Analysis '
    + 'Computational Tool (MPACT): An Advanced Informatics Tool for Metabolomics and Data '
    + 'Visualization of Molecules from Complex Biological Samples. ',
    el('em', { text: 'Anal. Chem.' }), ' ', el('strong', { text: '2023' }), ', ',
    el('em', { text: '95' }), ' (23), 8770–8779. ',
    el('a', { href: DOI, target: '_blank', rel: 'noopener', text: 'doi:10.1021/acs.analchem.2c04632' }),
  ]);
}

const person = (name, affiliation) => el('p', { class: 'credit-person' }, [
  el('strong', { text: name }),
  affiliation ? el('span', { class: 'muted', text: `, ${affiliation}` }) : null,
]);

let dialog = null;

/** Open the credits dialog. */
export function openCredits() {
  if (!dialog) {
    dialog = el('dialog', { class: 'credits' }, [
      el('div', { class: 'credits-head' }, [
        el('h3', { text: 'Credits' }),
        el('button', { class: 'btn small', type: 'button', text: 'Close',
                       on: { click: () => dialog.close() } }),
      ]),

      el('h4', { text: 'MPACT Web' }),
      person('Robert M. Samples', 'Center for Mass Spectrometry, Smith College'),
      el('p', { text: 'Lead developer. Designed and wrote MPACT Web, and maintains the MPACT '
                      + 'analysis pipeline.' }),
      person('Yilu Chen', 'Department of Food Science, University of Massachusetts Amherst'),
      el('p', { text: 'Developed the mass defect filter, the adduct and neutral-loss grouping '
                      + 'filter, and the revised isotope filter.' }),

      el('h4', { text: 'Original MPACT' }),
      el('p', { class: 'credit-line', text: 'Robert M. Samples - Algorithms, UI design, and benchmarking.' }),
      el('p', { class: 'credit-line', text: 'Sara P. Puckett - UI design, testing, and benchmarking.' }),
      el('p', { text: 'Marcy J. Balunas (University of Michigan) - Project supervision.' }),
      el('p', { class: 'muted',
                text: 'The original work was supported by National Science Foundation grant '
                      + 'IOS-1557914 and the Gordon and Betty Moore Foundation.' }),

      el('h4', { text: 'Citation' }),
      el('p', {}, [citation()]),

      el('p', { class: 'muted credits-foot' }, [
        'Free and open source under GPLv3. Source Code: ',
        el('a', { href: 'https://github.com/robertsamples/mpact', target: '_blank', rel: 'noopener',
                  text: 'Core' }),
        ' ',
        el('a', { href: 'https://github.com/robertsamples/mpactweb', target: '_blank', rel: 'noopener',
                  text: 'Web' }),
      ]),
    ]);
    // Clicking the backdrop closes the dialog.
    dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
    document.body.append(dialog);
  }
  dialog.showModal();
}

/** Citation and Credits link for the sign-in screen. */
export function citeFooter() {
  return el('div', { class: 'login-cite' }, [
    el('p', {}, [el('span', { class: 'cite-lead', text: 'If you use MPACT, please cite:' }), el('br'),
                 citation()]),
    el('button', { class: 'link-button', type: 'button', text: 'Credits',
                   on: { click: openCredits } }),
  ]);
}
