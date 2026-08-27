// Hidden display-anonymization ("screenshot") mode.
//
// Typing the keyword in the shell (see AppShell) flips a localStorage flag and
// reloads. While on, every identifying string the app has seen in data —
// people, projects, connections, code envs, hosts, clusters, groups, emails —
// is rewritten to a stable fictional cast (Acme Corp, cartoon characters, fun
// project codenames) at the DOM level: a TreeWalker pass plus a
// MutationObserver over document.body, with explicit hooks for canvas chart
// labels and the report export, which a DOM pass can't reach.
//
// Display-only by design: API calls, hrefs and DSS-bound exports carry real
// values, and CSV table exports inherit the aliases because they scrape the
// rendered DOM. The alias dictionary persists to localStorage so the same real
// entity keeps the same alias across pages, reloads and screenshots. Known
// residual leaks (accepted): text typed into inputs/textareas (e.g. the raw-log
// analysis textarea), raw file downloads, and free-text strings naming entities
// the app never loaded as data.

import { getRegisteredScanStores, type RegisteredScanStore } from '../state/scanStoreRegistry';

const MODE_KEY = 'admin-toolkit:screenshotMode';
const DICT_KEY = 'admin-toolkit:screenshotDict';

type EntityClass =
  | 'person' | 'email' | 'group' | 'org' | 'project' | 'object' | 'connection'
  | 'codeenv' | 'llm' | 'hostlabel' | 'hosturl' | 'nodeid' | 'installid'
  | 'cluster' | 'namespace' | 'registry' | 'cloudid' | 'ip';

// ── Alias pools ──────────────────────────────────────────────────────────────

// [displayName, login] — logins hand-set so they stay unique within the pool.
const CAST: ReadonlyArray<readonly [string, string]> = [
  ['Bugs Bunny', 'bbunny'], ['Daffy Duck', 'dduck'], ['Porky Pig', 'ppig'],
  ['Elmer Fudd', 'efudd'], ['Wile Coyote', 'wcoyote'], ['Road Runner', 'rrunner'],
  ['Yosemite Sam', 'ysam'], ['Tweety Bird', 'tbird'], ['Sylvester Cat', 'sylvester'],
  ['Foghorn Leghorn', 'fleghorn'], ['Marvin Martian', 'mmartian'],
  ['Speedy Gonzales', 'sgonzales'], ['Lola Bunny', 'lbunny'], ['Petunia Pig', 'petunia'],
  ['Taz Devil', 'tdevil'], ['Fred Flintstone', 'fflintstone'],
  ['Wilma Flintstone', 'wflintstone'], ['Barney Rubble', 'brubble'],
  ['Betty Rubble', 'berubble'], ['Pebbles Flintstone', 'pflintstone'],
  ['George Jetson', 'gjetson'], ['Jane Jetson', 'jjetson'], ['Judy Jetson', 'judyj'],
  ['Elroy Jetson', 'ejetson'], ['Scooby Doo', 'sdoo'], ['Shaggy Rogers', 'srogers'],
  ['Velma Dinkley', 'vdinkley'], ['Daphne Blake', 'dblake'], ['Fred Jones', 'fjones'],
  ['Yogi Bear', 'ybear'], ['Booboo Bear', 'bbear'], ['Cindy Bear', 'cbear'],
  ['Huckleberry Hound', 'hhound'], ['Top Cat', 'tcat'], ['Johnny Bravo', 'jbravo'],
  ['Mickey Mouse', 'mmouse'], ['Minnie Mouse', 'minmouse'], ['Donald Duck', 'donduck'],
  ['Daisy Duck', 'daduck'], ['Scrooge McDuck', 'smcduck'], ['Huey Duck', 'hduck'],
  ['Dewey Duck', 'deduck'], ['Louie Duck', 'lduck'], ['Launchpad McQuack', 'lmcquack'],
  ['Darkwing Duck', 'dwduck'], ['Goofy Goof', 'ggoof'], ['Max Goof', 'mgoof'],
  ['Olive Oyl', 'ooyl'], ['Felix Cat', 'fcat'], ['Tom Cat', 'tomcat'],
  ['Jerry Mouse', 'jmouse'], ['Spike Bulldog', 'sbulldog'], ['Droopy Dog', 'ddog'],
  ['Rocky Squirrel', 'rsquirrel'], ['Bullwinkle Moose', 'bmoose'],
  ['Boris Badenov', 'bbadenov'], ['Natasha Fatale', 'nfatale'],
  ['Dudley Doright', 'ddoright'], ['Snidely Whiplash', 'swhiplash'],
  ['Charlie Brown', 'cbrown'], ['Lucy Pelt', 'lpelt'], ['Linus Pelt', 'lipelt'],
  ['Sally Brown', 'sbrown'], ['Peppermint Patty', 'ppatty'],
];

