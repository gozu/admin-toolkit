import { useMemo } from 'react';
import {
  MAX_SAMPLE_SLOTS,
  computeResourceSeries,
  resourceSamplesStore,
} from '../../state/resourceSamples';

// Live CPU/MEM utilization strip — pure SVG (modeled on the Mission Control
// Sparkline), NOT Chart.js: the shared chartConfig keys its 650ms draw-in
// sweep by the labels signature, so a Chart.js line would replay its entrance
// animation on every 5s tick. Fixed 0–100% y-scale (no rescale jump); data
// enters at the right edge and slides left through a fixed 120-slot window.
// Series colors come from the validated --viz-cat slots (fixed order); both
// series are direct-labeled in the right gutter, so identity is never
// color-alone (CPU additionally carries the area wash).

const CPU_COLOR = 'var(--viz-cat-1)';
const MEM_COLOR = 'var(--viz-cat-2)';
const POINT_SLOTS = MAX_SAMPLE_SLOTS - 1; // 120 derived points
const GRID_PCTS = [25, 50, 75];

// viewBox 0 0 100 40; y: 0% → 39.5, 100% → 0.5
const Y = (pct: number) => 39.5 - (pct / 100) * 39;
const X = (i: number, n: number) => ((POINT_SLOTS - n + i + 0.5) / POINT_SLOTS) * 100;

