import { useEffect, useState, type CSSProperties } from 'react';
import { ExplainerScene } from './ExplainerScene';
import { BeamConnector, GateNode, type GateState } from './primitives';
import { GAUNTLET, GAUNTLET_GATES } from './content';
import type { SceneApi } from './useSceneSteps';

/** Scene 3 — the execute-time gauntlet. One request dot descends through the
 * seven real checkpoints of execute_admin_action, in true enforcement order.
 * Steps replay three scenarios: happy path, kill-switch flipped (dies at
 * gate 2), drifted target (ticket void at gate 5). */

const BEAT_MS = 620;
const HOLD_MS = 2400;
const GATE_COUNT = GAUNTLET_GATES.length;

interface Scenario {
  /** Gate index the run dies at; -1 = passes everything. */
  failAt: number;
  /** Gate rendered switched-off from the start (kill-switch scenario). */
  offGate?: number;
  outcome: string;
  outcomeKind: 'ok' | 'danger';
}

const SCENARIOS: readonly Scenario[] = [
  { failAt: -1, outcome: '✓ Executed — audit row written', outcomeKind: 'ok' },
  {
    failAt: 1,
    offGate: 1,
    outcome: 'Blocked — the kill-switch is off',
    outcomeKind: 'danger',
  },
  {
    failAt: 4,
    outcome: 'Ticket void — target changed since planning',
    outcomeKind: 'danger',
  },
];

function gateStateFor(i: number, phase: number, sc: Scenario): GateState {
  if (sc.failAt === i && phase > i) return 'fail';
  if (sc.offGate === i && phase <= i) return 'off';
  if (phase > i) return 'pass';
  if (phase === i) return 'active';
  return 'idle';
}

function GauntletDiagram({ api }: { api: SceneApi }) {
  const { step, live, revealed } = api;
  const [phase, setPhase] = useState(-1);
  const [cycle, setCycle] = useState(0);

  const sc = SCENARIOS[step] ?? SCENARIOS[0];

  // Beat clock: advance one gate per beat, hold the outcome, loop. Everything
  // is timeouts (cleared on step change / scroll-out / unmount) — the gates
  // themselves are pure CSS transitions on data-state.
  useEffect(() => {
    if (!live || !revealed) return;
    const scenario = SCENARIOS[step] ?? SCENARIOS[0];
    const last = scenario.failAt >= 0 ? scenario.failAt + 1 : GATE_COUNT + 1;
    const timeouts: number[] = [window.setTimeout(() => setPhase(0), 60)];
    for (let p = 1; p <= last; p++) {
      timeouts.push(window.setTimeout(() => setPhase(p), 320 + p * BEAT_MS));
    }
    timeouts.push(
      window.setTimeout(() => setCycle((c) => c + 1), 320 + last * BEAT_MS + HOLD_MS),
    );
    return () => timeouts.forEach((t) => window.clearTimeout(t));
  }, [step, live, revealed, cycle]);

  const finished =
    sc.failAt >= 0 ? phase > sc.failAt : phase > GATE_COUNT;
  const pos =
    phase < 0
      ? 0
      : sc.failAt >= 0
        ? Math.min(phase, sc.failAt)
        : Math.min(phase, GATE_COUNT);
  const chipTone = finished ? (sc.outcomeKind === 'ok' ? 'ok' : 'danger') : 'active';

  return (
    <div
      className="agx-gauntlet"
      style={{ '--agx-pos': pos } as CSSProperties}
      data-finished={finished || undefined}
    >
      <div className="agx-gauntlet-rail" aria-hidden="true">
        <BeamConnector
          d="M 8 0 L 8 1000"
          viewBox="0 0 16 1000"
          active={live && !finished}
          className="agx-gauntlet-beam"
        />
        <span className="agx-chip-dot" data-tone={chipTone} />
      </div>
      <div className="agx-gauntlet-gates">
        {GAUNTLET_GATES.map((g, i) => (
          <GateNode
            key={g.label}
            index={i + 1}
            label={g.label}
            sub={g.sub}
            badge={'badge' in g ? g.badge : undefined}
            state={gateStateFor(i, phase, sc)}
          />
        ))}
        <div
          className="agx-gauntlet-outcome"
          data-kind={sc.outcomeKind}
          data-visible={finished || undefined}
        >
          {sc.outcome}
        </div>
      </div>
    </div>
  );
}

export function SceneGauntlet() {
  return (
    <ExplainerScene
      sceneClass="agx-s-gauntlet"
      eyebrow={GAUNTLET.eyebrow}
      title={GAUNTLET.title}
      intro={GAUNTLET.intro}
      steps={GAUNTLET.steps}
    >
      {(api) => (
        <div className="p-4 sm:p-6">
          <GauntletDiagram api={api} />
        </div>
      )}
    </ExplainerScene>
  );
}

/* ---------------------------------------------------------------- mini -- */

const MINI_LABELS = [
  'catalog',
  'kill-switch',
  'enabled',
  'confirm',
  'ticket',
  'policy',
  'audit',
] as const;

type MiniState = 'idle' | 'active' | 'pass' | 'fail' | 'wait';

/** Compact horizontal gauntlet — the visual callback reused by the two-flags
 * and autonomy scenes. `mode` decides the route:
 *  - 'blocked': dies at the Enabled gate (flag off)
 *  - 'ask':     pauses yellow at Human confirm (waiting for you), then passes
 *  - 'auto':    passes straight through (standing Auto grant satisfies confirm)
 */
export function MiniGauntlet({ live, mode }: { live: boolean; mode: 'blocked' | 'ask' | 'auto' }) {
  const [phase, setPhase] = useState(-1);
  const [cycle, setCycle] = useState(0);

  useEffect(() => {
    if (!live) return;
    const beat = 420;
    const failAt = mode === 'blocked' ? 2 : -1;
    const waitAt = mode === 'ask' ? 3 : -1;
    const last = failAt >= 0 ? failAt + 1 : MINI_LABELS.length;
    const timeouts: number[] = [window.setTimeout(() => setPhase(0), 60)];
    let t = 200;
    for (let p = 1; p <= last; p++) {
      t += beat + (waitAt >= 0 && p === waitAt + 1 ? 1100 : 0); // linger on confirm
      timeouts.push(window.setTimeout(() => setPhase(p), t));
    }
    timeouts.push(window.setTimeout(() => setCycle((c) => c + 1), t + 1800));
    return () => timeouts.forEach((id) => window.clearTimeout(id));
  }, [live, mode, cycle]);

  const failAt = mode === 'blocked' ? 2 : -1;
  const waitAt = mode === 'ask' ? 3 : -1;

  function miniState(i: number): MiniState {
    if (failAt === i && phase > i) return 'fail';
    if (phase === i && waitAt === i) return 'wait';
    if (phase > i) return 'pass';
    if (phase === i) return 'active';
    return 'idle';
  }

  return (
    <div className="agx-mini" data-mode={mode}>
      {MINI_LABELS.map((label, i) => (
        <span key={label} className="agx-mini-node" data-state={miniState(i)}>
          <span className="agx-mini-lamp" aria-hidden="true" />
          <span className="agx-mini-label">{label}</span>
        </span>
      ))}
    </div>
  );
}