const CODENAMES = [
  'KRAKEN', 'MOONSHOT', 'JACKALOPE', 'TUMBLEWEED', 'SASQUATCH', 'STARDUST',
  'THUNDERCLAP', 'MARSHMALLOW', 'PORCUPINE', 'ZEPPELIN', 'FLAMINGO', 'AVOCADO',
  'BLIZZARD', 'CACTUS', 'DYNAMITE', 'ECLIPSE', 'FIREFLY', 'GUMDROP',
  'HULLABALOO', 'ICEBERG', 'JUKEBOX', 'KAZOO', 'LIGHTHOUSE', 'MONGOOSE',
  'NARWHAL', 'OCTOPUS', 'PINBALL', 'QUICKSAND', 'ROADTRIP', 'SUBMARINE',
  'TORNADO', 'UKULELE', 'VOLCANO', 'WAFFLES', 'XYLOPHONE', 'YETI', 'ZIGZAG',
  'BUMBLEBEE', 'CANNONBALL', 'DOODLEBUG', 'FIZZBANG', 'GIZMO', 'HONEYPOT',
  'IGLOO', 'JALOPY', 'KUMQUAT', 'LLAMA', 'MERMAID', 'NIMBUS', 'OUTBACK',
  'PELICAN', 'QUASAR', 'RICKSHAW', 'SNORKEL', 'TADPOLE', 'WHIRLWIND', 'BANJO',
  'CATAPULT', 'DIRIGIBLE',
];

// The ACME mail-order catalog — connections, code envs and DSS objects.
const ACME_WORDS = [
  'anvil', 'dynamite', 'rocket', 'magnet', 'catapult', 'slingshot',
  'trampoline', 'mallet', 'boulder', 'piano', 'tunnel', 'glue', 'springs',
  'horn', 'skates', 'jetpack', 'decoy', 'tent', 'paint', 'drill', 'crowbar',
  'pulley', 'lever', 'bellows', 'whistle', 'mousetrap', 'birdseed', 'fuse',
  'gears', 'periscope', 'stilts', 'raft', 'kite', 'lasso', 'telescope',
];

const CREWS = [
  'toon-squad', 'scooby-gang', 'looney-tunes', 'flintstones', 'jetsons',
  'powerpuffs', 'rugrats', 'animaniacs', 'thundercats', 'gummi-bears',
  'ducktales', 'care-bears', 'smurfs', 'snorks', 'wacky-racers', 'herculoids',
];

// Values never treated as identifying, per class.
const PERSON_STOP = new Set([
  'admin', 'root', 'user', 'users', 'dataiku', 'system', 'api', 'test',
  'guest', 'nobody', 'daemon', 'postgres', 'ubuntu', 'ec2-user', 'centos',
  'unknown', 'none',
]);
const GROUP_STOP = new Set([
  'users', 'admins', 'administrators', 'everyone', 'guest', 'guests',
  'public', 'default', 'readers', 'writers', 'all',
]);
const HOST_STOP = new Set(['local', 'local dss']);

// ── Mode flag ────────────────────────────────────────────────────────────────

function readEnabled(): boolean {
  try {
    return globalThis.localStorage?.getItem(MODE_KEY) === '1';
  } catch {
    return false;
  }
}

// Read once — flipping the flag always goes through a reload, so a stale value
// can't be observed within a page lifetime.
const enabled = readEnabled();

