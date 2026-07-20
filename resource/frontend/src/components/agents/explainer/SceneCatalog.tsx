import { ExplainerScene } from './ExplainerScene';
import { BoundaryBox } from './primitives';
import { CATALOG, CATALOG_EXCLUDED, FACTS } from './content';
import { RollingNumber } from '../../common/RollingNumber';
import type { SceneApi } from './useSceneSteps';

/** Scene 2 — the fixed capability menu. Counters roll up on first reveal,
 * tiers split by risk, the permanently-excluded list sits struck-through
 * outside the boundary, and the unplug demo shows a disabled sensor being
 * dropped before the tool list is even built. */

const TIERS = [
  { key: 'green', n: FACTS.tierGreen, label: 'green', desc: 'routine' },
  { key: 'amber', n: FACTS.tierAmber, label: 'amber', desc: 'changes state' },
  { key: 'red', n: FACTS.tierRed, label: 'red', desc: 'destructive' },
] as const;

function CatalogDiagram({ api }: { api: SceneApi }) {
  const { step, revealed } = api;
  const counted = revealed;
  // The unplug demo: at step 3 the sensor is switched off — it leaves the
  // bound-tool count entirely (11 → 10), it is not "refused", it is absent.
  const unplugged = step === 3;

  return (
    <div className="agx-catalog p-4 sm:p-6">
      <div className="agx-cat-grid">
        <BoundaryBox label="The catalog" marching className="agx-cat-box">
          <div className="agx-cat-counters" data-zone data-hot={step === 0 || undefined}>
            <div className="agx-cat-counter">
              <RollingNumber
                value={counted ? FACTS.sensors : 0}
                className="text-3xl font-bold text-[var(--text-primary)]"
              />
              <span className="agx-cat-counter-label">read-only sensors</span>
            </div>
            <div className="agx-cat-counter">
              <RollingNumber
                value={counted ? FACTS.actions : 0}
                className="text-3xl font-bold text-[var(--text-primary)]"
              />
              <span className="agx-cat-counter-label">admin actions</span>
            </div>
          </div>

          <div className="agx-cat-tiers" data-zone data-hot={step === 1 || undefined}>
            {TIERS.map((t) => (
              <div key={t.key} className="agx-cat-tier" data-tier={t.key}>
                <span className="agx-cat-tier-dot" aria-hidden="true" />
                <RollingNumber
                  value={counted ? t.n : 0}
                  className="text-lg font-bold text-[var(--text-primary)]"
                />
                <span className="agx-cat-tier-name">{t.label}</span>
                <span className="agx-cat-tier-desc">{t.desc}</span>
              </div>
            ))}
          </div>

          <div className="agx-cat-unplug" data-zone data-hot={step === 3 || undefined}>
            <span className="agx-cat-sensor" data-off={unplugged || undefined}>
              <input type="checkbox" checked={!unplugged} readOnly aria-label="log_errors enabled" />
              <code>log_errors</code>
            </span>
            <svg className="agx-cat-cable" viewBox="0 0 120 16" preserveAspectRatio="none" aria-hidden="true">
              <path className="agx-cat-cable-path" d="M 0 8 H 120" pathLength={100} data-cut={unplugged || undefined} />
            </svg>
            <span className="agx-cat-socket">
              <span className="agx-cat-socket-label">tools bound to the model</span>
              <RollingNumber
                value={counted ? (unplugged ? FACTS.sensors - 1 : FACTS.sensors) : 0}
                className="text-base font-bold text-[var(--text-primary)]"
              />
            </span>
            <span className="agx-cat-unplug-note" data-visible={unplugged || undefined}>
              never even bound — the model can’t see it
            </span>
          </div>
        </BoundaryBox>

        <div className="agx-cat-outside" data-zone data-hot={step === 2 || undefined}>
          <div className="agx-cat-outside-title">Outside the wall</div>
          <div className="agx-cat-outside-sub">structural, not policy — no switch turns these on</div>
          <ul className="agx-cat-outside-list">
            {CATALOG_EXCLUDED.map((x, i) => (
              <li key={x} style={{ '--agx-i': i } as React.CSSProperties}>
                {x}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export function SceneCatalog() {
  return (
    <ExplainerScene
      sceneClass="agx-s-catalog"
      eyebrow={CATALOG.eyebrow}
      title={CATALOG.title}
      intro={CATALOG.intro}
      steps={CATALOG.steps}
    >
      {(api) => <CatalogDiagram api={api} />}
    </ExplainerScene>
  );
}
