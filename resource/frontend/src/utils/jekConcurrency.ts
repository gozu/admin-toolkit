import type { ParsedData } from '../types';

/**
 * A JEK is one process per *job*, shared by all activities inside that job.
 * The worst-case number of concurrent JEKs is therefore the number of
 * concurrent jobs — not the number of concurrent activities.
 */
export function computeBaseMaxJobs(
  maxJobs: number,
  maxActivities: number,
  maxActivitiesPerJob: number,
): number {
  if (maxJobs > 0) return maxJobs;
  if (maxActivitiesPerJob > 0 && maxActivities > 0) {
    return Math.floor(maxActivities / maxActivitiesPerJob);
  }
  if (maxActivities > 0) return maxActivities;
  return 0;
}

/**
 * Fraction of jobs that still land on the local backend after the
 * instance-default containerization is taken into account. Assumes ~95%
 * of workloads honour the default and ~5% override it; assumes a 50/50
 * split between user-code and visual-recipe (DSS engine) workloads.
 */
export function computeLocalFraction(
  userCodeContainer: boolean,
  visualRecipesContainer: boolean,
): number {
  const userCodeLocal = userCodeContainer ? 0.05 : 1.0;
  const visualRecipesLocal = visualRecipesContainer ? 0.05 : 1.0;
  return (userCodeLocal + visualRecipesLocal) / 2;
}

export type JekDerivation = 'jobs' | 'activities-per-job' | 'activities' | 'none';

export interface JekConcurrencyInput {
  maxRunningActivities?: ParsedData['maxRunningActivities'];
  containerExecDefaults?: ParsedData['containerExecDefaults'];
}

export interface JekConcurrencyResult {
  baseMaxJobs: number;
  effectiveMaxJobs: number;
  localFraction: number;
  derivedFrom: JekDerivation;
  userCodeContainer: boolean;
  visualRecipesContainer: boolean;
}

export function computeJekConcurrency(input: JekConcurrencyInput): JekConcurrencyResult {
  const maxActivitiesRaw = input.maxRunningActivities?.['Max Running Activities'];
  const maxActivitiesPerJobRaw = input.maxRunningActivities?.['Max Running Activities Per Job'];
  const maxJobsRaw = input.maxRunningActivities?.['Max Running Jobs'];

  const maxActivities = typeof maxActivitiesRaw === 'number' ? maxActivitiesRaw : 0;
  const maxActivitiesPerJob = typeof maxActivitiesPerJobRaw === 'number' ? maxActivitiesPerJobRaw : 0;
  const maxJobs = typeof maxJobsRaw === 'number' && maxJobsRaw > 0 ? maxJobsRaw : 0;

  let derivedFrom: JekDerivation;
  if (maxJobs > 0) {
    derivedFrom = 'jobs';
  } else if (maxActivitiesPerJob > 0 && maxActivities > 0) {
    derivedFrom = 'activities-per-job';
  } else if (maxActivities > 0) {
    derivedFrom = 'activities';
  } else {
    derivedFrom = 'none';
  }

  const baseMaxJobs = computeBaseMaxJobs(maxJobs, maxActivities, maxActivitiesPerJob);

  const execDefaults = input.containerExecDefaults;
  const execConfigsPresent = !!execDefaults && execDefaults.executionConfigsCount > 0;
  const userCodeContainer = execConfigsPresent && execDefaults!.userCodeMode === 'CONTAINER';
  const visualRecipesContainer = execConfigsPresent && execDefaults!.visualRecipesMode === 'CONTAINER';

  const localFraction = computeLocalFraction(userCodeContainer, visualRecipesContainer);
  const effectiveMaxJobs = baseMaxJobs > 0 ? Math.ceil(baseMaxJobs * localFraction) : 0;

  return {
    baseMaxJobs,
    effectiveMaxJobs,
    localFraction,
    derivedFrom,
    userCodeContainer,
    visualRecipesContainer,
  };
}