export function isAnonEnabled(): boolean {
  return enabled;
}

/** Keyword handler: flip the flag and reload — the DOM rewriter only ever
 *  starts (or stays off) from a clean boot, so both directions reload. */
export function toggleAnonMode(): void {
  try {
    globalThis.localStorage?.setItem(MODE_KEY, enabled ? '0' : '1');
  } catch {
    return;
  }
  window.location.reload();
}

// ── Dictionary (real → alias), persisted for cross-reload stability ──────────

let dict: Record<string, string> = {};
let counters: Partial<Record<EntityClass, number>> = {};
const aliasValues = new Set<string>();
let dictVersion = 0;
let matcher: RegExp | null = null;
let matcherVersion = -1;

function loadDict(): void {
  try {
    const raw = globalThis.localStorage?.getItem(DICT_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as { dict?: Record<string, string>; counters?: Partial<Record<EntityClass, number>> };
    if (parsed && typeof parsed.dict === 'object') {
      dict = parsed.dict ?? {};
      counters = parsed.counters ?? {};
      for (const v of Object.values(dict)) aliasValues.add(v);
      dictVersion++;
    }
  } catch {
    /* corrupt or unavailable — start clean */
  }
}

let persistTimer: ReturnType<typeof setTimeout> | null = null;
function schedulePersist(): void {
  if (persistTimer) return;
  persistTimer = setTimeout(() => {
    persistTimer = null;
    try {
      globalThis.localStorage?.setItem(DICT_KEY, JSON.stringify({ dict, counters }));
    } catch {
      /* quota / unavailable */
    }
  }, 500);
}

function next(cls: EntityClass): number {
  const n = (counters[cls] ?? 0) + 1;
  counters[cls] = n;
  return n;
}

function fromPool(pool: readonly string[], n: number, sep: string): string {
  const i = n - 1;
  const cycle = Math.floor(i / pool.length);
  return pool[i % pool.length] + (cycle > 0 ? `${sep}${cycle + 1}` : '');
}

function nextCharacter(): { display: string; login: string } {
  const n = next('person');
  const i = n - 1;
  if (i < CAST.length) {
    const [display, login] = CAST[i];
    return { display, login };
  }
  const k = i - CAST.length + 1;
  return { display: `Toon ${k}`, login: `toon${k}` };
}

function emailFor(display: string): string {
  return `${display.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/ /g, '.')}@acme.com`;
}

const NODE_SUFFIX_RE = /-(design|automation|api|deployer|govern)$/;

function mint(value: string, cls: EntityClass): string {
  switch (cls) {
    case 'person': {
      const c = nextCharacter();
      return /\s/.test(value) ? c.display : c.login;
    }
    case 'email': {
      const c = nextCharacter();
      return emailFor(c.display);
    }
    case 'group': return fromPool(CREWS, next('group'), '-');
    case 'org': return 'Acme Corp';
    case 'project': {
      const code = fromPool(CODENAMES, next('project'), '');
      const isKeyish = /^[A-Z0-9_]+$/.test(value);
      return isKeyish ? code : `Project ${code.charAt(0)}${code.slice(1).toLowerCase()}`;
    }
    case 'object': return fromPool(ACME_WORDS, next('object'), '_');
    case 'connection': return `acme_${fromPool(ACME_WORDS, next('connection'), '_')}`;
    case 'codeenv': return `${fromPool(ACME_WORDS, next('codeenv'), '_')}_env`;
    case 'llm': return `acme-llm-${next('llm')}`;
    case 'hostlabel': return `Acme DSS ${next('hostlabel')}`;
    case 'hosturl': return `https://dss${next('hosturl')}.acme.com`;
    case 'nodeid': {
      const m = value.match(NODE_SUFFIX_RE);
      return m ? `acme-${m[1]}` : `acme-node-${next('nodeid')}`;
    }
    case 'installid': return `acme-install-${next('installid')}`;
    case 'cluster': return `acme-cluster-${next('cluster')}`;
    case 'namespace': return `acme-ns-${next('namespace')}`;
    case 'registry': {
      const n = next('registry');
      return `12345678901${n % 10}.dkr.ecr.us-east-1.amazonaws.com`;
    }
    case 'cloudid': return `acme-cloud-id-${next('cloudid')}`;
    case 'ip': {
      const n = next('ip');
      return `10.42.${Math.floor(n / 256) % 256}.${n % 256}`;
    }
  }
}

function register(rawValue: unknown, cls: EntityClass): void {
  if (typeof rawValue !== 'string') return;
  let value = rawValue.trim();
  if (cls === 'person' && value.startsWith('dssuser_')) value = value.slice('dssuser_'.length);
  if (value.length < 3 || value.length > 160) return;
  if (/^\d+$/.test(value)) return;
  if (dict[value] !== undefined || aliasValues.has(value)) return;
  const lower = value.toLowerCase();
  if (cls === 'person' && PERSON_STOP.has(lower)) return;
  if (cls === 'group' && GROUP_STOP.has(lower)) return;
  if (cls === 'hostlabel' && HOST_STOP.has(lower)) return;
  const alias = mint(value, cls);
  dict[value] = alias;
  aliasValues.add(alias);
  // Lowercase twin for uppercase project keys: K8s pod names and container
  // labels carry them lowercased. Length-gated to avoid eating common words.
  if (cls === 'project' && /^[A-Z0-9_]{5,}$/.test(value) && !/^\d/.test(value)) {
    const lc = value.toLowerCase();
    if (dict[lc] === undefined && !aliasValues.has(lc)) {
      dict[lc] = alias.toLowerCase();
      aliasValues.add(alias.toLowerCase());
    }
  }
  dictVersion++;
}

// ── Entity collection: a field-name-driven walk over any data payload ────────

const FIELD_MAP: Record<string, EntityClass> = {
  login: 'person', owner: 'person', ownerLogin: 'person', ownerDisplayName: 'person',
  displayName: 'person', authIdentifier: 'person', lastModifiedBy: 'person',
  runAsUser: 'person', lastEditor: 'person', dssSubmitter: 'person',
  submitter: 'person', createdBy: 'person', author: 'person', authors: 'person',
  instanceOwners: 'person', user: 'person',
  email: 'email', ownerEmail: 'email', userEmail: 'email', triage_recipient: 'email',
  triageRecipient: 'email',
  groups: 'group', group: 'group',
  company: 'org',
  projectKey: 'project', projectKeys: 'project', originProjectKey: 'project',
  creatorProjectKey: 'project', targetProjectKey: 'project',
  referencingProjects: 'project', projectKeyForSend: 'project', projectName: 'project',
  datasetName: 'object', recipeName: 'object', creatorRecipeName: 'object',
  objectName: 'object', assetName: 'object', notebookName: 'object',
  appId: 'object', scenarioName: 'object',
  connection: 'connection', connectionName: 'connection',
  codeEnvName: 'codeenv', codeEnvNames: 'codeenv', envName: 'codeenv',
  sourceEnvName: 'codeenv', targetEnvName: 'codeenv', codeEnv: 'codeenv',
  codeEnvKeys: 'codeenv',
  llmId: 'llm', friendlyName: 'llm',
  kubernetesNamespace: 'namespace', namespace: 'namespace', ns: 'namespace',
  clusterId: 'cluster', clusterName: 'cluster', kubeCtlContext: 'cluster',
  currentContext: 'cluster',
  registryUrl: 'registry', repositoryURL: 'registry',
  server: 'hosturl', instanceUrl: 'hosturl', url: 'hosturl',
  nodeId: 'nodeid', installId: 'installid',
  vpcId: 'cloudid', subnetIds: 'cloudid', securityGroups: 'cloudid',
};

interface UserLike { login?: unknown; displayName?: unknown; email?: unknown }

/** Users seed as linked triples so one character owns login + name + email. */
function seedUser(u: UserLike): void {
  const login = typeof u.login === 'string' ? u.login.trim() : '';
  if (!login || login.length < 3 || PERSON_STOP.has(login.toLowerCase())) return;
  if (dict[login] === undefined && !aliasValues.has(login)) {
    const c = nextCharacter();
    dict[login] = c.login;
    aliasValues.add(c.login);
    dictVersion++;
    const display = typeof u.displayName === 'string' ? u.displayName.trim() : '';
    if (display.length >= 3 && dict[display] === undefined && !aliasValues.has(display)) {
      dict[display] = c.display;
      aliasValues.add(c.display);
    }
    const email = typeof u.email === 'string' ? u.email.trim() : '';
    if (email.length >= 3 && dict[email] === undefined && !aliasValues.has(email)) {
      dict[email] = emailFor(c.display);
      aliasValues.add(emailFor(c.display));
    }
  }
}

function walkForEntities(v: unknown, depth: number): void {
  if (v == null || depth > 12) return;
  if (Array.isArray(v)) {
    for (const x of v) walkForEntities(x, depth + 1);
    return;
  }
  if (typeof v !== 'object') return;
  const o = v as Record<string, unknown>;
  for (const [k, val] of Object.entries(o)) {
    if (k === 'users' && Array.isArray(val)) {
      for (const u of val) if (u && typeof u === 'object') seedUser(u as UserLike);
      // fall through to the generic walk for groups etc.
    }
    if (k === 'codeEnvSizes' && val && typeof val === 'object' && !Array.isArray(val)) {
      for (const envName of Object.keys(val)) register(envName, 'codeenv');
      continue;
    }
    if (k === 'connectionDetails' || k === 'connectionHealth') {
      if (Array.isArray(val)) {
        for (const c of val) {
          const name = (c as { name?: unknown } | null)?.name;
          register(name, 'connection');
        }
      }
      continue;
    }
    if (k === 'clusters' && Array.isArray(val)) {
      for (const c of val) register((c as { name?: unknown } | null)?.name, 'cluster');
      // fall through: server/vpcId etc. picked up by the generic walk
    }
    const cls = FIELD_MAP[k]
      ?? ((k === 'name' || k === 'label') && ('projectKey' in o || 'projectName' in o)
        ? 'object'
        : undefined)
      // Host records ({id, label, url}): the label and the short host id both
      // render (host cards, feedback context, audit host column).
      ?? ((k === 'label' || k === 'id') && 'url' in o && 'label' in o
        ? 'hostlabel'
        : undefined);
    if (typeof val === 'string') {
      if (cls) register(val, cls);
    } else if (Array.isArray(val) && cls && val.every((x) => typeof x === 'string')) {
      for (const s of val) register(s, cls);
    } else {
      walkForEntities(val, depth + 1);
    }
  }
}

/** Feed any data payload (API response, parsedData, scan-store data) into the
 *  dictionary. No-op when the mode is off. */
export function anonCollect(value: unknown): void {
  if (!enabled || value == null) return;
  const before = dictVersion;
  try {
    walkForEntities(value, 0);
  } catch {
    /* never let collection break a data path */
  }
  if (dictVersion !== before) {
    schedulePersist();
    scheduleFullPass();
  }
}

// ── Text rewriting ───────────────────────────────────────────────────────────

const RE_ESCAPE = /[.*+?^${}()|[\]\\]/g;
const EMAIL_RE = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g;
const IPV4_RE = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
const ECR_ACCOUNT_RE = /\b\d{12}(?=\.dkr\.ecr)/g;

function getMatcher(): RegExp | null {
  if (matcherVersion === dictVersion) return matcher;
  matcherVersion = dictVersion;
  const terms = Object.keys(dict).sort((a, b) => b.length - a.length);
  matcher = terms.length
    ? new RegExp(
        `(?<![A-Za-z0-9])(?:${terms.map((t) => t.replace(RE_ESCAPE, '\\$&')).join('|')})(?![A-Za-z0-9])`,
        'g',
      )
    : null;
  return matcher;
}

/** Alias every known real entity inside `input`, then catch stray emails, IPs
 *  and ECR account ids. Identity function while the mode is off — safe to call
 *  unconditionally from chart label callbacks. */
export function anonText(input: string): string {
  if (!enabled || !input) return input;
  let out = input;
  const m = getMatcher();
  if (m) out = out.replace(m, (hit) => dict[hit] ?? hit);
  out = out.replace(EMAIL_RE, (e) => {
    if (e.endsWith('@acme.com')) return e;
    const known = dict[e];
    if (known) return known;
    register(e, 'email');
    return dict[e.trim()] ?? e;
  });
  out = out.replace(IPV4_RE, (ip) => {
    if (ip.startsWith('10.42.') || ip.startsWith('127.') || ip === '0.0.0.0') return ip;
    const known = dict[ip];
    if (known) return known;
    register(ip, 'ip');
    return dict[ip] ?? ip;
  });
  out = out.replace(ECR_ACCOUNT_RE, '123456789012');
  return out;
}

// ── DOM rewriter ─────────────────────────────────────────────────────────────

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'TEXTAREA', 'INPUT']);
const REWRITE_ATTRS = ['title', 'aria-label', 'alt', 'placeholder'];

