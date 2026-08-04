"""Projects -> App Instances: what App-as-recipe runs leave behind.

A DSS app can be used as a recipe (`App_<appId>`, `APP_RECIPE_BASE_ID` in the
backend). Every run of such a recipe instantiates the app into a throwaway
project keyed `RUN_<recipeName>_<random>` and deletes it when the run succeeds
— unless the recipe's Advanced tab has "Keep instance" ticked
(`params.keepInstance`, help text "For investigating failures"). Left on, every
single run permanently adds a project, which is how instance counts run away.

Three populations matter, mirroring DSS's own AppAsRecipeInstanceDetector
(sanity-check codes WARN_APP_AS_RECIPE_TOO_MANY_INSTANCES and
WARN_APP_AS_RECIPE_HAS_ORPHAN_INSTANCES):

  1. recipes with keepInstance ON  — the cause; every future run adds a project
  2. leftovers with keepInstance OFF — failed runs, which never reach the delete
  3. orphans — the creating recipe is gone, so nothing will ever clean them up

Cost shape: the app list and the project list are one API call each and carry
`instanceCount` / `projectAppType` + `generatingAppId` respectively. Finding
keepInstance is the expensive half — `list_recipes()` omits `params`, so every
`App_*` recipe needs its own settings fetch. Hence SSE: the cheap inventory
lands immediately and the per-project recipe sweep streams behind it.

Exact instance -> recipe attribution comes from the app-instances macro
(`appInstanceCreatorFullId` is stripped from every public-API projection). If
the macro is unavailable the page degrades to app-level counts and says so —
it never guesses attribution from the project label.
"""

import json
import logging
import time
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional, Set, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import ThreadPoolExecutor
from adk_backend.macros import _app_instances_macro
from adk_backend.utils import _parallel_workers, _sse_response, advanced

bp = Blueprint('app_instances', __name__)

_LOGGER = logging.getLogger(__name__)

# com.dataiku.dip.recipes.fromapp.AppRecipeMeta#APP_RECIPE_BASE_ID
_APP_RECIPE_PREFIX = 'App_'


def _app_id_from_recipe_type(recipe_type: str) -> str:
    """'App_PROJECT_SALES' -> 'PROJECT_SALES'. The app id is the recipe type
    minus the fixed prefix; project-backed apps are 'PROJECT_<projectKey>'."""
    return recipe_type[len(_APP_RECIPE_PREFIX):] if recipe_type.startswith(_APP_RECIPE_PREFIX) else ''


