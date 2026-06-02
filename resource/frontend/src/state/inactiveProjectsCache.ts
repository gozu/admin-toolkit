import { fetchJson } from '../utils/api';
import { getActiveHostId } from './hostStore';

export interface ProjectRow {
  projectKey: string;
  name: string;
  owner: string;
  daysInactive: number;
}

// ── Module-level cache so data survives remounts ──

const _cachedProjectsByHost = new Map<string, ProjectRow[]>();
const _cachePromiseByHost = new Map<string, Promise<ProjectRow[]>>();

/** Returns cached inactive projects for the given host, or null if not cached. */
export function getCachedInactiveProjects(hostId: string): ProjectRow[] | null {
  return _cachedProjectsByHost.get(hostId) ?? null;
}

export function fetchInactiveProjects(): Promise<ProjectRow[]> {
  const hostId = getActiveHostId();
  const cached = _cachedProjectsByHost.get(hostId);
  if (cached) return Promise.resolve(cached);
  const inflight = _cachePromiseByHost.get(hostId);
  if (inflight) return inflight;
  const promise = fetchJson<{ projects: ProjectRow[] }>('/api/tools/inactive-projects')
    .then((res) => {
      _cachedProjectsByHost.set(hostId, res.projects);
      _cachePromiseByHost.delete(hostId);
      return res.projects;
    })
    .catch((err) => {
      _cachePromiseByHost.delete(hostId);
      throw err;
    });
  _cachePromiseByHost.set(hostId, promise);
  return promise;
}

/** Prefetch inactive projects so data is ready before the user navigates to Project Cleaner */
export function prefetchInactiveProjects(): Promise<ProjectRow[]> {
  return fetchInactiveProjects();
}

/** Returns true if inactive projects data has been fetched and cached */
export function hasInactiveProjectsCache(): boolean {
  return _cachedProjectsByHost.has(getActiveHostId());
}

/** Clear the cache (e.g., after a delete operation) */
export function clearInactiveProjectsCache() {
  const hostId = getActiveHostId();
  _cachedProjectsByHost.delete(hostId);
  _cachePromiseByHost.delete(hostId);
}