let observer: MutationObserver | null = null;
let passTimer: ReturnType<typeof setTimeout> | null = null;

// SSE-fed scan stores never pass through fetchJson — harvest their data via the
// registry instead. Idempotent and re-checked on every mutation batch so
// lazily-registered stores are picked up too.
const subscribedStores = new WeakSet<RegisteredScanStore>();
function ensureScanSubscriptions(): void {
  for (const entry of getRegisteredScanStores()) {
    if (subscribedStores.has(entry)) continue;
    subscribedStores.add(entry);
    let t: ReturnType<typeof setTimeout> | null = null;
    entry.subscribe(() => {
      if (t) return;
      t = setTimeout(() => {
        t = null;
        anonCollect(entry.rawData?.());
      }, 400);
    });
  }
}

function rewriteTextNode(node: Text): void {
  const parent = node.parentElement;
  if (!parent) return;
  // Leave the subtree the user is actively editing alone (report slides are
  // contentEditable) — unfocused editable content still gets rewritten.
  if (parent.isContentEditable && document.activeElement?.contains(parent)) return;
  const value = node.nodeValue;
  if (!value) return;
  const replaced = anonText(value);
  if (replaced !== value) node.nodeValue = replaced;
}

function rewriteElementAttrs(el: Element): void {
  for (const attr of REWRITE_ATTRS) {
    const value = el.getAttribute(attr);
    if (!value) continue;
    const replaced = anonText(value);
    if (replaced !== value) el.setAttribute(attr, replaced);
  }
}