def _scan_project_recipes(client: Any, project_key: str) -> Dict[str, Any]:
    """App_* recipes in one project, each with its keepInstance flag.

    Two-stage on purpose: the listing is one call and gives `type`, so the
    per-recipe settings fetch (the only place `params.keepInstance` exists) is
    paid only for recipes that are actually app recipes. A project we cannot
    read degrades to an error row — never an exception that kills the stream.
    """
    row: Dict[str, Any] = {'projectKey': project_key, 'recipes': [], 'error': None}
    try:
        project = client.get_project(project_key)
        listing = project.list_recipes() or []
    except Exception as exc:
        row['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return row

    for entry in listing:
        if not isinstance(entry, dict):
            continue
        recipe_type = str(entry.get('type') or '')
        if not recipe_type.startswith(_APP_RECIPE_PREFIX):
            continue
        name = str(entry.get('name') or '')
        recipe_row: Dict[str, Any] = {
            'projectKey': project_key,
            'name': name,
            'fullId': '%s.%s' % (project_key, name),
            'appId': _app_id_from_recipe_type(recipe_type),
            'keepInstance': None,
            'error': None,
        }
        try:
            raw = project.get_recipe(name).get_settings().get_recipe_raw_definition()
            params = raw.get('params') if isinstance(raw, dict) else None
            recipe_row['keepInstance'] = bool((params or {}).get('keepInstance'))
        except Exception as exc:
            recipe_row['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        row['recipes'].append(recipe_row)
    return row


def _collect_apps(client: Any) -> List[Dict[str, Any]]:
    """One call. `instanceCount`/`useAsRecipe`/`origin` come straight from DSS."""
    try:
        apps = client.list_apps() or []
    except Exception as exc:
        _LOGGER.warning("[app-instances] list_apps failed: %s", exc)
        return []
    out = []
    for app in apps:
        if not isinstance(app, dict):
            continue
        owners = app.get('instanceOwners')
        out.append({
            'appId': str(app.get('appId') or ''),
            'label': str(app.get('label') or app.get('appId') or ''),
            'origin': str(app.get('origin') or ''),
            'originProjectKey': app.get('originProjectKey') or None,
            'useAsRecipe': bool(app.get('useAsRecipe')),
            'instanceCount': int(app.get('instanceCount') or 0),
            'lastInstantiation': app.get('lastInstantiation') or None,
            'instanceOwners': [str(o.get('login') or '') for o in owners
                               if isinstance(o, dict)] if isinstance(owners, list) else [],
        })
    return out


def _collect_projects(client: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(instance rows, project keys worth scanning for App_ recipes).

    `projectAppType` and `generatingAppId` ride along in the project listing —
    verified live on DSS 14.7 — so the whole instance inventory is one call.
    App-instance projects are excluded from the recipe sweep: they are copies
    of a template's flow, so any App_ recipe inside one is a duplicate of the
    template's own, not a distinct cause.
    """
    try:
        projects = client.list_projects() or []
    except Exception as exc:
        _LOGGER.warning("[app-instances] list_projects failed: %s", exc)
        return [], []

    instances: List[Dict[str, Any]] = []
    scannable: List[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        key = str(project.get('projectKey') or '')
        if not key:
            continue
        app_type = str(project.get('projectAppType') or 'REGULAR')
        if app_type == 'APP_INSTANCE':
            instances.append({
                'projectKey': key,
                'name': str(project.get('name') or key),
                'owner': str(project.get('ownerLogin') or 'Unknown'),
                'generatingAppId': project.get('generatingAppId') or None,
                'generatingAppVersion': project.get('generatingAppVersion') or None,
                'lastModified': (project.get('versionTag') or {}).get('lastModifiedOn'),
                # Filled from the macro when it is available.
                'creatorFullId': None,
                'creatorProjectKey': None,
                'creatorRecipeName': None,
                'isTemporary': None,
                'orphan': None,
            })
        else:
            scannable.append(key)
    return instances, scannable


def _attribute_instances(client: Any, instances: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold the macro's config-tree read into the instance rows.

    Returns the attribution status the page renders. A macro failure is
    reported, not papered over: without `appInstanceCreatorFullId` there is no
    trustworthy instance -> recipe edge, so attribution stays null and orphan
    detection is disabled rather than guessed from the project label.
    """
    try:
        result = _app_instances_macro(client)
    except Exception as exc:
        _LOGGER.warning("[app-instances] macro unavailable: %s", exc)
        return {'available': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}

    if not result.get('ok'):
        return {'available': False, 'error': str(result.get('error') or 'macro returned ok=false')}

    by_key = {}
    for entry in result.get('rows') or []:
        if isinstance(entry, dict) and entry.get('projectKey'):
            by_key[str(entry['projectKey'])] = entry

    attributed = 0
    for instance in instances:
        entry = by_key.get(instance['projectKey'])
        if not entry:
            continue
        full_id = entry.get('appInstanceCreatorFullId')
        instance['isTemporary'] = bool(entry.get('isTemporaryAppInstance'))
        if not instance['generatingAppId']:
            instance['generatingAppId'] = entry.get('generatingAppId')

        # appInstanceCreatorFullId is overloaded (verified live on DSS 14.7):
        # for an instance created from the app's homepage it holds the APP ID,
        # and only for a recipe run does it hold '<projectKey>.<recipeName>'.
        # Comparing against generatingAppId is the exact discriminator —
        # splitting on '.' alone would mint a phantom recipe out of any app id
        # that happens to contain one.
        app_id = entry.get('generatingAppId') or instance['generatingAppId']
        if isinstance(full_id, str) and full_id and full_id != app_id and '.' in full_id:
            project_key, _, recipe_name = full_id.partition('.')
            instance['creatorFullId'] = full_id
            instance['creatorProjectKey'] = project_key
            instance['creatorRecipeName'] = recipe_name
            attributed += 1

    unreadable = result.get('unreadable') or []
    return {
        'available': True,
        'error': None,
        'attributed': attributed,
        'unreadable': len(unreadable) if isinstance(unreadable, list) else 0,
    }


@bp.route('/api/app-instances/scan')
def api_app_instances_scan():
    """Stream the app-instance inventory, then the App_ recipe sweep, via SSE."""
    def generate():
        t0 = time.time()
        client = g.client

        apps = _collect_apps(client)
        instances, scannable = _collect_projects(client)
        attribution = _attribute_instances(client, instances)

        yield "event: inventory\ndata: %s\n\n" % json.dumps({
            'apps': apps,
            'instances': instances,
            'attribution': attribution,
            'projectsToScan': len(scannable),
        })

        scanned = 0
        recipes: List[Dict[str, Any]] = []
        failed_projects: List[Dict[str, str]] = []
        workers = max(1, min(8, _parallel_workers()))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_project_recipes, client, key): key for key in scannable}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    # A worker raising past its own guards must not kill the
                    # stream — the page would hang on a partial sweep with no
                    # done event. Degrade that project to an error row.
                    _LOGGER.exception("[app-instances] worker failed for %s", key)
                    row = {'projectKey': key, 'recipes': [],
                           'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
                scanned += 1
                if row['error']:
                    failed_projects.append({'projectKey': key, 'error': row['error']})
                if row['recipes']:
                    recipes.extend(row['recipes'])
                yield "event: project\ndata: %s\n\n" % json.dumps({
                    'projectKey': key,
                    'recipes': row['recipes'],
                    'error': row['error'],
                    'scanned': scanned,
                })

        # Orphans need BOTH halves: a creator id from the macro and the full
        # recipe sweep to check it against. Either missing ⇒ unknown, not zero.
        known_recipe_ids: Set[str] = {r['fullId'] for r in recipes}
        orphans = 0
        if attribution.get('available') and not failed_projects:
            for instance in instances:
                full_id = instance.get('creatorFullId')
                if full_id:
                    instance['orphan'] = full_id not in known_recipe_ids
                    if instance['orphan']:
                        orphans += 1

        yield "event: done\ndata: %s\n\n" % json.dumps({
            'projectsScanned': scanned,
            'appRecipes': len(recipes),
            'keepInstanceOn': sum(1 for r in recipes if r['keepInstance'] is True),
            'instances': len(instances),
            'orphans': orphans if (attribution.get('available') and not failed_projects) else None,
            'failedProjects': failed_projects,
            'totalMs': int((time.time() - t0) * 1000),
        })

    return _sse_response(generate)


@bp.route('/api/app-instances/keep-instance', methods=['POST'])
@advanced
def api_app_instances_set_keep_instance():
    """Turn an App recipe's `params.keepInstance` off (or back on).

    Fixes the cause rather than the symptom: with the flag off, DSS deletes the
    instance project at the end of each successful run again. Already-existing
    instances are untouched — this route never deletes a project.
    """
    body = request.get_json(force=True, silent=True) or {}
    project_key = str(body.get('projectKey') or '').strip()
    recipe_name = str(body.get('recipeName') or '').strip()
    keep = body.get('keepInstance')

    if not project_key or not recipe_name:
        return jsonify({'error': 'projectKey and recipeName are required'}), 400
    if not isinstance(keep, bool):
        return jsonify({'error': 'keepInstance must be a boolean'}), 400

    client = g.client
    try:
        project = client.get_project(project_key)
        recipe = project.get_recipe(recipe_name)
        settings = recipe.get_settings()
        raw = settings.get_recipe_raw_definition()
    except Exception as exc:
        _LOGGER.error("[app-instances] cannot read %s.%s: %s", project_key, recipe_name, exc)
        return jsonify({'error': 'Cannot read recipe: %s' % str(exc)[:200]}), 404

    recipe_type = str(raw.get('type') or '')
    if not recipe_type.startswith(_APP_RECIPE_PREFIX):
        # Only App recipes have this param; refuse rather than write a key that
        # a different recipe type would silently carry around.
        return jsonify({'error': 'Not an App-as-recipe recipe (type=%s)' % recipe_type}), 400

    previous = bool((raw.get('params') or {}).get('keepInstance'))
    try:
        raw.setdefault('params', {})['keepInstance'] = keep
        settings.save()
    except Exception as exc:
        _LOGGER.error("[app-instances] save failed for %s.%s: %s", project_key, recipe_name, exc)
        return jsonify({'error': 'Save failed: %s' % str(exc)[:200]}), 500

    _LOGGER.info("[app-instances] %s.%s keepInstance %s -> %s",
                 project_key, recipe_name, previous, keep)
    return jsonify({
        'ok': True,
        'projectKey': project_key,
        'recipeName': recipe_name,
        'previous': previous,
        'keepInstance': keep,
    })
