"""Directory tree scanning with df mount-usage overlay."""

import logging
import os
from typing import Any, Dict, List, Optional

from adk_backend.sysinfo import _format_size_human, _summarize_df_mounts

_LOGGER = logging.getLogger(__name__)

def _make_unscanned_usage_node(parent_path: str, depth: int, size: int, label: str) -> Dict[str, Any]:
    clean_parent = parent_path.rstrip('/') or '/'
    virtual_path = '/.unscanned' if clean_parent == '/' else f"{clean_parent}/.unscanned"
    return {
        'name': label,
        'path': virtual_path,
        'size': int(max(0, size)),
        'ownSize': int(max(0, size)),
        'isDirectory': False,
        'children': [],
        'fileCount': 0,
        'depth': depth,
        'hasHiddenChildren': False,
    }


def _overlay_mount_usage_on_node(
    node: Dict[str, Any],
    node_path: str,
    depth: int,
    mount_summary: Dict[str, Any],
    debug_state: Dict[str, Any],
) -> None:
    mount_by_path = mount_summary.get('byPath') or {}
    target_used = 0
    for mount_path, used in mount_by_path.items():
        if mount_path == node_path or mount_path.startswith(node_path.rstrip('/') + '/'):
            target_used += int(used or 0)
    if target_used <= 0:
        return

    scanned = int(node.get('size') or 0)
    if target_used <= scanned:
        return

    delta = target_used - scanned
    unknown = _make_unscanned_usage_node(node_path, depth + 1, delta, '[unscanned usage]')
    if node.get('isDirectory'):
        children = list(node.get('children') or [])
        children.append(unknown)
        children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
        node['children'] = children
        node['hasHiddenChildren'] = True
    node['size'] = target_used
    debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)


def _apply_df_overlay_to_root_tree(root_node: Dict[str, Any], debug_state: Dict[str, Any]) -> Dict[str, Any]:
    mount_summary = _summarize_df_mounts()
    included = mount_summary.get('included') or []
    excluded = mount_summary.get('excluded') or []
    top_buckets = mount_summary.get('topBuckets') or {}

    children = list(root_node.get('children') or [])
    child_by_path: Dict[str, Dict[str, Any]] = {}
    for child in children:
        child_path = str(child.get('path') or '')
        if child_path:
            child_by_path[child_path] = child

    for top_path, bucket in top_buckets.items():
        bucket_size = int((bucket or {}).get('size') or 0)
        bucket_mounts = list((bucket or {}).get('mounts') or [])
        child = child_by_path.get(top_path)
        if child is None:
            child = {
                'name': os.path.basename(top_path) or top_path,
                'path': top_path,
                'size': 0,
                'ownSize': 0,
                'isDirectory': True,
                'children': [],
                'fileCount': 0,
                'depth': 1,
                'hasHiddenChildren': True,
            }
            children.append(child)
            child_by_path[top_path] = child

        scanned_size = int(child.get('size') or 0)
        if bucket_size > scanned_size:
            delta = bucket_size - scanned_size
            unknown = _make_unscanned_usage_node(top_path, int(child.get('depth') or 1) + 1, delta, '[unscanned usage]')
            child_children = list(child.get('children') or [])
            child_children.append(unknown)
            child_children.sort(key=lambda entry: int(entry.get('size') or 0), reverse=True)
            child['children'] = child_children
            child['size'] = bucket_size
            child['hasHiddenChildren'] = True
            debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)
        child['mountPaths'] = sorted(set(bucket_mounts))

    root_used = int(mount_summary.get('rootUsed') or 0)
    mounted_used = int(mount_summary.get('mountedUsed') or 0)
    total_used = int(mount_summary.get('totalUsed') or 0)
    mounted_top_paths = set(top_buckets.keys())
    scanned_root_used = sum(
        int(child.get('size') or 0)
        for child in children
        if str(child.get('path') or '') not in mounted_top_paths
    )
    if root_used > scanned_root_used:
        delta = root_used - scanned_root_used
        children.append(_make_unscanned_usage_node('/', 1, delta, '[unscanned rootfs usage]'))
        debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)

    children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
    root_node['children'] = children
    if total_used > 0:
        root_node['size'] = total_used

    debug_state['dfRootUsed'] = root_used
    debug_state['dfMountedUsed'] = mounted_used
    debug_state['dfTotalUsed'] = total_used
    debug_state['dfMountsIncluded'] = [
        {
            'path': str(mount.get('path') or ''),
            'size': int(mount.get('used') or 0),
            'humanSize': _format_size_human(int(mount.get('used') or 0)),
            'fsType': str(mount.get('fsType') or ''),
        }
        for mount in sorted(included, key=lambda item: int(item.get('used') or 0), reverse=True)[:24]
    ]
    debug_state['dfMountsExcluded'] = [
        {
            'path': str(mount.get('path') or ''),
            'size': int(mount.get('used') or 0),
            'humanSize': _format_size_human(int(mount.get('used') or 0)),
            'fsType': str(mount.get('fsType') or ''),
        }
        for mount in sorted(excluded, key=lambda item: int(item.get('used') or 0), reverse=True)[:12]
    ]
    debug_state['dfTopMountBuckets'] = [
        {
            'path': path,
            'size': int(bucket.get('size') or 0),
            'humanSize': _format_size_human(int(bucket.get('size') or 0)),
            'mounts': sorted(bucket.get('mounts') or []),
        }
        for path, bucket in sorted(top_buckets.items(), key=lambda item: int((item[1] or {}).get('size') or 0), reverse=True)[:12]
    ]
    return mount_summary



