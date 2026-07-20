import { type CSSProperties } from 'react';
import { ExplainerScene } from './ExplainerScene';
import { MiniGauntlet } from './SceneGauntlet';
import { AUTONOMY, FACTS } from './content';
import { RollingNumber } from '../../common/RollingNumber';
import type { SceneApi } from './useSceneSteps';

/** Scene 7 — the nightly tier. Deterministic score flags hosts, the planner
 * proposes within hard caps, a 6-check code re-validator filters, and
 * everything funnels into the SAME execute gauntlet — under budgets, a pause
 * lever, and a morning digest. Host names/scores are illustrative; the caps
 * and checks are the real constants. */

const HOSTS = [
  { name: 'dss-design', score: 82 },
  { name: 'dss-auto', score: 62 },
  { name: 'dss-deploy', score: 91 },
] as const;

const CHECKS = [
  'catalogued action',
  'not python-run',
  'live Auto grant',
  'host flagged tonight',
  'not already handled',
  'budget headroom',
];

function Gauge({ score, filled }: { score: number; filled: boolean }) {
  return (
    <svg className="agx-au-gauge" viewBox="0 0 40 40" aria-hidden="true">
      <circle className="agx-au-gauge-track" cx="20" cy="20" r="16" pathLength={100} />
      <circle
        className="agx-au-gauge-fill"
        cx="20"
        cy="20"
        r="16"
        pathLength={100}
        data-low={score < FACTS.healthThreshold || undefined}
        style={{ strokeDasharray: `${filled ? score : 0} 100` } as CSSProperties}
      />
      {/* Threshold tick at 75. */}
      <line
        className="agx-au-gauge-tick"
        x1="20"
        y1="1.5"
        x2="20"
        y2="6.5"
        transform={`rotate(${(FACTS.healthThreshold / 100) * 360} 20 20)`}
      />
    </svg>
  );
}

function AutonomyDiagram({ api }: { api: SceneApi }) {
  const { step, live, revealed } = api;
  return (
    <div className="agx-autonomy p-4 sm:p-6">
      {/* Step 0 — deterministic scoring. */}
      <div className="agx-au-zone" data-zone data-hot={step === 0 || undefined}>
        <div className="agx-sb-zone-title">
          fixed-weight health score · flags &lt; {FACTS.healthThreshold} · no LLM in the ranking
        </div>
        <div className="agx-au-hosts">
          {HOSTS.map((h) => (
            <div
              key={h.name}
              className="agx-au-host"
              data-flagged={h.score < FACTS.healthThreshold || undefined}
            >
              <Gauge score={h.score} filled={revealed} />
              <span className="agx-au-host-name">{h.name}</span>
              <RollingNumber
                value={revealed ? h.score : 0}
                className="text-lg font-bold text-[var(--text-primary)]"
              />
              <span className="agx-au-host-tag">flagged</span>
            </div>
          ))}
        </div>
      </div>

      {/* Step 1 — planner caps + the 6-check re-validator. */}
      <div className="agx-au-zone" data-zone data-hot={step === 1 || undefined}>
        <div className="agx-au-planner-row">
          <div className="agx-au-planner">
            <div className="agx-au-planner-title">triage planner</div>
            <div className="agx-au-planner-caps">
              ≤ {FACTS.plannerMaxTurns} turns · ≤ {FACTS.plannerMaxProposals} proposals
            </div>
            <span className="agx-chip agx-au-proposal" data-tone="active">
              log-cleanup @ dss-auto
            </span>
          </div>
          <ul className="agx-au-checks">
            {CHECKS.map((c, i) => (
              <li key={c} style={{ '--agx-i': i } as CSSProperties}>
                <span className="agx-au-check-mark" aria-hidden="true">
                  ✓
                </span>
                {c}
              </li>
            ))}
          </ul>
        </div>
        <div className="agx-sec-note">every proposal re-validated by code — not by the model</div>
      </div>

      {/* Step 2 — the same gauntlet. */}
      <div className="agx-au-zone" data-zone data-hot={step === 2 || undefined}>
        <div className="agx-tf-mini-label">the exact same gates you saw above</div>
        <MiniGauntlet live={live && step === 2} mode="auto" />
      </div>

      {/* Step 3 — budgets, pause, digest. */}
      <div className="agx-au-zone" data-zone data-hot={step === 3 || undefined}>
        <div className="agx-au-foot">
          <div className="agx-au-budgets">
            <div className="agx-au-meter">
              <span className="agx-au-meter-label">freed tonight</span>
              <span className="agx-au-meter-track">
                <span
                  className="agx-au-meter-fill"
                  style={{ width: step === 3 ? `${(13.2 / FACTS.budgetGb) * 100}%` : '0%' }}
                />
              </span>
              <span className="agx-au-meter-value">13.2 / {FACTS.budgetGb} GB</span>
            </div>
            <div className="agx-au-meter">
              <span className="agx-au-meter-label">objects touched</span>
              <span className="agx-au-meter-track">
                <span
                  className="agx-au-meter-fill"
                  style={{ width: step === 3 ? `${(18 / FACTS.budgetObjects) * 100}%` : '0%' }}
                />
              </span>
              <span className="agx-au-meter-value">18 / {FACTS.budgetObjects}</span>
            </div>
          </div>
          <label className="agx-au-pause">
            <input type="checkbox" checked readOnly className="h-[15px] w-[15px] accent-[var(--accent)]" />
            <span>
              <span className="agx-tf-flag-name">auto_remediate_enabled</span>
              <span className="agx-tf-flag-sub">one switch pauses the whole tier</span>
            </span>
          </label>
          <div className="agx-au-digest">
            <div className="agx-au-digest-title">{FACTS.digestHour} · morning digest</div>
            <div className="agx-au-digest-body">
              everything executed — and everything merely proposed — in one email
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SceneAutonomy() {
  return (
    <ExplainerScene
      sceneClass="agx-s-autonomy"
      eyebrow={AUTONOMY.eyebrow}
      title={AUTONOMY.title}
      intro={AUTONOMY.intro}
      steps={AUTONOMY.steps}
    >
      {(api) => <AutonomyDiagram api={api} />}
    </ExplainerScene>
  );
}
