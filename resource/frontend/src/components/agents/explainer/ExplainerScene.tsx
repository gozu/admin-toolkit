import type { ReactNode } from 'react';
import { useSceneSteps, type SceneApi } from './useSceneSteps';
import { StepDots } from './primitives';
import type { SceneStep } from './content';

/** Scene shell: a full-width diagram stage with a sticky step rail beside it.
 *
 * The rail holds the prose (capped for legibility — the page itself is
 * full-bleed); the stage holds ONE persistent diagram whose look is driven by
 * the section's `data-step` / `data-live` / `data-revealed` attributes (see
 * explainer.css). Steps advance by clicking a rail entry, the dots, or
 * Prev/Next — the diagram never unmounts between steps.
 */
export function ExplainerScene({
  sceneClass,
  eyebrow,
  title,
  intro,
  steps,
  children,
}: {
  /** Scene-scoping class for CSS state selectors, e.g. `agx-s-gauntlet`. */
  sceneClass: string;
  eyebrow?: string;
  title: string;
  intro: string;
  steps: readonly SceneStep[];
  children: (api: SceneApi) => ReactNode;
}) {
  const api = useSceneSteps(steps.length);
  // Destructured once: `bindSection` feeds the ref prop, so reading other
  // fields off `api` in JSX would trip the compiler lint's ref-flow analysis.
  const { step, live, revealed, bindSection, goTo, next, prev } = api;

  return (
    <section
      ref={bindSection}
      className={`agx-scene ${sceneClass}`}
      data-step={step}
      data-live={live ? '' : undefined}
      data-revealed={revealed ? '' : undefined}
    >
      <header className="max-w-[42rem] space-y-1.5">
        {eyebrow && (
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--accent)]">
            {eyebrow}
          </div>
        )}
        <h2 className="text-xl font-semibold text-[var(--text-primary)]">{title}</h2>
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">{intro}</p>
      </header>

      <div className="agx-scene-grid">
        <aside className="agx-rail">
          <ol className="agx-rail-steps">
            {steps.map((s, i) => (
              <li key={s.title}>
                <button
                  type="button"
                  className="agx-rail-step"
                  data-active={i === step || undefined}
                  data-done={i < step || undefined}
                  onClick={() => goTo(i)}
                >
                  <span className="agx-rail-step-marker" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="agx-rail-step-text">
                    <span className="agx-rail-step-title">{s.title}</span>
                    <span className="agx-rail-step-body">{s.body}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="agx-rail-controls">
            <StepDots count={steps.length} active={step} onSelect={goTo} />
            <span className="ml-auto inline-flex gap-1">
              <button
                type="button"
                className="agx-rail-btn"
                onClick={prev}
                disabled={step === 0}
                aria-label="Previous step"
              >
                ←
              </button>
              <button
                type="button"
                className="agx-rail-btn"
                onClick={next}
                disabled={step === steps.length - 1}
                aria-label="Next step"
              >
                →
              </button>
            </span>
          </div>
        </aside>

        <div className="agx-stage">{children(api)}</div>
      </div>
    </section>
  );
}
