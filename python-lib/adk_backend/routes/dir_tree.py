"""Directory-tree route: footprint-derived size tree of the DSS data dir."""
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.footprint import _compute_footprint_payload, _footprint_details_map
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home
from adk_backend.utils import _coerce_int

bp = Blueprint('dir_tree', __name__)

_LOGGER = logging.getLogger(__name__)


def _scope_root(scope: str, project_key: Optional[str]) -> Dict[str, str]:
    if scope == 'all':
        return {'name': '/', 'path': '/'}
    if scope == 'global':
        return {'name': 'global', 'path': '/dss-data/global'}
    if scope == 'project' and project_key:
        return {'name': project_key, 'path': f'/dss-data/projects/{project_key}'}
    return {'name': 'dss_data', 'path': '/dss-data'}


def _build_footprint_node(name: str, path: str, footprint: Any, depth: int, max_depth: int,
                          bonus_depth: int = 0) -> Dict[str, Any]:
    details = _footprint_details_map(footprint)
    children: List[Dict[str, Any]] = []
    has_hidden = False
    effective_max = max_depth + bonus_depth
    if depth < effective_max:
        # Pre-sort children by size to identify top-N for adaptive depth
        child_items = []
        for child_name, child_footprint in details.items():
            child_size = _coerce_int(child_footprint.get('size'), 0)
            child_items.append((child_name, child_footprint, child_size))
        child_items.sort(key=lambda x: x[2], reverse=True)

        top_n = 5
        for idx, (child_name, child_footprint, _child_size) in enumerate(child_items):
            clean_name = str(child_name).strip('/') or str(child_name)
            child_path = f"{path.rstrip('/')}/{clean_name}" if path != '/' else f"/{clean_name}"
            child_bonus = 2 if (idx < top_n and bonus_depth == 0 and depth == 0) else bonus_depth
            child = _build_footprint_node(clean_name, child_path, child_footprint, depth + 1, max_depth,
                                          bonus_depth=child_bonus)
            children.append(child)
    elif details:
        has_hidden = True

    children.sort(key=lambda c: c.get('size', 0), reverse=True)

    size = _coerce_int(footprint.get('size'), 0)
    file_count = _coerce_int(footprint.get('nbFiles'), 0)

    if size <= 0 and children:
        size = sum(child['size'] for child in children)
    if file_count <= 0 and children:
        file_count = sum(child['fileCount'] for child in children)

    own_size = max(0, size - sum(child['size'] for child in children))
    locations_raw = footprint.get('locations')
    locations: List[str] = []
    if isinstance(locations_raw, list):
        locations = [str(loc) for loc in locations_raw if loc is not None and str(loc).strip()]
    elif isinstance(locations_raw, str) and locations_raw.strip():
        locations = [locations_raw.strip()]

    if not children and not details:
        file_count = max(file_count, 1)

    return {
        'name': name,
        'path': path,
        'size': size,
        'ownSize': own_size,
        'isDirectory': True,
        'children': children,
        'fileCount': file_count,
        'depth': depth,
        'hasHiddenChildren': has_hidden,
        'locations': locations,
    }


def _find_footprint_subtree(
    root_footprint: Any,
    root_path: str,
    target_path: str,
) -> Optional[Tuple[str, str, Any]]:
    """Locate target subtree using only Dataiku footprint details."""
    abs_root = os.path.abspath(str(root_path or '/'))
    abs_target = os.path.abspath(str(target_path or abs_root))
    if abs_target == abs_root:
        return (str(os.path.basename(abs_root) or abs_root or '/'), abs_root, root_footprint)
    root_prefix = abs_root.rstrip('/') + '/'
    if not abs_target.startswith(root_prefix):
        return None

    rel = abs_target[len(root_prefix):]
    parts = [part for part in rel.split('/') if part]
    current = root_footprint
    current_path = abs_root
    current_name = str(os.path.basename(abs_root) or abs_root or '/')

    for part in parts:
        details = _footprint_details_map(current)
        if not details:
            return None
        next_footprint = details.get(part)
        if next_footprint is None:
            # Be tolerant to slash formatting differences.
            for key, value in details.items():
                if str(key).strip('/') == part:
                    next_footprint = value
                    break
        if next_footprint is None:
            return None
        current = next_footprint
        current_name = part
        current_path = f"{current_path.rstrip('/')}/{part}" if current_path != '/' else f"/{part}"

    return (current_name, current_path, current)


def _build_dir_tree_from_footprint(
    client: Any,
    dip_home: str,
    max_depth: int,
    target_path: Optional[str] = None,
    scope: str = 'dss',
    project_key: Optional[str] = None,
    footprint_payload: Optional[Any] = None,
) -> Dict[str, Any]:
    scope = scope if scope in ('dss', 'project') else 'dss'
    if footprint_payload is not None:
        root_footprint = footprint_payload
    else:
        footprint_scope = 'all-dss' if scope == 'dss' else scope
        root_footprint = _compute_footprint_payload(client, footprint_scope, project_key)
    root_meta = _scope_root(scope, project_key)
    root_path = root_meta['path']

    if not root_footprint:
        _LOGGER.warning("[dir-tree] footprint payload unavailable scope=%s project=%s", scope, project_key)
        if target_path:
            return {'node': None}
        return {
            'root': None,
            'totalSize': 0,
            'totalFiles': 0,
            'rootPath': root_path,
            'scope': scope,
            'projectKey': project_key,
        }

    if target_path:
        subtree = _find_footprint_subtree(root_footprint, root_path, target_path)
        if subtree is None:
            return {'node': None}
        node_name, node_path, node_footprint = subtree
        node = _build_footprint_node(node_name, node_path, node_footprint, 0, max_depth)
        return {'node': node}

    root_node = _build_footprint_node(root_meta['name'], root_path, root_footprint, 0, max_depth)
    return {
        'root': root_node,
        'totalSize': root_node['size'],
        'totalFiles': root_node['fileCount'],
        'rootPath': root_node['path'],
        'scope': scope,
        'projectKey': project_key,
    }


@bp.route('/api/dir-tree')
def api_dir_tree():
    client = g.client
    dip_home = _dip_home()
    max_depth = request.args.get('maxDepth', type=int) or 3
    path = request.args.get('path')
    raw_scope = (request.args.get('scope') or 'dss').strip().lower()
    if raw_scope in ('global', 'all', 'unknown'):
        raw_scope = 'dss'
    scope = raw_scope if raw_scope in ('dss', 'project') else 'dss'
    project_key = (request.args.get('projectKey') or '').strip() or None
    if scope != 'project':
        project_key = None

    # Layer 1: cache the raw footprint payload (expensive DSS API call)
    footprint_scope = 'all-dss' if scope == 'dss' else scope
    footprint_cache_key = f"footprint:{footprint_scope}:{project_key or '-'}"

    def footprint_loader():
        return _compute_footprint_payload(client, footprint_scope, project_key)

    cached_footprint = _cache_get(footprint_cache_key, _BACKEND_SETTINGS['cache_ttl_dir_tree'], footprint_loader)

    # Layer 2: cache the tree view (cheap in-memory tree build from cached payload)
    tree_cache_key = f"dir_tree:{scope}:{project_key or '-'}:{path or 'root'}:{max_depth}"

    def tree_loader():
        return _build_dir_tree_from_footprint(
            client,
            dip_home,
            max_depth,
            target_path=path,
            scope=scope,
            project_key=project_key,
            footprint_payload=cached_footprint,
        )

    data = _cache_get(tree_cache_key, _BACKEND_SETTINGS['cache_ttl_dir_tree'], tree_loader)
    return jsonify(data)
