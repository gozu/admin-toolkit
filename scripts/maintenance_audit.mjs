#!/usr/bin/env node
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const errors = [];
const warnings = [];
const info = [];

const rel = (...parts) => path.join(...parts);
const abs = (...parts) => path.join(root, ...parts);
const exists = (...parts) => fs.existsSync(abs(...parts));
const read = (...parts) => fs.readFileSync(abs(...parts), 'utf8');
const readJson = (...parts) => JSON.parse(read(...parts));

function addError(message) {
  errors.push(message);
}

function addWarning(message) {
  warnings.push(message);
}

function stripPageMarker(value) {
  return value
    .replace(/<[^>]+>/g, '')
    .replace(/\s*\u{1F534}\s*/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseModules(registryText) {
  const entries = [];
  const re = /\{\s*id:\s*'([^']+)'([\s\S]*?)\}\s*,/g;
  let match;
  while ((match = re.exec(registryText)) !== null) {
    const body = match[2];
    const label = body.match(/\blabel:\s*'([^']+)'/)?.[1];
    const navLabel = body.match(/\bnavLabel:\s*'([^']+)'/)?.[1];
    const section = body.match(/\bsection:\s*'([^']+)'/)?.[1];
    if (!label || !section) continue;
    entries.push({
      id: match[1],
      label,
      page: navLabel || label,
      section,
      tool: /\btool:\s*true\b/.test(body),
      deprecated: /\bdeprecated:\s*true\b/.test(body),
    });
  }
  return entries.filter((entry) => !entry.deprecated);
}

function parseReadmePageRows(readmeText) {
  const rows = [];
  let inIndex = false;
  for (const line of readmeText.split('\n')) {
    if (line.startsWith('### Full page index')) {
      inIndex = true;
      continue;
    }
    if (!inIndex) continue;
    if (line.startsWith('## ') || line.startsWith('### ')) break;
    const match = line.match(/^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|/);
    if (!match) continue;
    if (match[1].trim() === 'Section' || /^-+$/.test(match[1].trim())) continue;
    rows.push({
      section: match[1].trim(),
      rawPage: match[2].trim(),
      page: stripPageMarker(match[2]),
      description: match[3].trim(),
      toolMarked: match[2].includes('\u{1F534}'),
    });
  }
  return rows;
}

function trackedFiles() {
  try {
    return new Set(
      execFileSync('git', ['ls-files'], { cwd: root, encoding: 'utf8' })
        .split('\n')
        .filter(Boolean),
    );
  } catch {
    return new Set();
  }
}

