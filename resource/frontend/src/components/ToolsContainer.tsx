import { lazy, Suspense } from 'react';
import { Container } from './Container';
import { useDiag } from '../context/DiagContext';
import { useUltraWideLayout } from '../hooks';

const PluginComparator = lazy(() =>
  import('./PluginComparator').then((m) => ({ default: m.PluginComparator })),
);
const InactiveProjectCleaner = lazy(() =>
  import('./InactiveProjectCleaner').then((m) => ({ default: m.InactiveProjectCleaner })),
);

export function ToolsContainer() {
  const { state } = useDiag();
  const { ultraWideEnabled } = useUltraWideLayout();
  const { activePage } = state;

  return (
    <main className="flex-1 flex flex-col min-h-0">
      <Container ultraWide={ultraWideEnabled} className="flex-1 flex flex-col min-h-0">
        <Suspense
          fallback={<div className="glass-card p-6 text-[var(--text-secondary)]">Loading...</div>}
        >
          {activePage === 'project-cleaner' && <InactiveProjectCleaner />}
          {activePage === 'plugins' && <PluginComparator />}
        </Suspense>
      </Container>
    </main>
  );
}
