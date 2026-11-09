/* The dinosaur behind the Run terminal.
 *
 * Frames come from one sprite sheet (web/assets/dino/<theme>.png, built by
 * scripts/build_dino.py) and are drawn onto a tiny canvas scaled up with
 * nearest-neighbour, so a frame costs one drawImage. The clock only ticks at
 * the clip's own rate (12-15 fps) and stops while the Run view is hidden.
 *
 * What it does follows the run in the store:
 *   no run / finished     idle loop
 *   queued or running     idle -> (neutral) -> start -> run loop
 *   finished while running  run until the stop frame -> stop -> idle
 *   failed while running    run until the crash frame -> crash, hold the last frame
 *   new run after a crash   getup -> run loop
 *
 * Holidays swap the sheet, chosen by the viewer's local date unless they pick
 * one from the menu on the MPACT icon. Only the one sheet in use is fetched.
 * Every theme has the same frames in the same order, so a swap mid-clip just
 * carries on in the new costume.
 */

import { store } from '../state.js';

const ASSETS = '/assets/dino';
const CHOICE_KEY = 'mpact.dinoTheme';   // 'auto' (by date), 'standard' or a holiday id
const HEIGHT_SHARE = 0.46;   // of the terminal's height, before rounding
const WIDTH_SHARE = 0.6;     // never much wider than this share of it

let meta = null;
let sheet = null;
let canvas = null;
let ctx = null;
let host = null;
let resizer = null;
let theme = null;            // theme whose sheet is loaded (or loading)

let clip = 'idle';           // current clip name, or 'down' (crash held)
let frames = [];             // sheet indices of the clip being played
let neutralFrom = null;      // idle frame the current back-to-neutral clip left from
let pos = 0;                 // index into frames
let after = null;            // what follows a one-shot clip
let timer = null;
let hiddenSince = 0;

let pressed = false;         // Run clicked, server not answered yet
let demo = null;             // 'run' then 'fail': a pretend run that crashes
let demoLeft = 0;            // run frames still to show before it does

/** Start moving on the click itself rather than on the server's reply. */
export function dinoPressed(on) {
  pressed = on;
  if (on) demo = null;
}

/** One run cycle, then a crash that holds until the next real run. */
export function dinoCrash() {
  const run = store.get('run');
  if (!meta || !visible() || pressed
      || (run && (run.state === 'queued' || run.state === 'running'))) return;
  demo = 'run';
  demoLeft = meta.clips.run.frames.length;
  wake();
}

function target() {
  if (pressed) return 'run';
  const run = store.get('run');
  if (run && (run.state === 'queued' || run.state === 'running')) {
    demo = null;
    return 'run';
  }
  if (demo) return demo;
  if (!run) return 'idle';
  if (run.state === 'failed') return 'fail';
  return 'idle';
}

function framesFor(name, from) {
  if (name === 'neutral') return meta.clips.idle_to_neutral.from[from];
  if (name === 'down') return meta.clips.crash.frames.slice(-1);
  return meta.clips[name].frames;
}

function play(name, next = null, start = 0, from = null) {
  clip = name;
  neutralFrom = from;
  frames = framesFor(name, from);
  pos = start;
  after = next;
}

const toIdle = () => play('idle');
const toRun = () => play('run', null, meta.run_join);
const toDown = () => {
  demo = null;   // down is down: from here only a real run gets it up
  play('down');
};

/** Jump straight to the resting pose for the current run, no transitions. */
function settle() {
  const want = target();
  if (want === 'run') toRun();
  else if (want === 'fail' && clip !== 'idle') toDown();
  else if (want === 'idle' && clip !== 'down') toIdle();
}

function draw(index) {
  if (!ctx || !sheet) return;
  const { width: w, height: h, columns } = meta;
  ctx.clearRect(0, 0, w, h);
  ctx.drawImage(sheet, (index % columns) * w, Math.floor(index / columns) * h, w, h, 0, 0, w, h);
}

