import { fetchJson } from '../utils/api';
import { getActiveHostId } from './hostStore';
import { fetchWithSessionCache, peekSessionCache, invalidateSessionKey } from './sessionCache';

export interface ProjectRow {
  projectKey: string;
  name: string;
  owner: string;
  daysInactive: number;
}

const key = (hostId = getActiveHostId()) => `inactive-projects:${hostId}`;
export const getCachedInactiveProjects = (hostId: string): ProjectRow[] | null =>
  peekSessionCache<ProjectRow[]>(key(hostId)) ?? null;
export const fetchInactiveProjects = (): Promise<ProjectRow[]> => fetchWithSessionCache(key(), async () =>
  (await fetchJson<{ projects: ProjectRow[] }>('/api/tools/inactive-projects')).projects);
export const prefetchInactiveProjects = fetchInactiveProjects;
export const hasInactiveProjectsCache = () => getCachedInactiveProjects(getActiveHostId()) !== null;
export const clearInactiveProjectsCache = () => invalidateSessionKey(key());
