import { motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import type { Lifecycle, PageId } from '../../types';
import type { ReactNode } from 'react';

import { resolveLifecycle } from '../../utils/pageLifecycle';
import type { ModuleDefinition } from '../../utils/moduleRegistry';
import {
  SHOW_DEPRECATED_STORAGE_KEY,
  SHOW_EXPERIMENTAL_STORAGE_KEY,
} from '../pages/SettingsPage';
import { useToggleFlag } from '../../hooks/useToggleFlag';
import { useCollapsible } from '../../hooks/useCollapsible';
import { MODULE_BY_ID, MODULE_NAV_SECTIONS } from '../../utils/moduleRegistry';
import { getActiveHost } from '../../state/hostStore';
import { useRedVisible } from '../../state/redUnlockStore';
import { ExternalLinkIcon } from '../ExternalLinkIcon';

/* ------------------------------------------------------------------ */
/*  Icons (20x20, viewBox 0 0 24 24, stroke=currentColor, sw=1.5)    */
/* ------------------------------------------------------------------ */

// Shared icon for the four "Insights" pages (Connections, Projects, Code Envs,
// K8s). Same glyph signals "this is the Insights view of its section".
const insightsIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="2" width="6" height="6" rx="1" />
    <rect x="2" y="16" width="6" height="6" rx="1" />
    <rect x="16" y="16" width="6" height="6" rx="1" />
    <path d="M12 8v3" />
    <path d="M5 16v-2h14v2" />
  </svg>
);

const icons: Record<string, ReactNode> = {
  summary: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  ),
  filesystem: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="8" rx="2" />
      <rect x="2" y="14" width="20" height="8" rx="2" />
      <circle cx="6" cy="6" r="1" />
      <circle cx="6" cy="18" r="1" />
    </svg>
  ),
  memory: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="4" width="14" height="16" rx="1" />
      <path d="M9 4V2M15 4V2M9 20v2M15 20v2M5 8H3M5 12H3M5 16H3M19 8h2M19 12h2M19 16h2" />
      <rect x="8" y="7" width="8" height="4" rx="0.5" />
    </svg>
  ),
  cpu: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2" />
    </svg>
  ),
  projects: insightsIcon,
  users: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  ),
  'code-envs': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  ),
  'code-envs-cleaner': insightsIcon,
  'code-envs-comparison': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  ),
  'connections-inventory': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  ),
  'connections-insights': insightsIcon,
  'connections-health': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12h4l3-8 4 16 3-8h4" />
    </svg>
  ),
  'connections-usage': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <line x1="4" y1="20" x2="4" y2="10" />
      <line x1="10" y1="20" x2="10" y2="4" />
      <line x1="16" y1="20" x2="16" y2="14" />
      <line x1="22" y1="20" x2="2" y2="20" />
    </svg>
  ),
  'connections-fs-migration': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v3" />
      <path d="M3 11v8a2 2 0 0 0 2 2h8" />
      <path d="M14 18h7" />
      <path d="m18 15 3 3-3 3" />
    </svg>
  ),
  logs: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  ),
  'sanity-check': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  ),
  'image-cleaner': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </svg>
  ),
  'container-execs': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M7 8h10M7 12h4M15 12h2" />
      <path d="M8 18v2M16 18v2M6 20h12" />
    </svg>
  ),
  'cs-template-replacement': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="21 4 21 9 16 9" />
      <polyline points="3 20 3 15 8 15" />
      <path d="M5.5 10a7 7 0 0 1 12-2.5L21 9" />
      <path d="M18.5 14a7 7 0 0 1-12 2.5L3 15" />
    </svg>
  ),
  'project-cleaner': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 8v13H3V8" />
      <path d="M1 3h22v5H1z" />
      <path d="M10 12h4" />
    </svg>
  ),
  'project-compute': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="9" width="13" height="10" rx="1" />
      <rect x="7" y="5" width="4" height="4" />
      <line x1="9" y1="5" x2="9" y2="2" />
      <path d="M16 11h4v6h-4" />
      <line x1="1" y1="13" x2="3" y2="13" />
    </svg>
  ),
  plugins: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
  'plugins-installed': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  ),
  'db-health': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  ),
  report: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  ),
  'llm-audit': (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="5" width="14" height="14" rx="2" />
      <path d="M9 9h6M9 12h6M9 15h4" />
      <path d="M3 9h2M3 15h2M19 9h2M19 15h2M9 3v2M15 3v2M9 19v2M15 19v2" />
    </svg>
  ),
  'k8s-insights': insightsIcon,
  settings: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