function countDirsWithFile(dir, filename) {
  if (!exists(dir)) return 0;
  return fs
    .readdirSync(abs(dir), { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && exists(dir, entry.name, filename))
    .length;
}

function checkVersions(readmeText) {
  const pluginVersion = readJson('plugin.json').version;
  const packageVersion = readJson('resource/frontend/package.json').version;
  const lock = readJson('resource/frontend/package-lock.json');
  const lockVersion = lock.version;
  const lockRootVersion = lock.packages?.['']?.version;
  const readmeBadgeVersion = readmeText.match(/badge\/version-([0-9]+(?:\.[0-9]+)+)-blue/)?.[1];

  const versions = [
    ['plugin.json', pluginVersion],
    ['resource/frontend/package.json', packageVersion],
    ['resource/frontend/package-lock.json', lockVersion],
    ['resource/frontend/package-lock.json packages[""]', lockRootVersion],
    ['README badge', readmeBadgeVersion],
  ];

  const missing = versions.filter(([, version]) => !version).map(([name]) => name);
  if (missing.length) {
    addError(`Missing version metadata: ${missing.join(', ')}`);
  }

  const distinct = new Set(versions.map(([, version]) => version).filter(Boolean));
  if (distinct.size > 1) {
    addError(`Version drift: ${versions.map(([name, version]) => `${name}=${version || 'missing'}`).join(', ')}`);
  } else if (pluginVersion) {
    info.push(`Version metadata is aligned at ${pluginVersion}.`);
  }
}

function checkReadmeModuleIndex(readmeText, modules) {
  const rows = parseReadmePageRows(readmeText);
  if (!rows.length) {
    addError('README full page index table was not found.');
    return;
  }

  const expectedKeys = new Map(modules.map((mod) => [`${mod.section}::${mod.page}`, mod]));
  const actualKeys = new Map(rows.map((row) => [`${row.section}::${row.page}`, row]));

  for (const [key, mod] of expectedKeys) {
    if (!actualKeys.has(key)) {
      addError(`README page index missing ${mod.section} / ${mod.page} (${mod.id}).`);
    }
  }
  for (const [key, row] of actualKeys) {
    if (!expectedKeys.has(key)) {
      addError(`README page index has stale or unknown row ${row.section} / ${row.page}.`);
    }
  }

  for (const mod of modules) {
    const row = actualKeys.get(`${mod.section}::${mod.page}`);
    if (!row) continue;
    if (mod.tool && !row.toolMarked) {
      addError(`README page index row ${mod.section} / ${mod.page} is a tool page but lacks the red marker.`);
    }
    if (!mod.tool && row.toolMarked) {
      addError(`README page index row ${mod.section} / ${mod.page} has the red marker but moduleRegistry.ts does not mark it tool:true.`);
    }
  }

  const sectionCount = new Set(modules.map((mod) => mod.section)).size;
  const pageCount = modules.length;
  const countLine = readmeText.match(/organized into\s+(\d+)\s+sidebar sections covering\s+(\d+)\s+pages/i);
  if (!countLine) {
    addWarning('README feature-tour count line was not found.');
  } else {
    const [, sectionsText, pagesText] = countLine;
    if (Number(sectionsText) !== sectionCount || Number(pagesText) !== pageCount) {
      addError(`README feature-tour count says ${sectionsText} sections / ${pagesText} pages, but moduleRegistry.ts has ${sectionCount} sections / ${pageCount} pages.`);
    }
  }

  info.push(`Module registry exposes ${sectionCount} sections and ${pageCount} active pages.`);
}

function checkReadmeScreenshots(readmeText) {
  const tracked = trackedFiles();
  const refs = [...readmeText.matchAll(/<img\s+[^>]*src="([^"]+)"/g)].map((match) => match[1]);
  for (const ref of refs) {
    if (!exists(ref)) {
      addError(`README references missing image ${ref}.`);
      continue;
    }
    if (ref.startsWith('docs/screenshots/') && tracked.size && !tracked.has(ref)) {
      addWarning(`README screenshot ${ref} exists but is not tracked by git.`);
    }
  }

  if (exists('docs/screenshots')) {
    const used = new Set(refs.filter((ref) => ref.startsWith('docs/screenshots/')));
    const screenshots = fs
      .readdirSync(abs('docs/screenshots'))
      .filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
      .map((name) => rel('docs/screenshots', name));
    const unused = screenshots.filter((file) => !used.has(file));
    if (unused.length) {
      addWarning(`Tracked screenshot files not referenced by README: ${unused.join(', ')}`);
    }
  }
}

function checkArchitectureCounts(readmeText) {
  const routeCount = exists('python-lib/adk_backend/routes')
    ? fs
        .readdirSync(abs('python-lib/adk_backend/routes'))
        .filter((name) => name.endsWith('.py') && name !== '__init__.py').length
    : 0;
  const macroCount = countDirsWithFile('python-runnables', 'runnable.json');

  const routeCounts = new Set([...readmeText.matchAll(/(\d+)\s+route groups/g)].map((match) => Number(match[1])));
  for (const count of routeCounts) {
    if (count !== routeCount) {
      addError(`README says ${count} route groups, but python-lib/adk_backend/routes has ${routeCount}.`);
    }
  }

  const macroCounts = new Set(
    [...readmeText.matchAll(/(\d+)\s+(?:privileged\s+)?(?:host-bound\s+)?macros/g)].map((match) => Number(match[1])),
  );
  for (const count of macroCounts) {
    if (count !== macroCount) {
      addError(`README says ${count} macros, but python-runnables has ${macroCount} runnable macros.`);
    }
  }
}

function checkStalePathReferences() {
  const checks = [
    {
      missingPath: 'python-lib/trends_registry.py',
      files: ['docs/ui-ux-contracts.md', 'scripts/check_frontend_contracts.mjs'],
    },
    {
      missingPath: 'scripts/check_trends_contract.py',
      files: ['docs/ui-ux-contracts.md'],
    },
  ];

  for (const check of checks) {
    if (exists(check.missingPath)) continue;
    const referring = check.files.filter((file) => exists(file) && read(file).includes(check.missingPath));
    if (referring.length) {
      addWarning(`Referenced path ${check.missingPath} does not exist; referenced by ${referring.join(', ')}.`);
    }
  }
}

function checkNotebookCards() {
  const files = [
    ...['__init__.py', 'client.py', 'data.py', 'parse.py', 'ui.py'].map((name) => abs('python-lib/adk_notebook', name)),
    ...(
      exists('python-lib/adk_notebook/cards')
        ? fs
            .readdirSync(abs('python-lib/adk_notebook/cards'))
            .filter((name) => name.endsWith('.py'))
            .map((name) => abs('python-lib/adk_notebook/cards', name))
        : []
    ),
    abs('python-lib/adk_backend/routes/algorithm_review.py'),
  ].filter((file) => fs.existsSync(file));

  if (!files.length) {
    addWarning('No algorithm review notebook sources found.');
    return;
  }

  const result = spawnSync('python3', ['-m', 'py_compile', ...files], {
    cwd: root,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    addError(`Algorithm review notebook Python compile failed:\n${result.stderr || result.stdout}`);
  } else {
    const cardCount = files.filter((file) => file.includes(`${path.sep}cards${path.sep}`)).length;
    info.push(`Algorithm review notebook sources compile (${cardCount} cards).`);
  }
}

function main() {
  if (!exists('plugin.json') || !exists('resource/frontend/package.json')) {
    console.error('[maintenance-audit] Run this from the Admin Toolkit repository root.');
    process.exit(2);
  }

  const readmeText = exists('README.md') ? read('README.md') : '';
  const registryText = exists('resource/frontend/src/utils/moduleRegistry.ts')
    ? read('resource/frontend/src/utils/moduleRegistry.ts')
    : '';
  const modules = registryText ? parseModules(registryText) : [];

  if (!readmeText) addError('README.md is missing.');
  if (!modules.length) addError('No modules parsed from resource/frontend/src/utils/moduleRegistry.ts.');

  if (readmeText) {
    checkVersions(readmeText);
    if (modules.length) checkReadmeModuleIndex(readmeText, modules);
    checkReadmeScreenshots(readmeText);
    checkArchitectureCounts(readmeText);
  }
  checkStalePathReferences();
  checkNotebookCards();

  console.log('Admin Toolkit maintenance audit');
  console.log('');
  if (errors.length) {
    console.log('Errors');
    for (const item of errors) console.log(`- ${item}`);
    console.log('');
  }
  if (warnings.length) {
    console.log('Warnings');
    for (const item of warnings) console.log(`- ${item}`);
    console.log('');
  }
  if (info.length) {
    console.log('Info');
    for (const item of info) console.log(`- ${item}`);
    console.log('');
  }
  if (!errors.length && !warnings.length) {
    console.log('No maintenance drift detected.');
  }

  process.exit(errors.length ? 1 : 0);
}

main();
