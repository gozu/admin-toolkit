import { useEffect, useRef, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { AgentOrb } from '../agents/AgentOrb';
import { OffsetChip } from '../agents/explainer/primitives';
import { HERO, CLOSING } from '../agents/explainer/content';
import { ScenePlanLoop } from '../agents/explainer/ScenePlanLoop';
import { SceneCatalog } from '../agents/explainer/SceneCatalog';
import { SceneGauntlet } from '../agents/explainer/SceneGauntlet';
import { SceneTwoFlags } from '../agents/explainer/SceneTwoFlags';
import { SceneSecrets } from '../agents/explainer/SceneSecrets';
import { SceneSandbox } from '../agents/explainer/SceneSandbox';
import { SceneAutonomy } from '../agents/explainer/SceneAutonomy';
import { SceneLedger } from '../agents/explainer/SceneLedger';
import '../agents/explainer/explainer.css';

/** "How Agents Work" — an animated tour of the agent pipeline and its
 * guardrails. Thin shell: hero + scenes + closing strip; every scene owns its
 * own diagram under components/agents/explainer/. The page is full-bleed like
 * the rest of the app; prose is capped per-scene for legibility. */

/* --------------------------------------------------------------- hero -- */

function Hero() {
  const [sectionEl, setSectionEl] = useState<HTMLElement | null>(null);
  const [live, setLive] = useState(false);
  const [phase, setPhase] = useState<'typing' | 'lift'>('typing');
  const [typed, setTyped] = useState(0);
  const startedRef = useRef(false);

  useEffect(() => {
    if (!sectionEl) return;
    const io = new IntersectionObserver(([entry]) => setLive(entry.isIntersecting), {
      threshold: 0.2,
    });
    io.observe(sectionEl);
    return () => io.disconnect();
  }, [sectionEl]);

  // Type the question char-by-char. First run waits ~350ms so the page
  // cascade lands first; loops restart faster. Paused while offscreen.
  useEffect(() => {
    if (!live || phase !== 'typing') return;
    let interval: number | undefined;
    const t = window.setTimeout(
      () => {
        interval = window.setInterval(() => {
          setTyped((n) => (n >= HERO.question.length ? n : n + 1));
        }, 32);
      },
      startedRef.current ? 250 : 350,
    );
    startedRef.current = true;
    return () => {
      window.clearTimeout(t);
      if (interval) window.clearInterval(interval);
    };
  }, [live, phase]);

  // Fully typed → brief beat → the text lifts off as the protagonist chip.
  useEffect(() => {
    if (phase !== 'typing' || typed < HERO.question.length) return;
    const t = window.setTimeout(() => setPhase('lift'), 750);
    return () => window.clearTimeout(t);
  }, [phase, typed]);

  // Chip flight done → reset and retype.
  useEffect(() => {
    if (phase !== 'lift' || !live) return;
    const t = window.setTimeout(() => {
      setTyped(0);
      setPhase('typing');
    }, 3600);
    return () => window.clearTimeout(t);
  }, [phase, live]);

  return (
    <section
      ref={setSectionEl}
      className="agx-hero"
      data-phase={phase}
      data-live={live ? '' : undefined}
    >
      <div className="max-w-[46rem] space-y-3">
        <div className="flex items-center gap-2.5">
          <AgentOrb size={34} state="idle" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--accent)]">
            {HERO.eyebrow}
          </span>
        </div>
        <h1 className="text-2xl font-semibold text-[var(--text-primary)]">{HERO.title}</h1>
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">{HERO.subtitle}</p>
      </div>

      {/* Mock composer: the question types itself, then lifts off as the
          glowing chip that stars in every scene below. */}
      <div className="agx-hero-stage max-w-[46rem]">
        <div className="agx-hero-composer" aria-hidden="true">
          <span className="agx-hero-text">
            {HERO.question.slice(0, typed)}
            <span className="agx-hero-caret" />
          </span>
          <span className="agx-hero-send">↵</span>
        </div>
        {phase === 'lift' && (
          <OffsetChip className="agx-hero-chip">{HERO.question}</OffsetChip>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ closing -- */

function ClosingStrip() {
  const { setActivePage } = useDiag();
  return (
    <section className="grid gap-3 sm:grid-cols-3">
      {CLOSING.map((card) => (
        <button
          key={card.page}
          type="button"
          onClick={() => setActivePage(card.page)}
          className="glass-card p-4 text-left space-y-1.5 transition-transform duration-200 hover:-translate-y-0.5"
        >
          <div className="text-sm font-semibold text-[var(--text-primary)]">{card.title}</div>
          <p className="text-xs leading-relaxed text-[var(--text-muted)]">{card.body}</p>
          <div className="text-xs font-semibold text-[var(--accent)]">{card.cta}</div>
        </button>
      ))}
    </section>
  );
}

/* --------------------------------------------------------------- page -- */

export function AgentExplainerPage() {
  return (
    <div className="w-full px-4 py-4 space-y-16">
      <Hero />
      <ScenePlanLoop />
      <SceneCatalog />
      <SceneGauntlet />
      <SceneTwoFlags />
      <SceneSecrets />
      <SceneSandbox />
      <SceneAutonomy />
      <SceneLedger />
      <ClosingStrip />
    </div>
  );
}