/* ------------------------------------------------------------------ */
/*  Trailing status glyph                                              */
/* ------------------------------------------------------------------ */

function CircleGlyph() {
  return (
    <span
      aria-hidden
      className="flex-shrink-0 inline-flex items-center justify-center w-3.5 h-3.5 text-[var(--text-tertiary)] opacity-60"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
        <circle cx="12" cy="12" r="9" />
      </svg>
    </span>
  );
}

// Completion checkmark: success green. It pops in crisp, then disintegrates
// into its own pixels (see PixelDustCheck) — green confirms "done" before the
// row settles back to a clean, glyph-free state.
function CheckGlyph() {
  return (
    <span
      aria-hidden
      className="flex-shrink-0 inline-flex items-center justify-center w-3.5 h-3.5 text-[var(--neon-green)]"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Pixel-dust completion ritual                                       */
/* ------------------------------------------------------------------ */

// The check holds crisp for a beat, then explodes into real pixel dust:
// the glyph is rasterized to a canvas, each opaque pixel becomes a particle
// that drifts outward with gravity/drag in a left-to-right wave, fading out.
const DUST_HOLD_MS = 1500; // green check sits crisp before it disintegrates
const DUST_MS = 4650; // particle flight (~1.5x slower); HOLD + DUST ≈ 6.15s total
const DUST_WAVE_MS = 1140; // left-to-right disintegration stagger (~1.5x slower)
const DUST_BOX = 28; // canvas overlay (css px) — larger than the 14px glyph so dust scatters

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    !!window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

interface DustParticle {
  x: number;
  y: number;
  r: number;
  g: number;
  b: number;
  a: number;
  vx: number;
  vy: number;
  delay: number;
  drag: number;
  grav: number;
}

// Rasterize the check onto `canvas`, sample its opaque pixels into particles,
// and animate the disintegration. Returns a cancel fn; calls onDone when spent.
function runPixelDust(canvas: HTMLCanvasElement, color: string, onDone: () => void): () => void {
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    onDone();
    return () => {};
  }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const px = Math.max(1, Math.round(dpr)); // device-pixel size of one "pixel"
  canvas.width = DUST_BOX * dpr;
  canvas.height = DUST_BOX * dpr;
  const W = canvas.width;
  const H = canvas.height;

  // Draw the same check the SVG uses (viewBox 24, points "20 6 9 17 4 12"),
  // scaled to 14px and centered in the box.
  const glyph = 14;
  const off = (DUST_BOX - glyph) / 2;
  const scale = glyph / 24;
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.translate(off, off);
  ctx.scale(scale, scale);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(20, 6);
  ctx.lineTo(9, 17);
  ctx.lineTo(4, 12);
  ctx.stroke();
  ctx.restore();

  const data = ctx.getImageData(0, 0, W, H).data;
  const particles: DustParticle[] = [];
  let minX = W;
  let maxX = 0;
  let cx = 0;
  let cy = 0;
  for (let y = 0; y < H; y += px) {
    for (let x = 0; x < W; x += px) {
      const i = (y * W + x) * 4;
      const a = data[i + 3];
      if (a > 40) {
        particles.push({
          x,
          y,
          r: data[i],
          g: data[i + 1],
          b: data[i + 2],
          a: a / 255,
          vx: 0,
          vy: 0,
          delay: 0,
          drag: 0,
          grav: 0,
        });
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        cx += x;
        cy += y;
      }
    }
  }
  const n = particles.length;
  if (!n) {
    onDone();
    return () => {};
  }
  cx /= n;
  cy /= n;
  const spanX = Math.max(1, maxX - minX);
  for (const p of particles) {
    const ang = Math.atan2(p.y - cy, p.x - cx) + (Math.random() - 0.5) * 0.9;
    // ~3x slower motion, same trajectory shape: velocity /3, gravity /9 (accel ∝ t²),
    // drag → cube-root so velocity decays over 3x as many frames.
    const speed = ((0.5 + Math.random() * 1.7) / 3) * dpr;
    p.vx = Math.cos(ang) * speed;
    p.vy = Math.sin(ang) * speed - ((0.4 + Math.random() * 0.7) / 3) * dpr; // slight upward pop
    p.delay = ((p.x - minX) / spanX) * DUST_WAVE_MS; // left-to-right wave
    p.drag = Math.cbrt(0.94 + Math.random() * 0.04);
    p.grav = ((0.05 + Math.random() * 0.12) / 9) * dpr;
  }

  const flight = DUST_MS - DUST_WAVE_MS;
  const start = performance.now();
  let raf = 0;
  function frame(now: number) {
    const t = now - start;
    ctx!.clearRect(0, 0, W, H);
    let alive = false;
    for (const p of particles) {
      const local = t - p.delay;
      if (local <= 0) {
        // still resting as part of the intact check
        ctx!.globalAlpha = p.a;
        ctx!.fillStyle = `rgb(${p.r},${p.g},${p.b})`;
        ctx!.fillRect(p.x, p.y, px, px);
        alive = true;
        continue;
      }
      const lifeT = local / flight;
      if (lifeT >= 1) continue;
      alive = true;
      p.vy += p.grav;
      p.vx *= p.drag;
      p.vy *= p.drag;
      p.x += p.vx;
      p.y += p.vy;
      ctx!.globalAlpha = Math.max(0, p.a * (1 - lifeT) * (1 - lifeT));
      ctx!.fillStyle = `rgb(${p.r},${p.g},${p.b})`;
      ctx!.fillRect(p.x, p.y, px, px);
    }
    ctx!.globalAlpha = 1;
    if (alive && t < DUST_MS) {
      raf = requestAnimationFrame(frame);
    } else {
      onDone();
    }
  }
  raf = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(raf);
}

// Green check that holds, then disintegrates into pixel dust and vanishes.
// Honors prefers-reduced-motion by leaving the static green check in place.
function PixelDustCheck() {
  const [reduced] = useState(prefersReducedMotion);
  const [stage, setStage] = useState<'hold' | 'dust' | 'gone'>('hold');
  const wrapRef = useRef<HTMLSpanElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (reduced) return; // reduced motion: keep the static green check
    const id = window.setTimeout(() => setStage('dust'), DUST_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [reduced]);

  useEffect(() => {
    if (stage !== 'dust') return;
    const done = () => setStage('gone');
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) {
      const id = window.setTimeout(done, 0); // refs always present here; defensive only
      return () => window.clearTimeout(id);
    }
    const color = getComputedStyle(wrap).color || 'rgb(34, 197, 94)';
    return runPixelDust(canvas, color, done);
  }, [stage]);

  if (stage === 'gone') {
    return <span aria-hidden className="flex-shrink-0 inline-block w-3.5 h-3.5" />;
  }

  if (stage === 'dust') {
    return (
      <span
        ref={wrapRef}
        aria-hidden
        className="relative flex-shrink-0 inline-flex items-center justify-center w-3.5 h-3.5 text-[var(--neon-green)]"
      >
        <canvas
          ref={canvasRef}
          className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ width: `${DUST_BOX}px`, height: `${DUST_BOX}px` }}
        />
      </span>
    );
  }

  return <CheckGlyph />;
}

