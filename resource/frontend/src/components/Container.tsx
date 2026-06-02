import type { ReactNode } from 'react';

interface ContainerProps {
  children: ReactNode;
  className?: string;
  ultraWide?: boolean;
}

export function Container({ children, className = '' }: ContainerProps) {
  return (
    <div className={`w-full ${className}`}>
      {children}
    </div>
  );
}
