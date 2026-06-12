import type { ReactNode } from 'react';
import { RollingNumber } from '../../common/RollingNumber';
import { formatAuto } from '../../../utils/formatters';
import { TONE_COLOR, type Tone } from './tokens';

// Pure CSS/SVG micro-visualization primitives for the Mission Control wall.
// Zero chart.js: every tile must stay canvas-free so 20 tiles render as cheap
// DOM/SVG and repaint independently.

export type { Tone } from './tokens';

export function BigStat({
  value,
  label,
  sub,
  tone,
}: {
  value: string | number;
  label: string;
  sub?: string;
  tone?: Tone;
}) {
  return (
    <div className="min-w-0">
      <div
        className="font-mono font-semibold leading-none text-[var(--text-primary)] whitespace-nowrap"
        style={{
          fontSize: 'clamp(15px, 1.3vw, 22px)',
          color: tone ? TONE_COLOR[tone] : undefined,
        }}
      >
        <RollingNumber value={value} />
        {sub && (
          <span className="ml-1 text-[10px] font-normal text-[var(--text-tertiary)]">{sub}</span>
        )}
      </div>
      <div className="mt-1 truncate text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
        {label}
      </div>
    </div>
  );
}

export function CountChip({
  label,
  count,
  tone = 'neutral',
  pulse = false,
}: {
  label: string;
  count: number | string;
  tone?: Tone;
  pulse?: boolean;
}) {
  const color = TONE_COLOR[tone];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] leading-none whitespace-nowrap ${pulse ? 'animate-pulse' : ''}`}
      style={{
        color,
        borderColor: `color-mix(in srgb, ${color} 35%, transparent)`,
        background: `color-mix(in srgb, ${color} 10%, transparent)`,
      }}
    >
      <span className="font-semibold">{count}</span>
      <span className="opacity-80">{label}</span>
    </span>
  );
}

export function Dot({ tone, title }: { tone: Tone; title?: string }) {
  return (
    <span
      title={title}
      className="inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full"
      style={{ background: TONE_COLOR[tone], opacity: tone === 'neutral' ? 0.4 : 1 }}
    />
  );
}

export function BarRow({
  label,
  value,
  pct,
  tone = 'info',
  onClick,
}: {
  label: ReactNode;
  value: string;
  pct: number;
  tone?: Tone;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="min-w-0 flex-1 truncate text-left text-[11px] text-[var(--text-secondary)]">
        {label}
      </span>
      <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
        <span
          className="block h-full rounded-full"
          style={{
            width: `${Math.min(100, Math.max(0, pct))}%`,
            background: TONE_COLOR[tone],
          }}
        />
      </span>
      <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)] truncate">
        {value}
      </span>
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        className="flex w-full items-center gap-2 rounded px-0.5 -mx-0.5 hover:bg-[var(--bg-hover)] transition-colors"
      >
        {body}
      </button>
    );
  }
  return <div className="flex w-full items-center gap-2">{body}</div>;
}

export function UsageBar({ pct, tone = 'info' }: { pct: number; tone?: Tone }) {
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--bg-elevated)]">
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{
          width: `${Math.min(100, Math.max(0, pct))}%`,
          background: TONE_COLOR[tone],
        }}
      />
    </div>
  );
}

export interface Segment {
  value: number;
  color: string;
  title?: string;
}

export function SegmentBar({ segments, height = 5 }: { segments: Segment[]; height?: number }) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  return (
    <div className="flex w-full overflow-hidden rounded-full bg-[var(--bg-elevated)]" style={{ height }}>
      {segments
        .filter((s) => s.value > 0)
        .map((s, i) => (
          <div
            key={i}
            title={s.title}
            style={{ width: `${(s.value / total) * 100}%`, background: s.color }}
          />
        ))}
    </div>
  );
}

export function MicroDonut({
  segments,
  size = 84,
  thickness = 9,
  center,
  centerLabel,
}: {
  segments: Segment[];
  size?: number;
  thickness?: number;
  center?: string | number;
  centerLabel?: string;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="relative flex-shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--bg-elevated)"
          strokeWidth={thickness}
        />
        {total > 0 &&
          segments.map((s, i) => {
            const dash = (s.value / total) * c;
            const el = (
              <circle
                key={i}
                cx={size / 2}
                cy={size / 2}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth={thickness}
                strokeDasharray={`${dash} ${c - dash}`}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              >
                {s.title && <title>{s.title}</title>}
              </circle>
            );
            offset += dash;
            return el;
          })}
      </svg>
      {(center !== undefined || centerLabel) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {center !== undefined && (
            <span className="font-mono text-sm font-semibold leading-none text-[var(--text-primary)]">
              <RollingNumber value={center} />
            </span>
          )}
          {centerLabel && (
            <span className="mt-0.5 text-[8px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
              {centerLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function ScoreRing({
  score,
  color,
  size = 84,
  label,
}: {
  score: number;
  color: string;
  size?: number;
  label?: string;
}) {
  const thickness = 7;
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const dash = (Math.min(100, Math.max(0, score)) / 100) * c;
  return (
    <div className="relative flex-shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--bg-elevated)"
          strokeWidth={thickness}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c - dash}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className="transition-all duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-mono text-xl font-semibold leading-none"
          style={{ color }}
        >
          <RollingNumber value={score} />
        </span>
        {label && (
          <span className="mt-0.5 text-[8px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
            {label}
          </span>
        )}
      </div>
    </div>
  );
}

export function HeatStrip({
  cells,
  max = 28,
}: {
  cells: { tone: Tone; title?: string }[];
  max?: number;
}) {
  const shown = cells.slice(0, max);
  return (
    <div className="flex flex-wrap items-center gap-[3px]">
      {shown.map((cell, i) => (
        <span
          key={i}
          title={cell.title}
          className="h-2 w-2 rounded-[2px]"
          style={{
            background: TONE_COLOR[cell.tone],
            opacity: cell.tone === 'neutral' ? 0.35 : 0.85,
          }}
        />
      ))}
      {cells.length > max && (
        <span className="font-mono text-[9px] text-[var(--text-tertiary)]">
          +{cells.length - max}
        </span>
      )}
    </div>
  );
}

export interface TreemapItem {
  name: string;
  size: number;
}

// One-level treemap: items (sorted desc) are greedily split into two rows of
// roughly equal total, each row laying out proportional widths via flex-grow.
export function MiniTreemap({ items }: { items: TreemapItem[] }) {
  if (items.length === 0) return null;
  const sorted = [...items].sort((a, b) => b.size - a.size);
  const max = sorted[0].size || 1;
  const rows: TreemapItem[][] = [[], []];
  const totals = [0, 0];
  for (const it of sorted) {
    const target = totals[0] <= totals[1] ? 0 : 1;
    rows[target].push(it);
    totals[target] += it.size;
  }
  return (
    <div className="flex h-full min-h-0 flex-col gap-[3px]">
      {rows
        .filter((row) => row.length > 0)
        .map((row, ri) => (
          <div key={ri} className="flex min-h-0 flex-1 gap-[3px]">
            {row.map((it) => (
              <div
                key={it.name}
                title={`${it.name} — ${formatAuto(it.size)}`}
                className="relative min-w-0 overflow-hidden rounded-[3px] border border-[var(--border-default)]"
                style={{
                  flexGrow: Math.max(it.size, max * 0.04),
                  flexBasis: 0,
                  background: `color-mix(in srgb, var(--accent) ${Math.round(6 + 20 * (it.size / max))}%, transparent)`,
                }}
              >
                <span className="absolute inset-x-1 top-0.5 truncate text-[9px] leading-tight text-[var(--text-secondary)]">
                  {it.name}
                </span>
                <span className="absolute inset-x-1 bottom-0.5 truncate font-mono text-[8px] leading-tight text-[var(--text-tertiary)]">
                  {formatAuto(it.size)}
                </span>
              </div>
            ))}
          </div>
        ))}
    </div>
  );
}