function SpinnerGlyph() {
  return (
    <span
      aria-hidden
      className="flex-shrink-0 inline-flex items-center justify-center w-3.5 h-3.5 text-[var(--neon-yellow)] animate-spin"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
    </span>
  );
}

function ErrorGlyph() {
  return (
    <span
      aria-hidden
      className="flex-shrink-0 inline-flex items-center justify-center w-3.5 h-3.5 text-[var(--neon-red)]"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    </span>
  );
}

interface SidebarItemStatusProps {
  module: ModuleDefinition;
  data: import('../../types').ParsedData;
}

// Cross-fade between glyph states (Material 195ms / HIG 200ms — snappy not
// abrupt). Reaching `done` swaps the old sticky-check ghost-fade for a
// pixel-dust disintegration (PixelDustCheck): the green check holds, then
// explodes into its pixels and the row settles glyph-free.
// motion-reduce:transition-none disables transitions for users who set
// `prefers-reduced-motion: reduce` (PixelDustCheck also skips the dust then).
const CROSSFADE_MS = 200;

type GlyphKind = 'queued' | 'running' | 'done' | 'error';

function glyphKindOf(lc: Lifecycle): GlyphKind {
  return lc.phase;
}

function renderGlyph(kind: GlyphKind): ReactNode {
  switch (kind) {
    case 'queued':
      return <CircleGlyph />;
    case 'running':
      return <SpinnerGlyph />;
    case 'done':
      return <CheckGlyph />;
    case 'error':
      return <ErrorGlyph />;
  }
}

