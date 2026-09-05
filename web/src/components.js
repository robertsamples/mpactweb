/* Small shared UI pieces used by more than one view. */

import { el } from './util.js';

/**
 * A disclosure section: a clickable header that shows/hides its body.
 *
 * `open` sets the initial state; `onToggle` is called with the new state, so
 * a caller can remember it (the details rail persists which of its sections
 * you left open) or defer expensive work until the body is first shown.
 */
export function collapsible({ title, hint, open = false, body, onToggle, className = '' }) {
  const twisty = el('span', { class: 'twisty', text: '▶' });
  const bodyNode = el('div', { class: 'collapsible-body' }, [body].filter(Boolean));
  const node = el('div', { class: `collapsible ${className} ${open ? 'open' : ''}`.trim() });

  const head = el('button', {
    class: 'collapsible-head', type: 'button',
    on: {
      click: () => {
        const nowOpen = !node.classList.contains('open');
        node.classList.toggle('open', nowOpen);
        head.setAttribute('aria-expanded', String(nowOpen));
        if (onToggle) onToggle(nowOpen);
      },
    },
  }, [twisty, el('span', { text: title }), hint ? el('span', { class: 'hint', text: hint }) : null]);
  head.setAttribute('aria-expanded', String(open));

  node.append(head, bodyNode);
  node.setBody = (content) => {
    bodyNode.replaceChildren(...[].concat(content).filter(Boolean));
  };
  node.isOpen = () => node.classList.contains('open');
  node.setOpen = (value) => {
    node.classList.toggle('open', Boolean(value));
    head.setAttribute('aria-expanded', String(Boolean(value)));
  };
  return node;
}

/**
 * Make `handle` drag-resize the panel *above* it: dragging down grows the
 * panel, dragging up shrinks it.
 *
 * `makeSplitter` sizes the element after the divider, which is what a
 * side-by-side split wants. Stacked panels want the opposite -- the boundary
 * between two of them belongs to the one on top -- so this is its own small
 * function rather than another flag on that one.
 */
export function makeRowResizer(handle, panel, { min = 110, max = 640,
                                                onResize, onCommit } = {}) {
  let dragging = false;

  const sizeFor = (event) =>
    Math.round(Math.min(max, Math.max(min, event.clientY - panel.getBoundingClientRect().top)));

  const move = (event) => {
    if (!dragging) return;
    event.preventDefault();
    const size = sizeFor(event);
    panel.style.height = `${size}px`;
    if (onResize) onResize(size);
  };

  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', stop);
    if (onCommit) onCommit(sizeFor(event));
  };

  handle.addEventListener('mousedown', (event) => {
    if (event.button !== 0) return;
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', stop);
    event.preventDefault();
  });

  handle.addEventListener('dblclick', () => {
    panel.style.height = '';
    if (onResize) onResize(null);
    if (onCommit) onCommit(null);
  });
}

/**
 * Make `splitter` drag-resize `panel`.
 *
 * `panel` is the element *after* the splitter -- the right-hand one for a
 * horizontal split, the lower one for a vertical split -- so dragging
 * towards it shrinks it. The size is reported through `onResize` during the
 * drag (to re-lay-out whatever just gained or lost the space) and through
 * `onCommit` when it ends, so a preference can be stored without writing on
 * every mousemove.
 */
export function makeSplitter(splitter, panel,
                             { min = 200, max = 720, axis = 'horizontal',
                               onResize, onCommit } = {}) {
  let dragging = false;
  const vertical = axis === 'vertical';

  const sizeFor = (event) => {
    const box = panel.parentElement.getBoundingClientRect();
    const raw = vertical ? box.bottom - event.clientY : box.right - event.clientX;
    return Math.round(Math.min(max, Math.max(min, raw)));
  };

  const apply = (size) => {
    panel.style.flexBasis = `${size}px`;
    // Only the cross-axis dimension is pinned; the other stays fluid so the
    // panel still fills its row/column.
    if (vertical) panel.style.height = `${size}px`;
    else panel.style.width = `${size}px`;
  };

  const move = (event) => {
    if (!dragging) return;
    event.preventDefault();
    const size = sizeFor(event);
    apply(size);
    if (onResize) onResize(size);
  };

  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', stop);
    if (onCommit) onCommit(sizeFor(event));
  };

  splitter.addEventListener('mousedown', (event) => {
    // Only a primary-button drag; a right-click here should do nothing.
    if (event.button !== 0) return;
    dragging = true;
    splitter.classList.add('dragging');
    // Suppress text selection for the duration, or the drag highlights the
    // whole page.
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', stop);
    event.preventDefault();
  });

  // Double-click resets to the stylesheet default, the usual affordance.
  splitter.addEventListener('dblclick', () => {
    panel.style.flexBasis = '';
    panel.style.width = '';
    panel.style.height = '';
    if (onResize) onResize(null);
    if (onCommit) onCommit(null);
  });
}
