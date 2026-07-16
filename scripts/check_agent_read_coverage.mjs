#!/usr/bin/env node
// Agent read-coverage contract: the toolkit_get bridge and the capability
// page map can never drift from what actually exists.
//
// Sources of truth:
//   - python-lib/atk_agent_common/read_registry.py → the whitelisted read
//     endpoints (ENDPOINTS) + the webapp page map (TOOLKIT_PAGES), exported
//     via contract_manifest(), executed here with a bare python3.
//   - python-lib/adk_backend/routes/*.py → the Flask routes that must back
//     every registered endpoint path.
//   - resource/frontend/src/utils/moduleRegistry.ts → the module ids the
//     page map must mirror (both directions).
//   - python-lib/atk_agent_common/tools_impl.py → the sensor catalog that
//     must expose the bridge tools.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(repo, p), 'utf8');
const fail = (msg) => {
  console.error(`[read-coverage] ${msg}`);
  process.exitCode = 1;
};

// ── the registry manifest (import-time asserts run here too) ─────────────────
let manifest;
try {
  const out = execFileSync(
    'python3',
    ['-c', 'import json; from atk_agent_common import read_registry as r; print(json.dumps(r.contract_manifest()))'],
    { env: { ...process.env, PYTHONPATH: path.join(repo, 'python-lib') }, encoding: 'utf8' },
  );
  manifest = JSON.parse(out.trim().split('\n').pop());
} catch (err) {
  fail(`read_registry import/assert failed: ${err.message}`);
  process.exit(1);
}

// ── every registered path must exist as a Flask route ────────────────────────
const routesDir = path.join(repo, 'python-lib/adk_backend/routes');
const routesSrc = fs
  .readdirSync(routesDir)
  .filter((f) => f.endsWith('.py'))
  .map((f) => fs.readFileSync(path.join(routesDir, f), 'utf8'))
  .join('\n');
const routePaths = new Set([...routesSrc.matchAll(/@bp\.route\('([^']+)'/g)].map((m) => m[1]));
if (routePaths.size < 40) {
  fail(`routes parse looks broken: only ${routePaths.size} routes found`);
}
for (const ep of manifest.endpoints) {
  if (!routePaths.has(ep.path)) {
    fail(`endpoint '${ep.name}' registers path ${ep.path}, which is not a backend route`);
  }
  if (ep.progressPath && !routePaths.has(ep.progressPath)) {
    fail(`endpoint '${ep.name}' registers progress path ${ep.progressPath}, which is not a backend route`);
  }
}

// ── the page map mirrors moduleRegistry.ts, both directions ──────────────────
const registry = read('resource/frontend/src/utils/moduleRegistry.ts');
const moduleIds = [...registry.matchAll(/\bid: '([^']+)'/g)].map((m) => m[1]);
if (moduleIds.length < 20) {
  fail(`moduleRegistry parse looks broken: only ${moduleIds.length} module ids found`);
}
const pages = manifest.pages ?? {};
for (const id of moduleIds) {
  if (!(id in pages)) {
    fail(`module '${id}' has no TOOLKIT_PAGES entry in read_registry.py — the agent can't point users at it`);
  }
}
const idSet = new Set(moduleIds);
for (const id of Object.keys(pages)) {
  if (!idSet.has(id)) {
    fail(`TOOLKIT_PAGES names module '${id}', which no longer exists in moduleRegistry.ts`);
  }
}

// ── the sensor catalog must expose the bridge tools ──────────────────────────
const toolsSrc = read('python-lib/atk_agent_common/tools_impl.py');
for (const sensor of ['log_tail', 'toolkit_get', 'list_capabilities']) {
  if (!new RegExp(`^    '${sensor}':`, 'm').test(toolsSrc)) {
    fail(`SENSOR_DESCRIPTIONS is missing '${sensor}' — the read bridge is not exposed to agents`);
  }
}

if (process.exitCode) {
  console.error('[read-coverage] FAILED — the agent read-access contract is broken.');
} else {
  console.log(
    `[read-coverage] OK — ${manifest.endpoints.length} whitelisted endpoints backed by routes, ` +
      `${Object.keys(pages).length} pages mirrored from ${moduleIds.length} modules.`,
  );
}