function SidebarItemStatus({ module, data }: SidebarItemStatusProps) {
  const { addDebugLog } = useDiag();
  const lc = resolveLifecycle(module, data);
  const kind = glyphKindOf(lc);
  // Track previous kind so we can fire a debug-log entry the moment the
  // checkmark first appears (idle/queued/running → done). Errors don't count
  // as "checkmark"; they get their own ✕ glyph and a separate log line.
  const prevKindRef = useRef<GlyphKind | null>(null);

  useEffect(() => {
    const prev = prevKindRef.current;
    if (kind === 'done' && prev !== 'done') {
      addDebugLog(`${module.label}: ready (✓)`, 'lifecycle');
    } else if (kind === 'error' && prev !== 'error') {
      const msg = lc.phase === 'error' ? lc.error : 'failed';
      addDebugLog(`${module.label}: error — ${msg}`, 'lifecycle', 'warn');
    }
    prevKindRef.current = kind;
  }, [kind, lc, module.label, addDebugLog]);

  return (
    <span
      data-state={kind}
      className={`inline-flex items-center justify-center transition-opacity ease-out motion-reduce:transition-none opacity-100`}
      style={{ transitionDuration: `${CROSSFADE_MS}ms` }}
    >
      {kind === 'done' ? (
        // Keyed on finishedAt so a fresh done-episode (e.g. after a session
        // reset) remounts and replays the dust instead of staying gone.
        <PixelDustCheck key={lc.phase === 'done' ? lc.finishedAt : 'done'} />
      ) : (
        renderGlyph(kind)
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Nav structure                                                      */
/* ------------------------------------------------------------------ */

interface NavSection {
  title: string;
  items: PageId[];
}

const NAV_SECTIONS: readonly NavSection[] = MODULE_NAV_SECTIONS;

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  onBackToHosts?: () => void;
}

interface SidebarSectionProps {
  section: NavSection;
  idx: number;
  collapsed: boolean;
  renderItem: (pageId: PageId) => ReactNode;
}

// One nav section: a collapsible header (label + green chevron) over the classic
// icon+label rows. Expanded by default; each section's open state persists.
function SidebarSection({ section, idx, collapsed, renderItem }: SidebarSectionProps) {
  const { isOpen, toggle } = useCollapsible({
    id: `sidebar-section-${section.title}`,
    defaultOpen: true,
  });

  // Rail (icon-only) mode: no headers, no collapsibility — unchanged.
  if (collapsed) {
    return (
      <div className={idx > 0 ? 'mt-4' : ''}>
        <div className="flex flex-col gap-0.5">{section.items.map(renderItem)}</div>
      </div>
    );
  }

  return (
    <div className={idx > 0 ? 'mt-4' : ''}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={isOpen}
        className="flex items-center gap-1 w-full px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[#2AB1AC] hover:text-[var(--text-primary)] transition-colors"
      >
        <motion.svg
          animate={{ rotate: isOpen ? 0 : -90 }}
          transition={{ duration: 0.2 }}
          className="w-3 h-3 flex-shrink-0 -ml-0.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </motion.svg>
        <span>{section.title}</span>
      </button>
      <div className="collapse-content" data-state={isOpen ? 'open' : 'closed'}>
        <div>
          <div className="flex flex-col gap-0.5">{section.items.map(renderItem)}</div>
        </div>
      </div>
    </div>
  );
}

export function Sidebar({ collapsed, onToggleCollapse, onBackToHosts }: SidebarProps) {
  const { state, setActivePage, addDebugLog } = useDiag();
  const { activePage, parsedData } = state;

  const [showExperimental] = useToggleFlag(
    SHOW_EXPERIMENTAL_STORAGE_KEY,
    'experimental-flag-changed',
  );
  const [showDeprecated] = useToggleFlag(
    SHOW_DEPRECATED_STORAGE_KEY,
    'deprecated-flag-changed',
  );
  const redVisible = useRedVisible();

  const visibleSections = NAV_SECTIONS
    .map((section) => ({
      ...section,
      items: section.items.filter((id) => {
        const m = MODULE_BY_ID[id];
        if (m.experimental && !showExperimental) return false;
        if (m.deprecated && !showDeprecated) return false;
        if (m.tool && !redVisible) return false;
        return true;
      }),
    }))
    .filter((section) => section.items.length > 0);

  // ↑/↓ change the active page (previous/next visible page), skipping section
  // titles and pages in collapsed sections. No focus moves — the only feedback
  // is the existing active highlight + blue bar. ←/→ are intentionally ignored.
  const navRef = useRef<HTMLElement>(null);
  const activePageRef = useRef(activePage);
  activePageRef.current = activePage;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) {
        return;
      }
      const nav = navRef.current;
      if (!nav) return;
      const ids: PageId[] = Array.from(nav.querySelectorAll<HTMLElement>('[data-page-id]'))
        .filter((el) => el.closest('.collapse-content')?.getAttribute('data-state') !== 'closed')
        .map((el) => el.dataset.pageId as PageId)
        .filter((id) => id !== 'sanity-check');
      if (ids.length === 0) return;
      const dir = e.key === 'ArrowDown' ? 1 : -1;
      const i = ids.indexOf(activePageRef.current);
      const next = i === -1 ? (dir === 1 ? 0 : ids.length - 1) : i + dir;
      if (next < 0 || next >= ids.length) return;
      e.preventDefault();
      setActivePage(ids[next]);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setActivePage]);

  // Glyph state is now derived directly from each module's Lifecycle. No
  // separate initialization/justReady tracking is needed — `queued` is the
  // default starting state (○) and `done` is the only path to ✓.

  // Badge counts
  const logsBadge = parsedData.formattedLogErrors ? 1 : 0;

  function getBadgeCount(badge?: 'logs'): number {
    if (badge === 'logs') return logsBadge;
    return 0;
  }

  // Shared navigation behaviour for both the rail rows and the expanded tiles.
  function handleNavClick(pageId: PageId, label: string) {
    if (pageId === 'sanity-check') {
      const base = getActiveHost().url || window.location.origin;
      const url = `${base.replace(/\/$/, '')}/admin/maintenance/sanitycheck/`;
      addDebugLog(`External: open ${url} (clicked "${label}")`, 'navigation');
      window.open(url, '_blank', 'noopener,noreferrer');
      return;
    }
    addDebugLog(`Navigate: ${activePage} → ${pageId} (clicked "${label}")`, 'navigation');
    setActivePage(pageId);
  }

  function renderNavItem(pageId: PageId) {
    const item = MODULE_BY_ID[pageId];
    const label = item.navLabel || item.label;
    const isActive = activePage === pageId;
    const isTool = item.tool === true;
    const badgeCount = getBadgeCount(item.badge);

    const baseClasses = isTool
      ? (isActive
        ? 'bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-400/25'
        : 'text-red-300/90 hover:text-red-200 hover:bg-red-500/10')
      : (isActive
        ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]');

    return (
      <button
        key={pageId}
        type="button"
        data-page-id={pageId}
        onClick={() => handleNavClick(pageId, label)}
        title={collapsed ? label : undefined}
        className={`relative flex items-center gap-3 w-full rounded-md px-2.5 py-1.5 text-sm transition-colors duration-200 ${baseClasses} ${collapsed ? 'justify-center px-0' : ''}`}
      >
        {/* Active indicator bar */}
        {isActive && (
          <motion.div
            layoutId="sidebar-active"
            className={`absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-r ${isTool ? 'bg-red-400' : 'bg-[var(--accent)]'}`}
            transition={{ type: 'spring', stiffness: 500, damping: 35 }}
          />
        )}

        <span className="flex-shrink-0">{icons[pageId]}</span>

        {!collapsed && (
          <>
            <span className={`flex-1 text-left whitespace-nowrap${isTool ? ' premium-shine-text' : ''}`}>
              {label}
              {pageId === 'sanity-check' && <ExternalLinkIcon />}
            </span>
            {badgeCount > 0 && (
              <span className="flex-shrink-0 min-w-[20px] h-5 flex items-center justify-center rounded-full bg-[var(--accent-muted)] text-[var(--accent)] text-xs font-medium px-1.5">
                {badgeCount}
              </span>
            )}
            {/* Action pages (noLoadGlyph) never show a load glyph — they have no
                startup ritual; their lifecycle field drives only in-page UI. */}
            {!item.noLoadGlyph && <SidebarItemStatus module={item} data={parsedData} />}
          </>
        )}
      </button>
    );
  }

  return (
    <aside
      className="flex flex-col h-full bg-[var(--bg-sidebar)] border-r border-[var(--border-default)] overflow-hidden"
    >
      {/* Host picker + collapse toggle */}
      <div className={`flex items-center px-4 ${collapsed ? 'flex-col gap-1.5 px-2 py-3' : 'justify-between h-11'}`}>
        <button
          type="button"
          onClick={onBackToHosts}
          title="Back to host picker"
          className={`flex items-center gap-2 rounded-md px-2 py-1 text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors ${collapsed ? 'justify-center' : ''}`}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5" />
            <path d="m12 19-7-7 7-7" />
          </svg>
          {!collapsed && <span className="text-sm font-medium">Hosts</span>}
        </button>
        <button
          type="button"
          onClick={onToggleCollapse}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={`flex items-center justify-center w-6 h-6 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors`}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {collapsed ? (
              <>
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="9" y1="3" x2="9" y2="21" />
                <path d="m14 9 3 3-3 3" />
              </>
            ) : (
              <>
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="9" y1="3" x2="9" y2="21" />
                <path d="m16 15-3-3 3-3" />
              </>
            )}
          </svg>
        </button>
      </div>

      {/* Divider */}
      <div className="mx-3 border-t border-[var(--border-default)]" />

      {/* Navigation — collapsible sections over the classic icon+label rows. */}
      <nav ref={navRef} className="flex-1 overflow-y-auto px-2 py-3 space-y-0">
        {visibleSections.map((section, idx) => (
          <SidebarSection
            key={section.title}
            section={section}
            idx={idx}
            collapsed={collapsed}
            renderItem={renderNavItem}
          />
        ))}
      </nav>

    </aside>
  );
}
