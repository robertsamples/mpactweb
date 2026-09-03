/* Small DOM/formatting helpers. Deliberately not a framework: the UI is a
 * fixed set of panels mirroring the desktop's tabs, so a build step and a
 * virtual DOM would cost more than they save here. */

/** Create an element. `attrs` may include `class`, `text`, `html`, `dataset`,
 *  `on` (event map) and any other attribute or property. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key === 'on') {
      for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
    } else if (key in node && key !== 'list') node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/** Significant-figure formatting for mixed-magnitude scientific values:
 *  abundances run to 1e9 while retention times are ~1. */
export function num(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const abs = Math.abs(value);
  if (abs === 0) return '0';
  if (abs >= 1e6 || abs < 1e-4) return value.toExponential(2);
  if (Number.isInteger(value) && digits === 0) return value.toLocaleString();
  return Number(value.toFixed(digits)).toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function bytes(size) {
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
  return `${(size / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function timeAgo(seconds) {
  if (!seconds) return '';
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

export function duration(from, to) {
  if (!from) return '';
  const seconds = Math.max(0, (to || Date.now() / 1000) - from);
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** #rrggbb + alpha -> rgba(), for the abundance-tied point opacity the
 *  desktop bakes into per-point RGBA. */
export function rgba(hex, alpha = 1) {
  const value = String(hex || '#000000').replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

let toastHost = null;
export function toast(message, kind = 'info', timeout = 5200) {
  toastHost = toastHost || document.getElementById('toasts');
  if (!toastHost) return;
  const node = el('div', { class: `toast ${kind}`, text: message });
  toastHost.append(node);
  setTimeout(() => {
    node.style.transition = 'opacity .3s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 320);
  }, timeout);
}

/** Trailing-edge debounce, for threshold boxes and filter inputs that would
 *  otherwise fire a request per keystroke. */
export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function field(label, control, hint) {
  return el('div', { class: 'field' }, [
    el('label', { text: label }),
    control,
    hint ? el('div', { class: 'suffix', text: hint }) : null,
  ]);
}

export function checkbox(label, checked, onChange) {
  const input = el('input', { type: 'checkbox', checked, on: { change: (e) => onChange(e.target.checked) } });
  return el('label', { class: 'check' }, [input, el('span', { text: label })]);
}

export function numberInput(value, onChange, attrs = {}) {
  return el('input', {
    type: 'number', value, step: 'any', ...attrs,
    on: {
      change: (e) => {
        const parsed = parseFloat(e.target.value);
        if (Number.isNaN(parsed)) { e.target.value = value; return; }
        onChange(parsed);
      },
    },
  });
}

export function select(options, value, onChange, attrs = {}) {
  const node = el('select', { ...attrs, on: { change: (e) => onChange(e.target.value) } });
  for (const option of options) {
    const [optionValue, optionLabel] = Array.isArray(option) ? option : [option, option];
    node.append(el('option', { value: optionValue, text: optionLabel,
                               selected: String(optionValue) === String(value) }));
  }
  node.value = value;
  return node;
}
