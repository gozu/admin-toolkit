"""Plugin macro: config-tree object inventory (full history of surviving objects).

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
read <DIP_HOME>/config/projects/ regardless of webapp impersonation. Backs the
Adoption page's inventory layer.

Mines every project object JSON for its creationTag/versionTag (creator login,
created ms, last editor, last edit ms, versionNumber = save count after
creation) plus object type. Unlike the git/audit adoption spine this covers the
instance's full multi-year history — but only for objects that still exist
(survivorship bias; deleted work is invisible). The output shape is the
ObjectInventory accumulator ported from the diag-parser plugin
(resource/frontend/src/utils/inventoryCore.ts there) so the frontend
derivations transfer verbatim:

- JSON-bodied categories (datasets, recipes, webapps, dashboards, insights,
  saved models, scenarios, prompt studios, MES, agentic assets) are parsed and
  tag-mined.
- Metadata-only categories (notebooks, SQL notebooks, wiki articles, flow
  zones, visual analyses) carry no creation tags on disk (verified live) —
  they are counted by file mtime, which feeds the staleness histogram but
  never the human-attributed fields.
- Every human timestamp is floored at the project's arrival on this instance:
  max(project repo first commit, instance birth = root config repo first
  commit). Imported/copied projects keep foreign creationTags — and exports
  can even carry foreign git history — which would otherwise fabricate
  pre-instance history.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from dataiku.runnables import Runnable

# Timestamps outside 2000-01-01 .. now+1d are clock-skew garbage; dropping
# (not clipping) them avoids false spikes at the boundary months.
_MIN_PLAUSIBLE_MS = 946684800000  # 2000-01-01T00:00:00Z

# Recipe `type` -> family sets (mirrors objectFamilies.ts in diag-parser).
_PYTHON_RECIPE_TYPES = {
    'python', 'python_step', 'custom_python', 'pyspark', 'streaming_python', 'code_studio',
}
_R_RECIPE_TYPES = {'r', 'custom_r'}
_SQL_RECIPE_TYPES = {'sql_query', 'sql_script', 'spark_sql_query', 'hive', 'impala'}
_ML_RECIPE_TYPES = {
    'prediction_training', 'prediction_scoring', 'clustering_training', 'clustering_scoring',
    'clustering_cluster', 'evaluation', 'standalone_evaluation',
}
_VISUAL_RECIPE_TYPES = {
    'shaker', 'grouping', 'join', 'fuzzyjoin', 'sync', 'window', 'split', 'vstack',
    'sampling', 'distinct', 'topn', 'sort', 'pivot', 'export', 'download', 'update',
    'merge_folder', 'generate_features',
}

# JSON-bodied categories: project subdir -> (family, filename filter).
# The filter keeps payload siblings (recipe .shaker/.join/.py, scenario .py,
# wiki .md) and nested noise (analysis mltask files) out of the counts.
_JSON_CATEGORIES = [
    ('datasets', 'dataset'),
    ('recipes', 'recipe'),  # family resolved per-JSON from its `type`
    ('web_apps', 'webapp'),
    ('dashboards', 'dashboard'),
    ('insights', 'insight'),
    ('saved_models', 'saved-model'),
    ('scenarios', 'scenario'),
    ('prompt-studios', 'prompt-studio'),
    ('model_evaluation_stores', 'mes'),
    # Agentic/code assets stay in one bucket — the Adoption page reads them as
    # "other built objects".
    ('agent-tools', 'other'),
    ('agent_reviews', 'other'),
    ('knowledge-banks', 'other'),
    ('code-studios', 'other'),
    ('retrieval-augmented-llms', 'other'),
]


def _family_for_recipe_type(rtype, plugin_ids):
    if not rtype:
        return 'recipe-other'
    if rtype in _PYTHON_RECIPE_TYPES:
        return 'recipe-python'
    if rtype in _R_RECIPE_TYPES:
        return 'recipe-r'
    if rtype in _SQL_RECIPE_TYPES:
        return 'recipe-sql'
    if rtype in _ML_RECIPE_TYPES:
        return 'recipe-ml'
    if rtype in _VISUAL_RECIPE_TYPES:
        return 'recipe-visual'
    # Plugin recipe types embed the plugin id as a whole underscore-delimited
    # segment (same trick the code-env attribution uses).
    segs = set(rtype.split('_'))
    for pid in plugin_ids:
        if pid in segs:
            return 'recipe-plugin'
    return 'recipe-other'


def _month_key_utc(ms):
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return '%04d-%02d' % (dt.year, dt.month)


def _tag_login(tag):
    if not isinstance(tag, dict):
        return None
    by = tag.get('lastModifiedBy')
    login = (by or {}).get('login') if isinstance(by, dict) else None
    login = login.strip() if isinstance(login, str) else None
    return login or None


class _Inventory(object):
    """Python port of the diag-parser inventory accumulator (same output keys)."""

    def __init__(self):
        self.families = {}
        self.months = {}
        self.creators = {}
        self.projects = {}
        self.first_creation_ms = None
        self.last_edit_ms = None
        self.scanned = 0
        self.tagged_objects = 0
        self.errors = 0
        self.max_plausible_ms = int(time.time() * 1000) + 24 * 60 * 60 * 1000

    def _clamp_ms(self, ms, floor_ms=None):
        if isinstance(ms, (int, float)) and _MIN_PLAUSIBLE_MS <= ms <= self.max_plausible_ms:
            ms = int(ms)
            # Imported/copied projects keep their original creationTag/versionTag
            # payloads, which can predate this instance by years (verified live:
            # akaos DIAG_PARSER_BRANCH1 carries Sep '20 tags but its config git
            # repo starts Dec '22). Floor every human timestamp at the project's
            # arrival here so the inventory never claims history older than the
            # instance's own git spine.
            if floor_ms is not None and ms < floor_ms:
                ms = floor_ms
            return ms
        return None

    def _family(self, key):
        stats = self.families.get(key)
        if stats is None:
            stats = {
                'count': 0, 'tagged': 0, 'subtypes': {}, 'versionSum': 0,
                'editBuckets': {'v1': 0, 'v2to5': 0, 'v6to20': 0, 'v21plus': 0},
            }
            self.families[key] = stats
        return stats

    def _creator(self, login):
        stats = self.creators.get(login)
        if stats is None:
            stats = {
                'created': 0, 'byFamily': {}, 'firstCreatedMs': None, 'lastCreatedMs': None,
                'lastEditMs': None, 'editedNotCreated': 0, 'saves': 0,
            }
            self.creators[login] = stats
        return stats

    def _project(self, key):
        stats = self.projects.get(key)
        if stats is None:
            stats = {
                'objectCount': 0, 'byFamily': {}, 'creators': {}, 'handoffCount': 0,
                'savedOnce': 0, 'versionSum': 0, 'lastHumanEditMs': None, 'lastEditor': None,
                'lastEditMonthCounts': {},
            }
            self.projects[key] = stats
        return stats

    def _month(self, key):
        m = self.months.get(key)
        if m is None:
            m = {'month': key, 'total': 0, 'byFamily': {}, 'creators': {}}
            self.months[key] = m
        return m

    def _note_project_last_edit(self, proj, ms):
        mk = _month_key_utc(ms)
        proj['lastEditMonthCounts'][mk] = proj['lastEditMonthCounts'].get(mk, 0) + 1
        self.last_edit_ms = ms if self.last_edit_ms is None else max(self.last_edit_ms, ms)

    def add_config_object(self, project_key, obj, fam, arrival_ms=None):
        self.scanned += 1
        fam_stats = self._family(fam)
        fam_stats['count'] += 1
        subtype = obj.get('type') or obj.get('savedModelType')
        if isinstance(subtype, str) and subtype:
            fam_stats['subtypes'][subtype] = fam_stats['subtypes'].get(subtype, 0) + 1

        proj = self._project(project_key)
        proj['objectCount'] += 1
        proj['byFamily'][fam] = proj['byFamily'].get(fam, 0) + 1

        creation_tag = obj.get('creationTag')
        version_tag = obj.get('versionTag')
        creator_login = _tag_login(creation_tag)
        created_ms = self._clamp_ms((creation_tag or {}).get('lastModifiedOn')
                                    if isinstance(creation_tag, dict) else None, arrival_ms)
        editor_login = _tag_login(version_tag)
        edit_ms = self._clamp_ms((version_tag or {}).get('lastModifiedOn')
                                 if isinstance(version_tag, dict) else None, arrival_ms)
        # versionNumber = save count after creation (0 = never re-saved). Some
        # object kinds carry only a creationTag, so fall back to it.
        raw_version = None
        if isinstance(version_tag, dict):
            raw_version = version_tag.get('versionNumber')
        if raw_version is None and isinstance(creation_tag, dict):
            raw_version = creation_tag.get('versionNumber')
        version_number = raw_version if isinstance(raw_version, int) and raw_version >= 0 else None

        tagged = creator_login is not None and created_ms is not None
        if tagged:
            fam_stats['tagged'] += 1
            self.tagged_objects += 1

        if version_number is not None:
            fam_stats['versionSum'] += version_number
            proj['versionSum'] += version_number
            saves = version_number + 1  # creation counts as the first save
            if saves <= 1:
                fam_stats['editBuckets']['v1'] += 1
            elif saves <= 5:
                fam_stats['editBuckets']['v2to5'] += 1
            elif saves <= 20:
                fam_stats['editBuckets']['v6to20'] += 1
            else:
                fam_stats['editBuckets']['v21plus'] += 1
            if tagged and version_number <= 0:
                proj['savedOnce'] += 1

        if creator_login and created_ms is not None:
            m = self._month(_month_key_utc(created_ms))
            m['total'] += 1
            m['byFamily'][fam] = m['byFamily'].get(fam, 0) + 1
            m['creators'][creator_login] = m['creators'].get(creator_login, 0) + 1

            c = self._creator(creator_login)
            c['created'] += 1
            c['byFamily'][fam] = c['byFamily'].get(fam, 0) + 1
            c['firstCreatedMs'] = created_ms if c['firstCreatedMs'] is None else min(c['firstCreatedMs'], created_ms)
            c['lastCreatedMs'] = created_ms if c['lastCreatedMs'] is None else max(c['lastCreatedMs'], created_ms)
            c['lastEditMs'] = created_ms if c['lastEditMs'] is None else max(c['lastEditMs'], created_ms)

            proj['creators'][creator_login] = proj['creators'].get(creator_login, 0) + 1
            self.first_creation_ms = (created_ms if self.first_creation_ms is None
                                      else min(self.first_creation_ms, created_ms))

        if editor_login:
            c = self._creator(editor_login)
            c['saves'] += 1
            if edit_ms is not None:
                c['lastEditMs'] = edit_ms if c['lastEditMs'] is None else max(c['lastEditMs'], edit_ms)
            if creator_login and editor_login != creator_login:
                c['editedNotCreated'] += 1
                proj['handoffCount'] += 1

        # "Last edit" of the object = last save if present, else creation.
        object_last_edit_ms = edit_ms if edit_ms is not None else created_ms
        object_last_editor = editor_login or creator_login
        if object_last_edit_ms is not None:
            self._note_project_last_edit(proj, object_last_edit_ms)
            if object_last_editor and (proj['lastHumanEditMs'] is None
                                       or object_last_edit_ms > proj['lastHumanEditMs']):
                proj['lastHumanEditMs'] = object_last_edit_ms
                proj['lastEditor'] = object_last_editor

    def add_meta_only(self, project_key, fam, mtime_ms):
        self.scanned += 1
        self._family(fam)['count'] += 1
        proj = self._project(project_key)
        proj['objectCount'] += 1
        proj['byFamily'][fam] = proj['byFamily'].get(fam, 0) + 1
        # File mtime is a genuine last-save time but carries no login — feed
        # the staleness histogram, never the human-attributed fields.
        ms = self._clamp_ms(mtime_ms)
        if ms is not None:
            self._note_project_last_edit(proj, ms)

    def snapshot(self):
        return {
            'families': self.families,
            'creationMonths': [self.months[k] for k in sorted(self.months.keys())],
            'creators': self.creators,
            'projects': self.projects,
            'firstCreationMs': self.first_creation_ms,
            'lastEditMs': self.last_edit_ms,
            'scanned': self.scanned,
            'taggedObjects': self.tagged_objects,
            'errors': self.errors,
            'complete': True,
        }


def _plugin_ids(dip_home):
    ids = set()
    for sub in ('installed', 'dev'):
        base = os.path.join(dip_home, 'plugins', sub)
        try:
            for name in os.listdir(base):
                if os.path.isdir(os.path.join(base, name)):
                    ids.add(name)
        except OSError:
            pass
    return ids


def _load_json(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        return json.load(fh)


def _iter_json_files(base):
    """Direct-child .json files of a category dir (payload siblings excluded)."""
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in sorted(names):
        if name.endswith('.json'):
            path = os.path.join(base, name)
            if os.path.isfile(path):
                yield path


def _mtime_ms(path):
    try:
        return int(os.stat(path).st_mtime * 1000)
    except OSError:
        return None


def _scan_meta_only(inv, project_key, project_dir):
    """Categories whose bodies carry no creation tags: count + mtime only."""
    # Jupyter notebooks: ipython_notebooks/<name>.ipynb
    nb_dir = os.path.join(project_dir, 'ipython_notebooks')
    try:
        for name in os.listdir(nb_dir):
            if name.endswith('.ipynb'):
                inv.add_meta_only(project_key, 'notebook', _mtime_ms(os.path.join(nb_dir, name)))
    except OSError:
        pass
    # SQL notebooks: notebooks/sql/<ID>/params.json
    sql_dir = os.path.join(project_dir, 'notebooks', 'sql')
    try:
        for name in os.listdir(sql_dir):
            params = os.path.join(sql_dir, name, 'params.json')
            if os.path.isfile(params):
                inv.add_meta_only(project_key, 'sql-notebook', _mtime_ms(params))
    except OSError:
        pass
    # Wiki articles: wiki/articles/<id>.json (the .md sibling is the body)
    for path in _iter_json_files(os.path.join(project_dir, 'wiki', 'articles')):
        inv.add_meta_only(project_key, 'wiki', _mtime_ms(path))
    # Flow zones: zones/<id>.json (no tags on disk, verified live)
    for path in _iter_json_files(os.path.join(project_dir, 'zones')):
        inv.add_meta_only(project_key, 'zone', _mtime_ms(path))
    # Visual analyses: analysis/<ID>/core_params.json
    an_dir = os.path.join(project_dir, 'analysis')
    try:
        for name in os.listdir(an_dir):
            core = os.path.join(an_dir, name, 'core_params.json')
            if os.path.isfile(core):
                inv.add_meta_only(project_key, 'analysis', _mtime_ms(core))
    except OSError:
        pass


def _git_first_commit_ms(repo_dir):
    """Timestamp of a repo's first commit; None on no repo / git failure."""
    if not os.path.isdir(os.path.join(repo_dir, '.git')):
        return None
    try:
        # safe.directory: the repo is normally owned by the service account the
        # macro runs as, but a mixed-ownership repo would otherwise fail git's
        # dubious-ownership guard and silently skip the clamp.
        out = subprocess.run(
            ['git', '-c', 'safe.directory=%s' % repo_dir, '-C', repo_dir,
             'log', '--reverse', '--format=%at'],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    first = out.stdout.split('\n', 1)[0].strip()
    return int(first) * 1000 if first.isdigit() else None


def _arrival_floor_ms(project_dir, birth_ms):
    """When the project appeared on THIS instance. The project repo's first
    commit says "Imported project X" for imports and "Created project" for
    locals — but exports can carry the project's git history along, so a
    project repo can predate the instance itself (verified live: akaos born
    2026-01-22 per config/.git "Welcome to DSS!", yet an imported project repo
    starts Dec '22). The instance-birth floor wins over anything older."""
    arrival = _git_first_commit_ms(project_dir)
    if birth_ms is None:
        return arrival
    return birth_ms if arrival is None else max(arrival, birth_ms)


def _build_inventory(dip_home):
    projects_dir = os.path.join(dip_home, 'config', 'projects')
    plugin_ids = _plugin_ids(dip_home)
    inv = _Inventory()

    try:
        project_keys = sorted(
            k for k in os.listdir(projects_dir)
            if os.path.isdir(os.path.join(projects_dir, k))
        )
    except OSError as exc:
        return {'ok': False, 'error': 'cannot list %s: %s' % (projects_dir, exc)}

    # Instance birth = first commit of the root config repo ("Welcome to
    # DSS!"), written at install and never part of any project import.
    birth_ms = _git_first_commit_ms(os.path.join(dip_home, 'config'))

    for project_key in project_keys:
        project_dir = os.path.join(projects_dir, project_key)
        arrival_ms = _arrival_floor_ms(project_dir, birth_ms)
        for subdir, family in _JSON_CATEGORIES:
            for path in _iter_json_files(os.path.join(project_dir, subdir)):
                try:
                    obj = _load_json(path)
                except (OSError, ValueError):
                    inv.errors += 1
                    continue
                if not isinstance(obj, dict):
                    inv.errors += 1
                    continue
                fam = (_family_for_recipe_type(obj.get('type'), plugin_ids)
                       if family == 'recipe' else family)
                inv.add_config_object(project_key, obj, fam, arrival_ms)
        _scan_meta_only(inv, project_key, project_dir)

    result = inv.snapshot()
    result['ok'] = True
    result['generatedAtMs'] = int(time.time() * 1000)
    result['projectCount'] = len(project_keys)
    return result


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set on host'})
        try:
            result = _build_inventory(dip_home)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:240]}'})
        return json.dumps(result)
