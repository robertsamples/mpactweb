/* Taxonomic level + name with suggestions from the atlas. Shared by the
 * Analysis Settings database search and the Feature Info hit filter. */

import { clear, debounce, el } from '../util.js';

export const TAXON_LEVELS = [['kingdom', 'Kingdom'], ['phylum', 'Phylum'], ['class', 'Class'],
                             ['order', 'Order'], ['family', 'Family'], ['genus', 'Genus'],
                             ['species', 'Species']];

/** `suggest(level, text)` resolves to [{name, count}]; `onChange(level, value)`
 *  fires when a name is committed, or the level changes under a name. */
export function taxonPicker({ level = 'genus', value = '', suggest, onChange,
                              placeholder = 'any' }) {
  let current = { level, value: String(value || '').trim() };
  const input = el('input', { type: 'text', class: 'taxon-input', placeholder,
                              value: current.value, autocomplete: 'off', spellcheck: false });
  const menu = el('div', { class: 'cat-menu suggest-menu hidden' });
  const box = el('div', { class: 'suggest' }, [input, menu]);
  let items = [];
  let active = -1;
  let request = 0;

  const levelSelect = el('select', {
    on: { change: (event) => {
      current = { ...current, level: event.target.value };
      if (current.value) onChange(current.level, current.value);
      input.focus();
    } },
  }, TAXON_LEVELS.map(([key, text]) =>
    el('option', { value: key, text, selected: key === current.level })));

  const commit = (text) => {
    const next = String(text || '').trim();
    input.value = next;
    menu.classList.add('hidden');
    box.classList.toggle('active', Boolean(next));
    if (next === current.value) return;
    current = { ...current, value: next };
    onChange(current.level, current.value);
  };

  const highlight = (index) => {
    active = index;
    [...menu.children].forEach((child, i) => child.classList.toggle('active', i === index));
    menu.children[index]?.scrollIntoView({ block: 'nearest' });
  };

  async function refresh() {
    if (document.activeElement !== input) return;
    const mine = ++request;
    let names = [];
    try { names = await suggest(current.level, input.value); } catch { names = []; }
    if (mine !== request || document.activeElement !== input) return;
    items = names;
    clear(menu);
    for (const [index, item] of names.entries()) {
      menu.append(el('div', {
        class: 'cat-option suggest-option',
        on: { mousedown: (event) => { event.preventDefault(); commit(item.name); },
              mouseenter: () => highlight(index) },
      }, [el('span', { text: item.name }),
          el('span', { class: 'muted', text: item.count.toLocaleString() })]));
    }
    active = -1;
    menu.classList.toggle('hidden', !names.length);
  }
  const refreshSoon = debounce(refresh, 140);

  input.addEventListener('input', () => {
    if (!input.value.trim() && current.value) commit('');
    refreshSoon();
  });
  input.addEventListener('focus', () => refresh());
  input.addEventListener('blur', () => {
    menu.classList.add('hidden');
    if (input.value.trim() !== current.value) commit(input.value);
  });
  input.addEventListener('keydown', (event) => {
    const open = !menu.classList.contains('hidden') && items.length > 0;
    if (event.key === 'ArrowDown' && open) {
      event.preventDefault(); highlight(Math.min(items.length - 1, active + 1));
    } else if (event.key === 'ArrowUp' && open) {
      event.preventDefault(); highlight(Math.max(0, active - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      commit(open && active >= 0 ? items[active].name : input.value);
    } else if (event.key === 'Escape') {
      menu.classList.add('hidden');
    }
  });

  box.classList.toggle('active', Boolean(current.value));
  const node = el('div', { class: 'taxon-picker' }, [levelSelect, box]);
  return { node };
}
