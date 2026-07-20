import type { CSSProperties } from 'react';

export type OrbState = 'idle' | 'thinking' | 'tool' | 'error';

/** The agent's visual identity: the Dataiku bird inside a living halo whose
 * palette and tempo track what the agent is doing (idle breathe / thinking
 * swirl / tool amber flare / error red). The bird itself never rotates — only
 * the aura layers behind it spin, while the whole mark breathes. Pure CSS
 * animation — state drives `data-state`, and the blur/glow radii scale with
 * the rendered size via custom props. */
export function AgentOrb({
  size = 24,
  state = 'idle',
  className = '',
}: {
  size?: number;
  state?: OrbState;
  className?: string;
}) {
  return (
    <span
      className={`agent-orb ${className}`}
      data-state={state}
      aria-hidden="true"
      style={
        {
          width: size,
          height: size,
          '--orb-blur': `${Math.max(1.5, size * 0.08)}px`,
          '--orb-glow-size': `${Math.round(size * 0.55)}px`,
        } as CSSProperties
      }
    >
      {/* Dataiku bird — inlined from assets/dataiku-bird-logo.svg (no svgr). */}
      <svg className="agent-orb-bird" viewBox="0 0 71 71" shapeRendering="geometricPrecision">
        <path fill="currentColor" d="M68.2984 48.7227H38.7461V54.1448H68.2984V48.7227Z" />
        <path
          fill="currentColor"
          d="M65.5439 4.61992C64.0076 1.87173 61.0335 0 57.6053 0C52.6105 0 48.5617 3.97372 48.5617 8.87589C48.5617 9.34382 48.6071 9.7969 48.6828 10.2425L47.9184 11.1636L0.150326 69.0017C-0.0842763 69.2839 -0.0388693 69.6998 0.248708 69.9301C0.513582 70.1381 0.899541 70.1232 1.14171 69.8855L21.2494 50.1729C24.9577 46.5408 29.9827 44.4983 35.2272 44.4983H42.2274C57.522 44.4983 66.7169 35.9492 65.052 18.3831C64.4768 12.3371 64.7947 9.78947 66.9061 7.2344C67.9959 5.91973 70.1073 3.34981 70.1073 3.34981L67.6326 4.03314L65.5363 4.61249L65.5439 4.61992ZM57.7944 9.96773C56.4398 9.96773 55.3425 8.89074 55.3425 7.56121C55.3425 6.23169 56.4398 5.1547 57.7944 5.1547C59.1491 5.1547 60.2464 6.23169 60.2464 7.56121C60.2464 8.89074 59.1491 9.96773 57.7944 9.96773Z"
        />
      </svg>
    </span>
  );
}
