import { ExplainerScene } from './ExplainerScene';
import { BeamConnector, OffsetChip } from './primitives';
import { PLAN_LOOP, FACTS } from './content';
import { AgentOrb } from '../AgentOrb';

/** Scene 1 — the request path. One persistent diagram: You → SSE beam →
 * planner orb (tool loop, ≤12 iterations) → plan card; plus the ghost
 * LLM→Execute arrow that gets struck out. All state transitions are CSS keyed
 * on data-step; the chip glides between stations via left/top transitions. */

const SENSORS = ['instance_health', 'compute_cost', 'log_errors', 'config_inspect'];

function PlanLoopDiagram() {
  return (
    <div className="agx-planloop">
      {/* Beams: You→orb (SSE), orb→plan, and the ghost orb→execute arrow. */}
      <BeamConnector
        className="agx-pl-beam-sse"
        viewBox="0 0 900 320"
        d="M 100 130 H 348"
        active
      />
      <BeamConnector
        className="agx-pl-beam-plan"
        viewBox="0 0 900 320"
        d="M 452 130 H 668"
        active
      />
      <svg
        className="agx-pl-ghost"
        viewBox="0 0 900 320"
        preserveAspectRatio="none"
        fill="none"
        aria-hidden="true"
      >
        <path
          className="agx-pl-ghost-path"
          d="M 430 175 C 490 260, 570 268, 660 264"
          pathLength={100}
        />
        <line className="agx-pl-ghost-strike" x1="656" y1="284" x2="820" y2="244" pathLength={100} />
      </svg>

      {/* You */}
      <div className="agx-pl-node agx-pl-you">
        <span className="agx-pl-node-label">You</span>
      </div>

      {/* The traveling question */}
      <OffsetChip className="agx-pl-chip">your question</OffsetChip>

      {/* Planner orb + tool loop ring + sensor pings */}
      <div className="agx-pl-orb">
        <span className="agx-pl-ring" aria-hidden="true" />
        <AgentOrb size={46} state="thinking" />
        <span className="agx-pl-ring-label">≤ {FACTS.maxIterations} iterations</span>
        {SENSORS.map((s, i) => (
          <span key={s} className="agx-pl-sensor" data-i={i}>
            {s}
          </span>
        ))}
        <span className="agx-pl-readonly">read-only sensors</span>
      </div>

      {/* Plan card */}
      <div className="agx-pl-plan">
        <div className="agx-pl-plan-title">Plan · project-delete</div>
        <div className="agx-pl-plan-row">
          <span>target</span>
          <code>SANDBOX_OLD</code>
        </div>
        <div className="agx-pl-plan-row">
          <span>ticket</span>
          <code>hmac · {FACTS.tokenTtlMinutes} min</code>
        </div>
        <div className="agx-pl-plan-foot">nothing has happened yet</div>
      </div>

      {/* The struck-out direct-execute node */}
      <div className="agx-pl-exec">
        <span className="agx-pl-exec-label">Execute</span>
        <span className="agx-pl-exec-note">no single-call path</span>
      </div>
    </div>
  );
}

export function ScenePlanLoop() {
  return (
    <ExplainerScene
      sceneClass="agx-s-planloop"
      eyebrow={PLAN_LOOP.eyebrow}
      title={PLAN_LOOP.title}
      intro={PLAN_LOOP.intro}
      steps={PLAN_LOOP.steps}
    >
      {() => <PlanLoopDiagram />}
    </ExplainerScene>
  );
}
