import type { ParsedData } from '../../../types';

export type EstateKind = 'project' | 'environment' | 'connection';
export interface EstateObject {
  id: string;
  kind: EstateKind;
  key: string;
  name: string;
  detail: string;
  language?: string;
  attention?: string;
}
export interface EstateModel {
  objects: EstateObject[];
  links: Map<string, Set<string>>;
}

/** Only inventory and recorded usage edges; unknown dependencies stay unknown. */
export function buildEstateModel(data: ParsedData): EstateModel {
  const objects = new Map<string, EstateObject>();
  const links = new Map<string, Set<string>>();
  const add = (object: EstateObject) => objects.set(object.id, object);
  const connect = (a: string, b: string) => {
    if (!links.has(a)) links.set(a, new Set());
    if (!links.has(b)) links.set(b, new Set());
    links.get(a)!.add(b);
    links.get(b)!.add(a);
  };
  const footprints = new Map(data.projectFootprint?.map((p) => [p.projectKey, p]));
  // Live mode delivers project inventory through the footprint scan; diagnostic
  // bundles can also provide basic projects. Merge both without duplicate blocks.
  const projects = new Map(
    (data.projectFootprint ?? []).map((p) => [p.projectKey, {
      key: p.projectKey, name: p.name, owner: p.owner,
    }]),
  );
  for (const p of data.projects ?? []) projects.set(p.key, p);
  for (const p of projects.values()) {
    const footprint = footprints.get(p.key);
    add({
      id: `project:${p.key}`,
      kind: 'project',
      key: p.key,
      name: p.name || p.key,
      detail: p.owner ? `Owner: ${p.owner}` : 'Project',
      attention:
        footprint && ['orange', 'red', 'angry-red'].includes(footprint.projectSizeHealth)
          ? 'Project footprint needs attention'
          : undefined,
    });
  }
  for (const env of data.codeEnvs ?? []) {
    const id = `environment:${env.language}:${env.name}`;
    add({
      id,
      kind: 'environment',
      key: env.name,
      name: env.name,
      language: env.language,
      detail: `${env.language} · ${env.version || 'version unknown'}`,
    });
    const projects = new Set([
      ...(env.projectKeys ?? []),
      ...(env.usageDetails ?? []).map((u) => u.projectKey),
    ]);
    for (const key of projects) connect(id, `project:${key}`);
  }
  const health = new Map(data.connectionHealth?.map((c) => [c.name, c]));
  const audit = new Map(data.connectionAudit?.map((c) => [c.name, c]));
  for (const conn of data.connectionDetails ?? []) {
    const verdict = health.get(conn.name);
    const finding = audit.get(conn.name);
    add({
      id: `connection:${conn.name}`,
      kind: 'connection',
      key: conn.name,
      name: conn.name,
      detail: conn.type,
      attention:
        verdict?.status === 'fail'
          ? verdict.error || 'Connection test failed'
          : finding && finding.severity !== 'info'
            ? finding.configIssues.join(' · ') || 'Configuration needs attention'
            : undefined,
    });
  }
  for (const conn of [
    ...(data.connectionDatasetUsages ?? []),
    ...(data.connectionLlmUsages ?? []),
  ]) {
    for (const usage of conn.projects)
      connect(`connection:${conn.name}`, `project:${usage.projectKey}`);
  }
  // Do not imply visible links to objects that inventory has not delivered yet.
  for (const [id, neighbors] of links) {
    if (!objects.has(id)) {
      links.delete(id);
      continue;
    }
    for (const neighbor of neighbors) if (!objects.has(neighbor)) neighbors.delete(neighbor);
  }
  return { objects: [...objects.values()], links };
}
