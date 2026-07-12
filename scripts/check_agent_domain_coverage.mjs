#!/usr/bin/env node
// Agent domain coverage contract: adding parsed data without exposing it to
// the agent BREAKS THE BUILD.
//
// Sources of truth:
//   - resource/frontend/src/types/core.ts  → the canonical ParsedData fields
//   - resource/frontend/src/utils/moduleRegistry.ts → the module ids
//   - python-lib/atk_agent_common/domain_registry.py → what the agent can
//     query (DOMAINS), what other sensors cover (PARSED_FIELD_COVERAGE),
//     and per-module coverage (MODULE_COVERAGE) — exported via
//     contract_manifest(), executed here with a bare python3.
//
// Fails unless every ParsedData DATA field (lifecycle/progress fields
// excluded) and every module id is accounted for in the registry. Stale
// registry entries (naming fields/modules that no longer exist) fail too, so
// the mapping can't rot in either direction. Fix-action completeness (every
// domain/field has fix_actions or an explicit waiver, every named action is
// catalogued) is asserted inside domain_registry at import — a violation
// makes the python3 call below exit non-zero.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(repo, p), 'utf8');
const fail = (msg) => {
  console.error(`[domain-coverage] ${msg}`);
  process.exitCode = 1;
};

// ── the registry manifest (import-time asserts run here too) ─────────────────
let manifest;
try {
  const out = execFileSync(
    'python3',
    ['-c', 'import json; from atk_agent_common import domain_registry as d; print(json.dumps(d.contract_manifest()))'],
    { env: { ...process.env, PYTHONPATH: path.join(repo, 'python-lib') }, encoding: 'utf8' },
  );
  manifest = JSON.parse(out.trim().split('\n').pop());
} catch (err) {
  fail(`domain_registry import/assert failed: ${err.message}`);
  process.exit(1);
}

const domainFields = new Map(); // parsed field -> domain name
for (const d of manifest.domains) {
  for (const f of d.parsedFields) domainFields.set(f, d.name);
}
const coverage = manifest.parsedFieldCoverage ?? {};
const moduleCoverage = manifest.moduleCoverage ?? {};

// ── ParsedData data fields ────────────────────────────────────────────────────
const core = read('resource/frontend/src/types/core.ts');
const block = core.match(/export interface ParsedData \{([\s\S]*?)\n\}/);
if (!block) {
  fail('could not locate the ParsedData interface in types/core.ts');
  process.exit(1);
}
const dataFields = [];
for (const m of block[1].matchAll(/^ {2}(\w+)\??:\s*([^;]+);/gm)) {
  const [, key, type] = m;
  // Lifecycle / progress / readiness plumbing is not data the agent queries.
  if (/\bLifecycle\b|\bLoadingProgressState\b/.test(type)) continue;
  if (key === 'dataReady') continue;
  dataFields.push(key);
}
if (dataFields.length < 40) {
  fail(`ParsedData parse looks broken: only ${dataFields.length} data fields found`);
}

for (const field of dataFields) {
  if (!domainFields.has(field) && !(field in coverage)) {
    fail(
      `ParsedData.${field} is not reachable by the agent: map it to a domain ` +
        `(domain_registry.DOMAINS parsed_fields) or account for it in ` +
        `PARSED_FIELD_COVERAGE (sensor/deferred/waiver, with fix actions).`,
    );
  }
}

// stale registry entries (field renamed/removed in core.ts)
const fieldSet = new Set(dataFields);
for (const [field, domain] of domainFields) {
  if (!fieldSet.has(field)) {
    fail(`domain '${domain}' claims ParsedData.${field}, which no longer exists in core.ts`);
  }
}
for (const field of Object.keys(coverage)) {
  if (!fieldSet.has(field)) {
    fail(`PARSED_FIELD_COVERAGE names ParsedData.${field}, which no longer exists in core.ts`);
  }
}

// ── module ids ────────────────────────────────────────────────────────────────
const registry = read('resource/frontend/src/utils/moduleRegistry.ts');
const moduleIds = [...registry.matchAll(/\bid: '([^']+)'/g)].map((m) => m[1]);
if (moduleIds.length < 20) {
  fail(`moduleRegistry parse looks broken: only ${moduleIds.length} module ids found`);
}
for (const id of moduleIds) {
  if (!(id in moduleCoverage)) {
    fail(`module '${id}' has no MODULE_COVERAGE entry in domain_registry.py`);
  }
}
const idSet = new Set(moduleIds);
for (const id of Object.keys(moduleCoverage)) {
  if (!idSet.has(id)) {
    fail(`MODULE_COVERAGE names module '${id}', which no longer exists in moduleRegistry.ts`);
  }
}

if (process.exitCode) {
  console.error('[domain-coverage] FAILED — the agent data-access contract is broken.');
} else {
  console.log(
    `[domain-coverage] OK — ${dataFields.length} ParsedData fields / ${moduleIds.length} modules ` +
      `covered by ${manifest.domains.length} domains + ${Object.keys(coverage).length} sensor/waiver entries.`,
  );
}
