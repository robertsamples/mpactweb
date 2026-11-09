/* The dinosaur behind the Run terminal.
 *
 * Frames come from one sprite sheet (web/assets/dino.png, built by
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
 */

import { store } from '../state.js';

const SHEET = '/assets/dino.png';
const META = '/assets/dino.json';
const HEIGHT_SHARE = 0.46;   // of the terminal's height, before rounding
const WIDTH_SHARE = 0.6;     // never much wider than this share of it

let meta = null;
let sheet = null;
let canvas = null;
let ctx = null;
let host = null;
let resizer = null;

let clip = 'idle';           // current clip name, or 'down' (crash held)
let frames = [];             // sheet indices of the clip being played
let pos = 0;                 // index into frames
let after = null;            // what follows a one-shot clip
let timer = null;
let hiddenSince = 0;

function target() {
  const run = store.get('run');
  if (!run) return 'idle';
  if (run.state === 'queued' || run.state === 'running') return 'run';
  if (run.state === 'failed') return 'fail';
  return 'idle';
}

function play(name, list, next = null, start = 0) {
  clip = name;
  frames = list || meta.clips[name].frames;
  pos = start;
  after = next;
}

const toIdle = () => play('idle', null, null, 0);
const toRun = () => play('run', null, null, meta.run_join);
const toDown = () => { const f = meta.clips.crash.frames; play('down', [f[f.length - 1]]); };

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
        if (pos === 0) play('start', null, toRun);
        else play('neutral', meta.clips.idle_to_neutral.from[String(pos)],
                  () => play('start', null, toRun));
      } else {
        pos = (pos + 1) % frames.length;
      }
      break;
    }
    case 'run':
      if (want === 'idle' && pos === meta.stop_after) play('stop', null, toIdle);
      else if (want === 'fail' && pos === meta.crash_after) play('crash', null, toDown);
      else pos = (pos + 1) % frames.length;
      break;
    case 'down':
      if (want === 'run') play('getup', null, toRun);
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
  const dpr = window.devicePixelRatio || 1;
  const k = Math.max(1, Math.round(Math.min(
    (host.clientHeight * HEIGHT_SHARE * dpr) / meta.height,
    (host.clientWidth * WIDTH_SHARE * dpr) / meta.width)));
  canvas.style.width = `${(meta.width * k) / dpr}px`;
  canvas.style.height = `${(meta.height * k) / dpr}px`;
}

async function load() {
  try {
    const [body, image] = await Promise.all([
      fetch(META).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
      new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = SHEET;
      }),
    ]);
    meta = body;
    sheet = image;
  } catch {
    canvas.remove();   // decoration only: no sheet, no dinosaur
    return;
  }
  canvas.width = meta.width;
  canvas.height = meta.height;
  ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  // Open the page mid-run (or after a crash) in the matching pose.
  if (target() === 'run') toRun();
  else if (target() === 'fail') toDown();
  else toIdle();
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
    load();
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

export const _state = () => ({ clip, pos });   // for tests
