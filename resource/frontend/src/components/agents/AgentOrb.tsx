import type { CSSProperties } from 'react';

export type OrbState = 'idle' | 'thinking' | 'tool' | 'error';

/** The agent's visual identity: a conic-swirl orb whose palette and tempo
 * track what the agent is doing (idle breathe / thinking swirl / tool amber
 * flare / error red). Pure CSS animation — state drives `data-state`, and the
 * blur/glow radii scale with the rendered size via custom props. */
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
      <span className="agent-orb-core" />
    </span>
  );
}