/** Show the current frame, then decide what comes after it. */
function step() {
  draw(frames[pos]);
  const want = target();
  const last = pos >= frames.length - 1;
  switch (clip) {
    case 'idle': {
      const every = meta.idle_exit_every;
      if (want === 'run' && pos % every === 0) {
        if (pos === 0) play('start', toRun);
        else play('neutral', () => play('start', toRun), 0, String(pos));
      } else {
        pos = (pos + 1) % frames.length;
      }
      break;
    }
    case 'run':
      if (demo === 'run' && --demoLeft <= 0) demo = 'fail';
      if (want === 'idle' && pos === meta.stop_after) play('stop', toIdle);
      else if (want === 'fail' && pos === meta.crash_after) play('crash', toDown);
      else pos = (pos + 1) % frames.length;
      break;
    case 'down':
      if (want === 'run') play('getup', toRun);
      break;
    default:     // one-shot transitions run to the end, whatever the target
      if (last) after(); else pos += 1;
  }
}

function fps() {
  const name = clip === 'neutral' ? 'idle_to_neutral' : clip === 'down' ? 'crash' : clip;
  return meta.clips[name].fps;
}

function visible() {
  return canvas && canvas.isConnected && canvas.offsetParent !== null && !document.hidden;
}

function tick() {
  timer = null;
  if (!visible()) {
    if (!hiddenSince) hiddenSince = performance.now();
    timer = setTimeout(tick, 500);
    return;
  }
  // Back after a while away: don't replay transitions for things long over.
  if (hiddenSince && performance.now() - hiddenSince > 3000) settle();
  hiddenSince = 0;
  step();
  // A held crash pose has nothing to redraw; just check for a new run.
  timer = setTimeout(tick, clip === 'down' ? 250 : 1000 / fps());
}

/** Start the clock if it isn't running. Never steps a running clock: the Run
 * view re-renders on every status poll and would speed the animation up. */
function wake() {
  if (meta && sheet && !timer) tick();
}

function fit() {
  if (!host || !meta) return;
  // A whole number of *device* pixels per sprite pixel, so every sprite pixel
  // is the same size on screen; on scaled displays that also gives finer steps.
  // Sized by the plain standing dinosaur (ref_height), so the crash's taller
  // canvas and a hat's headroom add room above it instead of shrinking it.
  const dpr = window.devicePixelRatio || 1;
  const k = Math.max(1, Math.round(Math.min(
    (host.clientHeight * HEIGHT_SHARE * dpr) / meta.ref_height,
    (host.clientWidth * WIDTH_SHARE * dpr) / meta.width)));
  canvas.style.width = `${(meta.width * k) / dpr}px`;
  canvas.style.height = `${(meta.height * k) / dpr}px`;
}

function fetchTheme(id) {
  return Promise.all([
    fetch(`${ASSETS}/${id}.json`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
    new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = `${ASSETS}/${id}.png`;
    }),
  ]);
}

async function load(id) {
  theme = id;
  let body;
  let image;
  try {
    [body, image] = await fetchTheme(id);
  } catch {
    if (theme !== id) return;
    if (id !== 'standard') load('standard');
    else if (!meta) canvas.remove();   // decoration only: no sheet, no dinosaur
    return;
  }
  if (theme !== id) return;      // a later choice won the race
  const first = !meta;
  meta = body;
  sheet = image;
  canvas.width = meta.width;
  canvas.height = meta.height;
  ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  if (first) {
    // Open the page mid-run (or after a crash) in the matching pose.
    if (target() === 'run') toRun();
    else if (target() === 'fail') toDown();
    else toIdle();
  } else {
    // Same clip, same place in it, new costume.
    frames = framesFor(clip, neutralFrom) || meta.clips.idle.frames;
    pos = Math.min(pos, frames.length - 1);
    draw(frames[pos]);
  }
  fit();
  wake();
}

/** Put the dinosaur into a terminal container (position: relative). The same
 * canvas moves between re-renders, so the animation never restarts. */
export function attachDino(container) {
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.className = 'dino';
    canvas.setAttribute('aria-hidden', 'true');
    resizer = new ResizeObserver(fit);
    window.addEventListener('resize', fit);   // browser zoom changes the pixel ratio
    load(currentTheme());
  }
  if (host !== container) {
    if (host) resizer.unobserve(host);
    host = container;
    resizer.observe(host);
  }
  container.prepend(canvas);
  fit();
  wake();
}

/* --- holidays ------------------------------------------------------------ */

export const THEMES = [
  { id: 'newyear', label: 'New Year' },
  { id: 'valentine', label: "Valentine's Day" },
  { id: 'mardigras', label: 'Mardi Gras' },
  { id: 'easter', label: 'Easter' },
  { id: 'pride', label: 'Pride' },
  { id: 'july4', label: 'Fourth of July' },
  { id: 'labor', label: 'Labor Day' },
  { id: 'halloween', label: 'Halloween' },
  { id: 'santa', label: 'Christmas' },
];

