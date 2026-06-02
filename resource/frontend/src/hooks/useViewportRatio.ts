import { useEffect, useState } from 'react';

function readViewportRatio(): number {
  if (typeof window === 'undefined') return 1;
  const width = window.innerWidth || 1;
  const height = window.innerHeight || 1;
  return width / height;
}

export function useViewportRatio(): number {
  const [ratio, setRatio] = useState<number>(() => readViewportRatio());

  useEffect(() => {
    const handleResize = () => setRatio(readViewportRatio());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return ratio;
}
