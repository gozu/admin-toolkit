import { useSyncExternalStore } from 'react';

export interface ThresholdSettings {
  // === Main Settings ===
  // Check thresholds
  codeEnvCountUnhealthy: number;
  codeStudioCountUnhealthy: number;
  filesystemWarningPct: number;
  filesystemCriticalPct: number;
  largeFlowThreshold: number;
  inactiveProjectDays: number;
  emptyProjectBytes: number;
  orphanNotebookMinCount: number;
  highFreqScenarioMinutes: number;
  deprecatedPythonPrefixes: string;
  disabledFeaturesSeverityCutoff: number;

  // === Advanced Settings ===
  // Version & system thresholds
  openFilesMinimum: number;
  javaHeapMinimumMB: number;
  pythonCriticalBelow: string;
  pythonWarningBelow: string;
  sparkVersionMinimum: number;
  projectCountWarning: number;

  // Health scoring weights
  weightCodeEnvs: number;
  weightProjectFootprint: number;
  weightSystemCapacity: number;
  weightSecurityIsolation: number;
  weightVersionCurrency: number;
  weightRuntimeConfig: number;
  healthCriticalBelow: number;
  healthWarningBelow: number;
  /** Score the overall is clamped to when a critical cap rule fires
   *  (49 ⇒ always lands in the critical band). */
  healthCriticalCapScore: number;
  /** DIP_HOME data mount used% at/above which the cap rule fires. */
  dataMountCriticalPct: number;

  // Log parsing
  logLinesBefore: number;
  logLinesAfter: number;
  logTimeThresholdSec: number;
  logMaxErrors: number;

  // Scan limits
  largeFileThresholdGB: number;
  dirTreeDefaultDepth: number;
  fileViewerMaxLines: number;
  syntaxHighlightMaxKB: number;
}

const DEFAULT_THRESHOLDS: ThresholdSettings = {
  // Main Settings
  codeEnvCountUnhealthy: 1,
  codeStudioCountUnhealthy: 7,
  filesystemWarningPct: 70,
  filesystemCriticalPct: 90,
  largeFlowThreshold: 100,
  inactiveProjectDays: 180,
  emptyProjectBytes: 1048576,
  orphanNotebookMinCount: 5,
  highFreqScenarioMinutes: 30,
  deprecatedPythonPrefixes: '2.,3.6,3.7',
  disabledFeaturesSeverityCutoff: 5,

  // Advanced Settings
  openFilesMinimum: 65535,
  javaHeapMinimumMB: 2048,
  pythonCriticalBelow: '3.8',
  pythonWarningBelow: '3.10',
  sparkVersionMinimum: 3,
  projectCountWarning: 500,

  weightCodeEnvs: 0.15,
  weightProjectFootprint: 0.15,
  weightSystemCapacity: 0.30,
  weightSecurityIsolation: 0,
  weightVersionCurrency: 0.10,
  weightRuntimeConfig: 0.30,
  healthCriticalBelow: 50,
  healthWarningBelow: 80,
  healthCriticalCapScore: 49,
  dataMountCriticalPct: 75,

  logLinesBefore: 10,
  logLinesAfter: 100,
  logTimeThresholdSec: 5,
  logMaxErrors: 5,

  largeFileThresholdGB: 100,
  dirTreeDefaultDepth: 3,
  fileViewerMaxLines: 10000,
  syntaxHighlightMaxKB: 500,
};

let listeners: Array<() => void> = [];
let cached: ThresholdSettings | null = null;
let serverDefaults: Partial<ThresholdSettings> = {};

// Fetch plugin param defaults once on module load
import { fetchJson } from '../utils/api';
fetchJson<Partial<ThresholdSettings>>('/api/settings/threshold-defaults')
  .then((data) => {
    if (Object.keys(data).length > 0) {
      serverDefaults = data;
      cached = null;
      for (const fn of listeners) fn();
    }
  })
  .catch(() => {});

function read(): ThresholdSettings {
  if (cached) return cached;
  cached = { ...DEFAULT_THRESHOLDS, ...serverDefaults };
  return cached;
}

function subscribe(cb: () => void) {
  listeners.push(cb);
  return () => {
    listeners = listeners.filter((fn) => fn !== cb);
  };
}

function getSnapshot(): ThresholdSettings {
  return read();
}

export function useThresholds() {
  const thresholds = useSyncExternalStore(subscribe, getSnapshot);
  return { thresholds, defaults: DEFAULT_THRESHOLDS };
}