const day = (y, m, d) => new Date(y, m - 1, d);
const shift = (date, days) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);

/** Western Easter Sunday (anonymous Gregorian algorithm). */
function easter(y) {
  const a = y % 19; const b = Math.floor(y / 100); const c = y % 100;
  const d = Math.floor(b / 4); const e = b % 4; const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3); const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4); const k = c % 4; const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const n = h + l - 7 * m + 114;
  return day(y, Math.floor(n / 31), (n % 31) + 1);
}

/** US Labor Day: the first Monday of September. */
function laborDay(y) {
  const first = day(y, 9, 1);
  return shift(first, (8 - first.getDay()) % 7);
}

/** [id, first day, last day] of each holiday's window in year y, inclusive.
 * The first match wins: Mardi Gras can overlap Valentine's week, and takes it. */
function windows(y) {
  const sunday = easter(y);
  const labor = laborDay(y);
  return [
    ['newyear', day(y - 1, 12, 29), day(y, 1, 2)],
    ['mardigras', shift(sunday, -51), shift(sunday, -47)],   // Friday to Fat Tuesday
    ['valentine', day(y, 2, 7), day(y, 2, 14)],
    ['easter', shift(sunday, -7), sunday],
    ['pride', day(y, 6, 1), day(y, 6, 7)],
    ['july4', day(y, 6, 27), day(y, 7, 4)],
    ['labor', shift(labor, -3), labor],                      // Friday to Monday
    ['halloween', day(y, 10, 24), day(y, 10, 31)],
    ['santa', day(y, 12, 18), day(y, 12, 25)],
    ['newyear', day(y, 12, 29), day(y + 1, 1, 2)],
  ];
}

/** The holiday on a date (the viewer's local calendar), or 'standard'. */
export function themeFor(date = new Date()) {
  const today = day(date.getFullYear(), date.getMonth() + 1, date.getDate());
  for (const [id, first, last] of windows(today.getFullYear())) {
    if (today >= first && today <= last) return id;
  }
  return 'standard';
}

function storedChoice() {
  try { return localStorage.getItem(CHOICE_KEY) || 'auto'; } catch { return 'auto'; }
}

function currentTheme() {
  const choice = storedChoice();
  if (choice === 'standard' || THEMES.some((t) => t.id === choice)) return choice;
  return themeFor();
}

/** Pick 'auto', 'standard' or a holiday id, remember it, and swap the sheet. */
export function setDinoTheme(choice) {
  try { localStorage.setItem(CHOICE_KEY, choice); } catch { /* this visit only */ }
  const id = currentTheme();
  if (canvas && id !== theme) load(id);
}

let menu = null;

function closeMenu() {
  if (!menu) return;
  menu.remove();
  menu = null;
  document.removeEventListener('pointerdown', outside, true);
  document.removeEventListener('keydown', onKey, true);
}
function outside(event) {
  if (!menu.contains(event.target) && !event.target.closest('.app-icon')) closeMenu();
}
function onKey(event) { if (event.key === 'Escape') closeMenu(); }

/** The small costume menu under the MPACT icon; a second click closes it. */
export function toggleDinoMenu(anchor) {
  if (menu) { closeMenu(); return; }
  const choice = storedChoice();
  const dated = themeFor();
  const today = dated === 'standard' ? 'none today' : THEMES.find((t) => t.id === dated).label;
  const options = [{ id: 'auto', label: `By date (${today})` }, { id: 'standard', label: 'None' }, ...THEMES];
  menu = document.createElement('div');
  menu.className = 'dino-menu';
  menu.setAttribute('role', 'menu');
  for (const option of options) {
    const item = document.createElement('button');
    item.type = 'button';
    item.setAttribute('role', 'menuitemradio');
    item.setAttribute('aria-checked', String(option.id === choice));
    item.textContent = option.label;
    item.addEventListener('click', () => { setDinoTheme(option.id); closeMenu(); });
    menu.append(item);
  }
  const box = anchor.getBoundingClientRect();
  menu.style.left = `${box.left}px`;
  menu.style.top = `${box.bottom + 6}px`;
  document.body.append(menu);
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('keydown', onKey, true);
}

export const _state = () => ({ clip, pos, theme });   // for tests