def _build_dir_tree(
    root_path: str,
    max_depth: int,
    target_path: Optional[str] = None,
    approximate_limit: bool = False,
) -> Dict[str, Any]:
    root_path = os.path.abspath(root_path)
    target = os.path.abspath(target_path) if target_path else root_path
    if not target.startswith(root_path):
        target = root_path
    max_depth = max(1, int(max_depth or 1))

    exclude_virtual_mounts = root_path == '/'
    excluded_prefixes = ('/proc', '/sys', '/dev', '/run') if exclude_virtual_mounts else tuple()
    skip_symlink_entries = exclude_virtual_mounts

    def should_skip_path(path: str) -> bool:
        normalized = os.path.abspath(path)
        for prefix in excluded_prefixes:
            if normalized == prefix or normalized.startswith(prefix + os.sep):
                return True
        return False

    debug_state: Dict[str, Any] = {
        'rootPath': root_path,
        'targetPath': target,
        'maxDepth': max_depth,
        'approximateLimit': bool(approximate_limit),
        'excludedPrefixes': list(excluded_prefixes),
        'nodesVisited': 0,
        'dirsVisited': 0,
        'filesVisited': 0,
        'entriesScanned': 0,
        'symlinksSeen': 0,
        'skippedSymlinks': 0,
        'skippedEntries': 0,
        'statErrors': 0,
        'scanErrors': 0,
        'largeLeafs': [],
        'errors': [],
        'permissionDeniedPaths': [],
        'overlayUnknownBytes': 0,
        'topChildren': [],
        'specialMountTotals': [],
    }

    def record_error(kind: str, path: str, exc: Exception) -> None:
        if isinstance(exc, PermissionError):
            denied = debug_state['permissionDeniedPaths']
            if path not in denied and len(denied) < 16:
                denied.append(path)
        if len(debug_state['errors']) >= 12:
            return
        debug_state['errors'].append({
            'kind': kind,
            'path': path,
            'error': str(exc),
        })

    def record_large_leaf(path: str, size: int, reason: str) -> None:
        if size <= 0:
            return
        # Keep a short list to avoid bloating responses.
        items: List[Dict[str, Any]] = debug_state['largeLeafs']
        items.append({
            'path': path,
            'size': size,
            'humanSize': _format_size_human(size),
            'reason': reason,
        })
        items.sort(key=lambda item: int(item.get('size') or 0), reverse=True)
        del items[12:]

    def depth_for(path: str) -> int:
        if path == root_path:
            return 0
        relative = os.path.relpath(path, root_path)
        if relative in ('.', ''):
            return 0
        return relative.count(os.sep) + 1

    def make_node(
        path: str,
        is_directory: bool,
        size: int,
        own_size: int,
        file_count: int,
        depth: int,
        has_hidden_children: bool,
        children: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            'name': os.path.basename(path) or path,
            'path': path,
            'size': int(max(0, size)),
            'ownSize': int(max(0, own_size)),
            'isDirectory': bool(is_directory),
            'children': children or [],
            'fileCount': int(max(0, file_count)),
            'depth': int(max(0, depth)),
            'hasHiddenChildren': bool(has_hidden_children),
        }

    def summarize_directory(path: str, own_size: int) -> Dict[str, Any]:
        total_size = int(max(0, own_size))
        total_files = 0
        has_children = False

        if approximate_limit:
            def on_walk_error(exc: OSError) -> None:
                debug_state['scanErrors'] += 1
                record_error('walk', getattr(exc, 'filename', path) or path, exc)

            for walk_root, dirs, files in os.walk(path, topdown=True, followlinks=False, onerror=on_walk_error):
                filtered_dirs: List[str] = []
                for dir_name in list(dirs):
                    dir_path = os.path.join(walk_root, dir_name)
                    debug_state['entriesScanned'] += 1
                    if should_skip_path(dir_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if os.path.islink(dir_path):
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    filtered_dirs.append(dir_name)
                    has_children = True
                dirs[:] = filtered_dirs

                for file_name in files:
                    file_path = os.path.join(walk_root, file_name)
                    debug_state['entriesScanned'] += 1
                    if should_skip_path(file_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if os.path.islink(file_path):
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    has_children = True
                    try:
                        file_stat = os.lstat(file_path)
                        file_size = int(max(0, file_stat.st_size))
                        total_size += file_size
                        total_files += 1
                        debug_state['filesVisited'] += 1
                        if file_size >= 100 * 1024 ** 3:
                            record_large_leaf(file_path, file_size, 'walk-depth-limit')
                    except Exception as exc:
                        debug_state['statErrors'] += 1
                        record_error('stat', file_path, exc)
        else:
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        debug_state['entriesScanned'] += 1
                        entry_path = entry.path
                        if should_skip_path(entry_path):
                            debug_state['skippedEntries'] += 1
                            continue
                        if entry.is_symlink():
                            debug_state['symlinksSeen'] += 1
                            if skip_symlink_entries:
                                debug_state['skippedSymlinks'] += 1
                                continue
                        has_children = True
                        if entry.is_file(follow_symlinks=False):
                            try:
                                entry_stat = entry.stat(follow_symlinks=False)
                                file_size = int(max(0, entry_stat.st_size))
                                total_size += file_size
                                total_files += 1
                                debug_state['filesVisited'] += 1
                            except Exception as exc:
                                debug_state['statErrors'] += 1
                                record_error('stat', entry_path, exc)
            except Exception as exc:
                debug_state['scanErrors'] += 1
                record_error('scandir', path, exc)
        return {
            'totalSize': int(max(0, total_size)),
            'totalFiles': int(max(0, total_files)),
            'hasChildren': bool(has_children),
        }

    def scan_node(path: str) -> Dict[str, Any]:
        debug_state['nodesVisited'] += 1
        node_depth = depth_for(path)

        if path != root_path and should_skip_path(path):
            debug_state['skippedEntries'] += 1
            return make_node(path, True, 0, 0, 0, node_depth, False)

        try:
            node_stat = os.lstat(path)
        except Exception as exc:
            debug_state['statErrors'] += 1
            record_error('stat', path, exc)
            return make_node(path, False, 0, 0, 0, node_depth, False)

        if os.path.islink(path):
            debug_state['symlinksSeen'] += 1
            if skip_symlink_entries and path != root_path:
                debug_state['skippedSymlinks'] += 1
                return make_node(path, False, 0, 0, 0, node_depth, False)

        is_directory = os.path.isdir(path)
        own_size = int(max(0, node_stat.st_size))
        if not is_directory:
            debug_state['filesVisited'] += 1
            if own_size >= 100 * 1024 ** 3:
                record_large_leaf(path, own_size, 'leaf-size')
            return make_node(path, False, own_size, own_size, 1, node_depth, False)

        debug_state['dirsVisited'] += 1

        if node_depth >= max_depth:
            summary = summarize_directory(path, own_size)
            return make_node(
                path,
                True,
                int(summary['totalSize']),
                own_size,
                int(summary['totalFiles']),
                node_depth,
                bool(summary['hasChildren']),
            )

        children: List[Dict[str, Any]] = []
        total_size = own_size
        total_files = 0
        has_hidden_children = False
        try:
            with os.scandir(path) as it:
                for entry in it:
                    debug_state['entriesScanned'] += 1
                    entry_path = entry.path
                    if should_skip_path(entry_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if entry.is_symlink():
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    child = scan_node(entry_path)
                    children.append(child)
                    total_size += int(child.get('size') or 0)
                    total_files += int(child.get('fileCount') or 0)
                    if child.get('hasHiddenChildren'):
                        has_hidden_children = True
        except Exception as exc:
            debug_state['scanErrors'] += 1
            record_error('scandir', path, exc)
            has_hidden_children = True

        children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
        return make_node(
            path,
            True,
            int(max(0, total_size)),
            own_size,
            int(max(0, total_files)),
            node_depth,
            has_hidden_children,
            children,
        )

    root_node = scan_node(target)
    mount_summary: Optional[Dict[str, Any]] = None
    if root_path == '/' and isinstance(root_node, dict):
        if target == root_path:
            mount_summary = _apply_df_overlay_to_root_tree(root_node, debug_state)
        else:
            mount_summary = _summarize_df_mounts()
            _overlay_mount_usage_on_node(root_node, target, depth_for(target), mount_summary, debug_state)
            if mount_summary:
                debug_state['dfRootUsed'] = int(mount_summary.get('rootUsed') or 0)
                debug_state['dfMountedUsed'] = int(mount_summary.get('mountedUsed') or 0)
                debug_state['dfTotalUsed'] = int(mount_summary.get('totalUsed') or 0)

    if isinstance(root_node, dict):
        debug_state['totalSize'] = int(root_node.get('size', 0) or 0)
        debug_state['totalFiles'] = int(root_node.get('fileCount', 0) or 0)
        top_children = (root_node.get('children') or [])[:8]
        debug_state['topChildren'] = [
            {
                'path': str(child.get('path') or ''),
                'size': int(child.get('size') or 0),
                'humanSize': _format_size_human(int(child.get('size') or 0)),
                'fileCount': int(child.get('fileCount') or 0),
            }
            for child in top_children
        ]

        if root_path == '/':
            special_totals: Dict[str, int] = {}
            for child in (root_node.get('children') or []):
                child_path = str(child.get('path') or '')
                child_size = int(child.get('size') or 0)
                for prefix in ('/proc', '/sys', '/dev', '/run'):
                    if child_path == prefix or child_path.startswith(prefix + '/'):
                        special_totals[prefix] = special_totals.get(prefix, 0) + child_size
                        break
            debug_state['specialMountTotals'] = [
                {
                    'path': key,
                    'size': value,
                    'humanSize': _format_size_human(value),
                }
                for key, value in sorted(special_totals.items(), key=lambda item: item[1], reverse=True)
            ]

    _LOGGER.info(
        "[dir-tree] root=%s target=%s total=%s files=%s nodes=%s dirs=%s filesVisited=%s scanned=%s symlinks=%s skippedSymlinks=%s skippedEntries=%s statErrors=%s scanErrors=%s",
        root_path,
        target,
        _format_size_human(int(debug_state.get('totalSize') or 0)),
        int(debug_state.get('totalFiles') or 0),
        int(debug_state.get('nodesVisited') or 0),
        int(debug_state.get('dirsVisited') or 0),
        int(debug_state.get('filesVisited') or 0),
        int(debug_state.get('entriesScanned') or 0),
        int(debug_state.get('symlinksSeen') or 0),
        int(debug_state.get('skippedSymlinks') or 0),
        int(debug_state.get('skippedEntries') or 0),
        int(debug_state.get('statErrors') or 0),
        int(debug_state.get('scanErrors') or 0),
    )
    if int(debug_state.get('dfTotalUsed') or 0) > 0:
        _LOGGER.info(
            "[dir-tree] df-overlay total=%s rootfs=%s mounted=%s included=%s excluded=%s",
            _format_size_human(int(debug_state.get('dfTotalUsed') or 0)),
            _format_size_human(int(debug_state.get('dfRootUsed') or 0)),
            _format_size_human(int(debug_state.get('dfMountedUsed') or 0)),
            len((mount_summary or {}).get('included') or debug_state.get('dfMountsIncluded') or []),
            len((mount_summary or {}).get('excluded') or debug_state.get('dfMountsExcluded') or []),
        )
    if int(debug_state.get('overlayUnknownBytes') or 0) > 0:
        _LOGGER.warning(
            "[dir-tree] unscanned usage overlaid: %s",
            _format_size_human(int(debug_state.get('overlayUnknownBytes') or 0)),
        )
    if debug_state.get('specialMountTotals'):
        _LOGGER.warning("[dir-tree] special mounts included in totals: %s", debug_state.get('specialMountTotals'))
    if debug_state.get('largeLeafs'):
        _LOGGER.warning("[dir-tree] large leaf entries detected: %s", debug_state.get('largeLeafs'))

    if target != root_path:
        return {'node': root_node, 'debug': debug_state}

    return {
        'root': root_node,
        'totalSize': root_node['size'],
        'totalFiles': root_node['fileCount'],
        'rootPath': root_node['path'],
        'debug': debug_state,
    }

