// Shared full-screen particle engine (canvas 2D, additive blending) in the
// same hand-rolled spirit as the sidebar's pixel-dust checkmark. One lazily
// created overlay canvas hosts every effect; the rAF loop only runs while
// particles are alive, so the layer costs nothing at idle.

import { prefersReducedMotion } from './fxBus';

type RGB = readonly [number, number, number];

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number; // seconds lived (negative while delayed)
  ttl: number;
  size: number;
  color: RGB;
  gravity: number;
  drag: number;
  kind: 'spark' | 'dust' | 'ring';
  phase: number; // twinkle offset
}

const PALETTE: readonly RGB[] = [
  [96, 165, 250], // blue
  [167, 139, 250], // violet
  [45, 212, 191], // teal
  [74, 222, 128], // green
  [237, 237, 239], // white
];

const MAX_PARTICLES = 2400;

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let particles: Particle[] = [];
let raf = 0;
let lastTime = 0;

function ensureCanvas(): CanvasRenderingContext2D | null {
  if (ctx) return ctx;
  canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  Object.assign(canvas.style, {
    position: 'fixed',
    inset: '0',
    width: '100vw',
    height: '100vh',
    zIndex: '9000',
    pointerEvents: 'none',
  });
  document.body.appendChild(canvas);
  ctx = canvas.getContext('2d');
  return ctx;
}

function resize() {
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(window.innerWidth * dpr);
  const h = Math.round(window.innerHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

function frame(now: number) {
  raf = 0;
  const c = ctx;
  if (!c || !canvas) return;
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;
  resize();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  c.clearRect(0, 0, canvas.width, canvas.height);
  c.save();
  c.scale(dpr, dpr);
  c.globalCompositeOperation = 'lighter';

  const next: Particle[] = [];
  for (const p of particles) {
    p.life += dt;
    if (p.life < 0) {
      next.push(p);
      continue;
    }
    if (p.life >= p.ttl) continue;
    const t = p.life / p.ttl;

    if (p.kind === 'ring') {
      // size = final radius; eased expansion with a fading, thinning stroke.
      const ease = 1 - Math.pow(1 - t, 3);
      const r = p.size * ease;
      const alpha = (1 - t) * 0.55;
      c.globalAlpha = alpha;
      c.lineWidth = Math.max(0.75, 2.5 * (1 - t));
      c.strokeStyle = `rgb(${p.color[0]},${p.color[1]},${p.color[2]})`;
      c.beginPath();
      c.arc(p.x, p.y, r, 0, Math.PI * 2);
      c.stroke();
      next.push(p);
      continue;
    }

    const dragF = Math.exp(-p.drag * dt);
    p.vx *= dragF;
    p.vy = p.vy * dragF + p.gravity * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;

    let alpha = 1 - t;
    if (p.kind === 'dust') {
      alpha *= 0.55 + 0.45 * Math.sin(p.life * 7 + p.phase);
    }
    c.globalAlpha = Math.max(0, Math.min(1, alpha));
    c.fillStyle = `rgb(${p.color[0]},${p.color[1]},${p.color[2]})`;
    const s = p.size * (p.kind === 'spark' ? 1 - t * 0.5 : 1);
    c.beginPath();
    c.arc(p.x, p.y, s, 0, Math.PI * 2);
    c.fill();
    next.push(p);
  }
  c.restore();
  particles = next;

  if (particles.length > 0) {
    raf = requestAnimationFrame(frame);
  } else {
    c.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function spawn(batch: Particle[]) {
  if (prefersReducedMotion()) return;
  if (!ensureCanvas()) return;
  particles.push(...batch);
  if (particles.length > MAX_PARTICLES) {
    particles = particles.slice(particles.length - MAX_PARTICLES);
  }
  if (!raf) {
    lastTime = performance.now();
    raf = requestAnimationFrame(frame);
  }
}

const rand = (min: number, max: number) => min + Math.random() * (max - min);
const pick = <T,>(arr: readonly T[]): T => arr[Math.floor(Math.random() * arr.length)];

export interface BurstOptions {
  count?: number;
  speed?: [number, number];
  palette?: readonly RGB[];
  gravity?: number;
  size?: [number, number];
  ttl?: [number, number];
}

/** Radial spark burst at viewport coordinates. */
export function burst(x: number, y: number, opts: BurstOptions = {}): void {
  const {
    count = 36,
    speed = [120, 480],
    palette = PALETTE,
    gravity = 320,
    size = [1, 2.6],
    ttl = [0.5, 1.1],
  } = opts;
  const batch: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const a = Math.random() * Math.PI * 2;
    const v = rand(speed[0], speed[1]);
    batch.push({
      x,
      y,
      vx: Math.cos(a) * v,
      vy: Math.sin(a) * v - v * 0.15,
      life: 0,
      ttl: rand(ttl[0], ttl[1]),
      size: rand(size[0], size[1]),
      color: pick(palette),
      gravity,
      drag: rand(1.6, 3.2),
      kind: 'spark',
      phase: 0,
    });
  }
  spawn(batch);
}

/**
 * The big one — analysis-complete celebration. Expanding shockwave rings and
 * a starburst at the focal point, plus a slow stardust fall across the top of
 * the viewport. Tuned to read as "victory" without burying the data.
 */
export function celebrate(origin?: { x: number; y: number }): void {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const ox = origin?.x ?? w * 0.5;
  const oy = origin?.y ?? h * 0.3;
  const batch: Particle[] = [];

  // Shockwave rings, staggered.
  const ringColors: RGB[] = [
    [74, 222, 128],
    [96, 165, 250],
    [167, 139, 250],
  ];
  ringColors.forEach((color, i) => {
    batch.push({
      x: ox,
      y: oy,
      vx: 0,
      vy: 0,
      life: -i * 0.13,
      ttl: 1.1,
      size: Math.min(w, h) * 0.28 * (1 + i * 0.18),
      color,
      gravity: 0,
      drag: 0,
      kind: 'ring',
      phase: 0,
    });
  });

  // Starburst.
  for (let i = 0; i < 170; i++) {
    const a = Math.random() * Math.PI * 2;
    const v = rand(220, 920);
    batch.push({
      x: ox,
      y: oy,
      vx: Math.cos(a) * v,
      vy: Math.sin(a) * v * 0.85 - 60,
      life: -rand(0, 0.08),
      ttl: rand(1.0, 2.1),
      size: rand(1, 3),
      color: pick(PALETTE),
      gravity: 340,
      drag: rand(1.8, 3.0),
      kind: 'spark',
      phase: 0,
    });
  }

  // Stardust drifting down from the top edge, arriving over ~1.4s.
  for (let i = 0; i < 130; i++) {
    batch.push({
      x: rand(0, w),
      y: rand(-30, -4),
      vx: rand(-22, 22),
      vy: rand(45, 130),
      life: -rand(0, 1.4),
      ttl: rand(2.4, 4.2),
      size: rand(0.8, 2),
      color: pick(PALETTE),
      gravity: 14,
      drag: 0.12,
      kind: 'dust',
      phase: rand(0, Math.PI * 2),
    });
  }

  spawn(batch);
}
