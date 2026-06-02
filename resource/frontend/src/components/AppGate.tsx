import type { ReactNode } from 'react';

interface AppGateProps {
  children: (gate: { isSqliteFallback: boolean }) => ReactNode;
}

export function AppGate({ children }: AppGateProps) {
  return <>{children({ isSqliteFallback: false })}</>;
}
