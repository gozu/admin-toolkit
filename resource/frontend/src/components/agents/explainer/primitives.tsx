import type { CSSProperties, ReactNode, Ref } from 'react';

/** Shared visual vocabulary for the explainer diagrams. All appearance lives
 * in explainer.css under the `agx-` prefix; these components only set the
 * structural markup + data attributes the CSS keys on. */

export type Tone = 'neutral' | 'active' | 'ok' | 'warn' | 'danger';

/* ------------------------------------------------------------------ chip -- */

/** The protagonist: a glowing request chip that travels through diagrams.
 * Movement is WAAPI `offset-path` driven by the owning scene (see
 * `animateAlongPath`); the chip itself is just the styled capsule. */
export function OffsetChip({
  ref,
  children,
  tone = 'active',
  className = '',
  style,
}: {
  ref?: Ref<HTMLSpanElement>;
  children?: ReactNode;
  tone?: Tone;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span ref={ref} className={`agx-chip ${className}`} data-tone={tone} style={style}>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ beam -- */

/** Animated connector: a quiet base stroke plus a glowing dash segment that
 * flows along the same path (CSS stroke-dashoffset keyframes — pauses via
 * `animation-play-state` when the scene goes offscreen, which SMIL cannot).
 * `pathLength=100` normalizes the dash metrics for any geometry. */
export function BeamConnector({
  d,
  viewBox,
  tone = 'active',
  active = true,
  className = '',
  preserveAspectRatio = 'none',
}: {
  d: string;
  viewBox: string;
  tone?: Tone;
  active?: boolean;
  className?: string;
  preserveAspectRatio?: string;
}) {
  return (
    <svg
      className={`agx-beam ${className}`}
      viewBox={viewBox}
      preserveAspectRatio={preserveAspectRatio}
      fill="none"
      aria-hidden="true"
    >
      <path className="agx-beam-base" d={d} pathLength={100} />
      <path
        className="agx-beam-glow"
        d={d}
        pathLength={100}
        data-tone={tone}
        data-active={active || undefined}
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ gate -- */

export type GateState = 'idle' | 'active' | 'pass' | 'fail' | 'off';

/** One checkpoint in a gauntlet: index, lamp, label, optional annotation
 * badge (e.g. "30s cache"). State drives color + lamp via CSS. */
export function GateNode({
  index,
  label,
  sub,
  badge,
  state = 'idle',
  className = '',
}: {
  index: number;
  label: string;
  sub?: string;
  badge?: string;
  state?: GateState;
  className?: string;
}) {
  return (
    <div className={`agx-gate ${className}`} data-state={state}>
      <span className="agx-gate-lamp" aria-hidden="true" />
      <span className="agx-gate-index">{index}</span>
      <span className="agx-gate-text">
        <span className="agx-gate-label">{label}</span>
        {sub && <span className="agx-gate-sub">{sub}</span>}
      </span>
      {badge && <span className="agx-gate-badge">{badge}</span>}
    </div>
  );
}

/* -------------------------------------------------------------- boundary -- */

/** Dashed enclosure marking a trust boundary. `marching` animates the dashes
 * (marching ants) while the scene is live; `label` sits on the top edge. */
export function BoundaryBox({
  label,
  tone = 'neutral',
  marching = false,
  className = '',
  children,
}: {
  label?: string;
  tone?: Tone;
  marching?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`agx-boundary ${className}`} data-tone={tone}>
      <svg className="agx-boundary-edge" aria-hidden="true" preserveAspectRatio="none">
        <rect
          className="agx-boundary-rect"
          data-marching={marching || undefined}
          x="1"
          y="1"
          rx="10"
          pathLength={100}
        />
      </svg>
      {label && <span className="agx-boundary-label">{label}</span>}
      {children}
    </div>
  );
}

/* ------------------------------------------------------------- step dots -- */

export function StepDots({
  count,
  active,
  onSelect,
}: {
  count: number;
  active: number;
  onSelect: (i: number) => void;
}) {
  return (
    <span className="agx-dots" role="tablist" aria-label="Scene steps">
      {Array.from({ length: count }, (_, i) => (
        <button
          key={i}
          type="button"
          role="tab"
          aria-selected={i === active}
          aria-label={`Step ${i + 1}`}
          className="agx-dot"
          data-active={i === active || undefined}
          onClick={() => onSelect(i)}
        />
      ))}
    </span>
  );
}