function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LiveResourceChart() {
  const { status, samples, intervalMs } = resourceSamplesStore.use();
  const points = useMemo(() => computeResourceSeries(samples), [samples]);
  const n = points.length;
  const last = n > 0 ? points[n - 1] : null;
  const windowMinutes = Math.round((POINT_SLOTS * intervalMs) / 60_000);

  const cpuLine = points
    .map((p, i) => `${X(i, n).toFixed(2)},${Y(p.cpuPct).toFixed(2)}`)
    .join(' ');
  const memLine = points
    .map((p, i) => `${X(i, n).toFixed(2)},${Y(p.memPct).toFixed(2)}`)
    .join(' ');
  const cpuArea =
    n > 1
      ? `M ${X(0, n).toFixed(2)},39.5 L ${cpuLine.replace(/ /g, ' L ')} L ${X(n - 1, n).toFixed(2)},39.5 Z`
      : '';

  // Right-gutter label positions (percent of plot height), nudged apart when
  // the two series run close together.
  const labels = useMemo(() => {
    if (!last) return null;
    let cpuTop = (Y(last.cpuPct) / 40) * 100;
    let memTop = (Y(last.memPct) / 40) * 100;
    const MIN_GAP = 9;
    const gap = Math.abs(cpuTop - memTop);
    if (gap < MIN_GAP) {
      const push = (MIN_GAP - gap) / 2;
      if (cpuTop <= memTop) {
        cpuTop -= push;
        memTop += push;
      } else {
        memTop -= push;
        cpuTop += push;
      }
    }
    // Labels are translate-y-centered on `top`; keep the whole glyph inside.
    const clamp = (v: number) => Math.min(95, Math.max(5, v));
    return { cpuTop: clamp(cpuTop), memTop: clamp(memTop) };
  }, [last]);

  return (
    <div className="glass-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-4">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Live usage</h3>
          <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: CPU_COLOR }} />
            CPU
          </span>
          <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: MEM_COLOR }} />
            Memory
          </span>
        </div>
        <span className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${status === 'paused' ? 'bg-[var(--neon-amber)]' : 'animate-pulse bg-[var(--neon-green)]'}`}
          />
          {status === 'paused'
            ? 'paused — tab in background'
            : `sampling every ${Math.round(intervalMs / 1000)}s · ${windowMinutes} min window`}
        </span>
      </div>

      <div className="flex">
        {/* y-axis tick labels */}
        <div className="relative w-8 flex-shrink-0" aria-hidden>
          {GRID_PCTS.map((pct) => (
            <span
              key={pct}
              className="absolute right-1.5 -translate-y-1/2 font-mono text-[9px] leading-none text-[var(--text-tertiary)]"
              style={{ top: `${(Y(pct) / 40) * 100}%` }}
            >
              {pct}%
            </span>
          ))}
        </div>

        <div className="relative h-44 min-w-0 flex-1">
          {n >= 2 ? (
            <>
              <svg
                className="absolute inset-0 h-full w-full"
                viewBox="0 0 100 40"
                preserveAspectRatio="none"
                aria-hidden
              >
                {GRID_PCTS.map((pct) => (
                  <line
                    key={pct}
                    x1={0}
                    x2={100}
                    y1={Y(pct)}
                    y2={Y(pct)}
                    stroke="var(--border-default)"
                    strokeWidth={1}
                    strokeDasharray="2 3"
                    opacity={0.6}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
                <line
                  x1={0}
                  x2={100}
                  y1={39.5}
                  y2={39.5}
                  stroke="var(--border-default)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                <path d={cpuArea} fill={CPU_COLOR} opacity={0.12} />
                <polyline
                  points={cpuLine}
                  fill="none"
                  stroke={CPU_COLOR}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                />
                <polyline
                  points={memLine}
                  fill="none"
                  stroke={MEM_COLOR}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
              {/* Endpoint markers as HTML so the SVG's non-uniform scale can't squash them. */}
              {last && (
                <>
                  <span
                    aria-hidden
                    className="absolute h-2 w-2 rounded-full"
                    style={{
                      background: CPU_COLOR,
                      boxShadow: '0 0 0 2px var(--bg-surface)',
                      left: `calc(${X(n - 1, n)}% - 4px)`,
                      top: `calc(${(Y(last.cpuPct) / 40) * 100}% - 4px)`,
                    }}
                  />
                  <span
                    aria-hidden
                    className="absolute h-2 w-2 rounded-full"
                    style={{
                      background: MEM_COLOR,
                      boxShadow: '0 0 0 2px var(--bg-surface)',
                      left: `calc(${X(n - 1, n)}% - 4px)`,
                      top: `calc(${(Y(last.memPct) / 40) * 100}% - 4px)`,
                    }}
                  />
                </>
              )}
              {/* Hover columns: native titles, hit target = the full column height. */}
              <div className="absolute inset-0 flex">
                {Array.from({ length: POINT_SLOTS }, (_, slot) => {
                  const p = slot >= POINT_SLOTS - n ? points[slot - (POINT_SLOTS - n)] : null;
                  return (
                    <span
                      key={slot}
                      title={
                        p
                          ? `${fmtClock(p.ts)} — CPU ${p.cpuPct.toFixed(1)}% · MEM ${p.memPct.toFixed(1)}%`
                          : undefined
                      }
                      className="h-full min-w-0 flex-1"
                    />
                  );
                })}
              </div>
            </>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center rounded border border-dashed border-[var(--border-default)]">
              <span className="animate-pulse text-xs text-[var(--text-muted)]">
                Collecting first samples…
              </span>
            </div>
          )}
        </div>

        {/* Right gutter: current-value direct labels (text tokens carry the
            values; the colored dot carries identity). */}
        <div className="relative h-44 w-20 flex-shrink-0">
          {last && labels && (
            <>
              <span
                className="absolute left-2 flex -translate-y-1/2 items-center gap-1.5 whitespace-nowrap font-mono text-[11px] font-semibold leading-none text-[var(--text-primary)]"
                style={{ top: `${labels.cpuTop}%` }}
              >
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: CPU_COLOR }} />
                {last.cpuPct.toFixed(0)}%
              </span>
              <span
                className="absolute left-2 flex -translate-y-1/2 items-center gap-1.5 whitespace-nowrap font-mono text-[11px] font-semibold leading-none text-[var(--text-primary)]"
                style={{ top: `${labels.memTop}%` }}
              >
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: MEM_COLOR }} />
                {last.memPct.toFixed(0)}%
              </span>
            </>
          )}
        </div>
      </div>

      <div className="ml-8 mr-20 mt-1 flex justify-between font-mono text-[9px] leading-none text-[var(--text-tertiary)]">
        <span>−{windowMinutes} min</span>
        <span>now</span>
      </div>
    </div>
  );
}