function applyToTree(root: Node): void {
  if (root.nodeType === Node.TEXT_NODE) {
    rewriteTextNode(root as Text);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE) {
    if (SKIP_TAGS.has((root as Element).tagName)) return;
    rewriteElementAttrs(root as Element);
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.nodeType === Node.ELEMENT_NODE && SKIP_TAGS.has((node as Element).tagName)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  let node = walker.nextNode();
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) rewriteTextNode(node as Text);
    else rewriteElementAttrs(node as Element);
    node = walker.nextNode();
  }
}

function scheduleFullPass(): void {
  if (!observer || passTimer) return;
  passTimer = setTimeout(() => {
    passTimer = null;
    applyToTree(document.body);
    observer?.takeRecords();
  }, 250);
}

/** Boot the DOM rewriter (main.tsx). No-op while the mode is off. */
export function initAnonMode(): void {
  if (!enabled || observer || typeof document === 'undefined') return;
  loadDict();
  observer = new MutationObserver((records) => {
    ensureScanSubscriptions();
    for (const r of records) {
      if (r.type === 'characterData' && r.target.nodeType === Node.TEXT_NODE) {
        rewriteTextNode(r.target as Text);
      } else if (r.type === 'attributes' && r.target.nodeType === Node.ELEMENT_NODE) {
        rewriteElementAttrs(r.target as Element);
      } else if (r.type === 'childList') {
        r.addedNodes.forEach((n) => applyToTree(n));
      }
    }
    // Discard the records our own writes just queued — everything above ran
    // synchronously, so nothing external can be interleaved with them.
    observer?.takeRecords();
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: REWRITE_ATTRS,
  });
  ensureScanSubscriptions();
  applyToTree(document.body);
  observer.takeRecords();
}
