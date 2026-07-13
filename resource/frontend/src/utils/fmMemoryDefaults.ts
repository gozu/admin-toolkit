// Fleet Manager memory defaults.
//
// Mirrors the brackets in FM's setup_memory_settings.py, with the public DSS
// cgroups recommendation applied to the top bracket: 120 GiB and up uses 70%
// of physical memory. Round up to whole GiB values so 256 GiB maps to 180g,
// matching the documented example.

export function fmBackendXmxGB(instanceGB: number): number {
  if (instanceGB > 95) return 16;
  if (instanceGB > 30) return 8;
  if (instanceGB >= 12) return 4;
  return 2;
}

export function fmCgroupGB(instanceGB: number): number {
  if (instanceGB >= 120) return Math.ceil(instanceGB * 0.7);
  if (instanceGB > 60) return Math.trunc(instanceGB * 0.66);
  if (instanceGB > 30) return instanceGB - 20;
  return Math.trunc(instanceGB * 0.5);
}

export interface FMBaseline {
  backendGB: number;
  cgroupGB: number;
  availableForJEK: number;
}

export function fmBaseline(instanceGB: number): FMBaseline {
  const backendGB = fmBackendXmxGB(instanceGB);
  const cgroupGB = fmCgroupGB(instanceGB);
  return {
    backendGB,
    cgroupGB,
    availableForJEK: instanceGB - backendGB - cgroupGB,
  };
}

export type FMComparison = 'match' | 'below' | 'above';

export interface FMComparisonResult {
  backend: FMComparison;
  cgroup: FMComparison;
}

export function compareToFM(
  actual: { backendGB: number; cgroupGB: number },
  expected: FMBaseline,
): FMComparisonResult {
  // Exact match on backend (it's an Xmx flag, no rounding involved).
  const backend: FMComparison =
    actual.backendGB === expected.backendGB
      ? 'match'
      : actual.backendGB < expected.backendGB
        ? 'below'
        : 'above';

  // ±1 GB tolerance on cgroup to absorb int() rounding in FM + parseInt
  // unit-suffix stripping in the diag parser.
  const cgroupDelta = actual.cgroupGB - expected.cgroupGB;
  const cgroup: FMComparison =
    Math.abs(cgroupDelta) <= 1 ? 'match' : cgroupDelta < 0 ? 'below' : 'above';

  return { backend, cgroup };
}

export function atOrAboveFMDefaults(cmp: FMComparisonResult): boolean {
  return cmp.backend !== 'below' && cmp.cgroup !== 'below';
}
