import { useState, type CSSProperties } from 'react';
import { ExplainerScene } from './ExplainerScene';
import { MiniGauntlet } from './SceneGauntlet';
import { TWO_FLAGS, FACTS } from './content';
import type { SceneApi } from './useSceneSteps';

/** Scene 4 — the two-flag permission model. REAL toggles (same controls as
 * the Permissions page) drive the outcome grid and re-route the mini gauntlet
 * live. Steps preset the flags; the user can flip them freely — an override
 * lives only within its step, so stepping always restores the narration. */

const PRESETS = [
  { enabled: false, auto: false },
  { enabled: true, auto: false },
  { enabled: true, auto: true },
  { enabled: true, auto: false }, // python-run: Auto is structurally off
] as const;

const FOUR_LAYERS = [
  'the API refuses the grant (400)',
  'the gate reader hard-floors it to off',
  'the nightly candidate list subtracts it',
  'the planner refuses to propose it',
];

function TwoFlagsDiagram({ api }: { api: SceneApi }) {
  const { step, live } = api;
  const [override, setOverride] = useState<{
    step: number;
    enabled: boolean;
    auto: boolean;
  } | null>(null);

  const preset = PRESETS[step] ?? PRESETS[0];
  const active = override?.step === step ? override : preset;
  const pythonRun = step === 3;
  const enabled = active.enabled;
  const auto = pythonRun ? false : active.auto;
  const mode: 'blocked' | 'ask' | 'auto' = !enabled ? 'blocked' : auto ? 'auto' : 'ask';

  // Mirror the backend's normalization: Auto implies Enabled, and dropping
  // Enabled drops Auto with it (routes/agent_gates.py).
  const toggleEnabled = () => {
    const e = !enabled;
    setOverride({ step, enabled: e, auto: e ? auto : false });
  };
  const toggleAuto = () => {
    if (pythonRun) return;
    const a = !auto;
    setOverride({ step, enabled: a ? true : enabled, auto: a });
  };

  const row = enabled ? 1 : 0;
  const col = auto ? 1 : 0;

  return (
    <div className="agx-twoflags p-4 sm:p-6">
      <div className="agx-tf-top">
        {/* The control card — same two checkboxes as the Permissions page. */}
        <div className="agx-tf-controls">
          <div className="agx-tf-action">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: pythonRun ? 'var(--agx-danger)' : 'var(--agx-warn)' }}
            />
            <code>{pythonRun ? 'python-run' : 'log-cleanup'}</code>
          </div>
          <label className="agx-tf-flag">
            <input
              type="checkbox"
              checked={enabled}
              onChange={toggleEnabled}
              className="h-[17px] w-[17px] accent-[var(--accent)] cursor-pointer"
            />
            <span>
              <span className="agx-tf-flag-name">Enabled</span>
              <span className="agx-tf-flag-sub">may be planned at all</span>
            </span>
          </label>
          <label className="agx-tf-flag" data-locked={pythonRun || undefined}>
            <input
              type="checkbox"
              checked={auto}
              disabled={pythonRun}
              onChange={toggleAuto}
              title={
                pythonRun
                  ? 'python-run can never run autonomously — every run requires a human acknowledgment.'
                  : undefined
              }
              className="h-[17px] w-[17px] accent-[var(--accent)] cursor-pointer disabled:cursor-not-allowed disabled:opacity-30"
            />
            <span>
              <span className="agx-tf-flag-name">Auto</span>
              <span className="agx-tf-flag-sub">nightly tier may run it</span>
            </span>
          </label>
          <div className="agx-tf-defaults">
            Fail-closed defaults: all {FACTS.actions} actions ship Blocked; the {FACTS.sensors}{' '}
            sensors ship on.
          </div>
        </div>

        {/* Outcome grid with sliding highlight. */}
        <div
          className="agx-tf-grid"
          style={{ '--agx-row': row, '--agx-col': col } as CSSProperties}
        >
          <span className="agx-tf-hl" aria-hidden="true" />
          <div className="agx-tf-cell" data-kind="blocked" data-on={row === 0 && col === 0 ? '' : undefined}>
            <span className="agx-tf-cell-title">Blocked</span>
            <span className="agx-tf-cell-sub">cannot even be planned</span>
          </div>
          <div className="agx-tf-cell" data-kind="unreachable">
            <span className="agx-tf-cell-title">Unreachable</span>
            <span className="agx-tf-cell-sub">Auto forces Enabled on</span>
          </div>
          <div className="agx-tf-cell" data-kind="ask" data-on={row === 1 && col === 0 ? '' : undefined}>
            <span className="agx-tf-cell-title">Ask first</span>
            <span className="agx-tf-cell-sub">you approve every run</span>
          </div>
          <div className="agx-tf-cell" data-kind="auto" data-on={row === 1 && col === 1 ? '' : undefined}>
            <span className="agx-tf-cell-title">Auto-run</span>
            <span className="agx-tf-cell-sub">nightly, same gauntlet</span>
          </div>
        </div>
      </div>

      <div className="agx-tf-mini">
        <div className="agx-tf-mini-label">the execute gauntlet, re-routed live</div>
        <MiniGauntlet live={live} mode={mode} />
      </div>

      <div className="agx-tf-layers" data-visible={pythonRun || undefined}>
        <span className="agx-tf-layers-title">python-run’s Auto is off at 4 layers:</span>
        {FOUR_LAYERS.map((l, i) => (
          <span key={l} className="agx-tf-layer" style={{ '--agx-i': i } as CSSProperties}>
            {l}
          </span>
        ))}
      </div>
    </div>
  );
}

export function SceneTwoFlags() {
  return (
    <ExplainerScene
      sceneClass="agx-s-twoflags"
      eyebrow={TWO_FLAGS.eyebrow}
      title={TWO_FLAGS.title}
      intro={TWO_FLAGS.intro}
      steps={TWO_FLAGS.steps}
    >
      {(api) => <TwoFlagsDiagram api={api} />}
    </ExplainerScene>
  );
}
