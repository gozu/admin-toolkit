"""Overview / host-info routes: raw general settings + project standards,
instance overview, per-process metrics, java memory config, sanity check."""
import logging
import os
import platform
import time

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.clients import _safe_request_host_id
from adk_backend.macros import _host_metrics_macro, _process_metrics_macro
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import (
    _dip_home,
    _find_spark_version,
    _get_cpu_cores,
    _get_os_info,
    _instance_info_from_install_map,
    _parse_filesystem_info,
    _parse_install_ini_map,
    _parse_memory_info,
    _parse_supervisord_restart,
    _parse_system_limits,
    _run_command,
    _safe_read_json,
    _safe_read_text,
)
from adk_backend.utils import _coerce_int

bp = Blueprint('overview', __name__)

_LOGGER = logging.getLogger(__name__)


@bp.route('/api/settings/raw')
def api_settings_raw():
    client = g.client
    settings = client.get_general_settings().get_raw()
    return jsonify(settings)


@bp.route('/api/project-standards/raw')
def api_project_standards_raw():
    client = g.client
    try:
        standards = client.get_project_standards().get_raw()
    except Exception:
        standards = {}
    return jsonify(standards)


def _overview_payload_remote(client):
    m = _host_metrics_macro(client)
    install = m.get('install') or {}
    version = m.get('version') or {}
    cpu = m.get('cpu') or {}
    os_info = m.get('os') or {}
    physical_cores = _coerce_int(cpu.get('physicalCores'), 0)
    logical_cores = _coerce_int(cpu.get('logicalCores'), 0)
    if physical_cores > 0 and logical_cores > physical_cores:
        cpu_label = f"{physical_cores} Cores / {logical_cores} Threads"
    else:
        cpu_label = str(physical_cores or logical_cores or '')
    settings = None
    try:
        settings = client.get_general_settings().get_raw()
    except Exception:
        settings = None
    return {
        'cpuCores': cpu_label,
        'osInfo': os_info.get('PRETTY_NAME') or os_info.get('NAME') or '',
        'memoryInfo': _parse_memory_info(m.get('freeOutput')),
        'systemLimits': _parse_system_limits(m.get('ulimitOutput')),
        'filesystemInfo': _parse_filesystem_info(m.get('dfOutput')),
        'pythonVersion': m.get('pythonVersion') or '',
        'sparkVersion': _find_spark_version(settings) or '',
        'lastRestartTime': _parse_supervisord_restart(m.get('supervisordLog')) or '',
        'dssVersion': version.get('product_version') or version.get('version'),
        'instanceInfo': _instance_info_from_install_map(install),
        'javaMemRaw': m.get('javaMemRaw'),
    }


def _overview_payload(client, dip_home, host_id):
    if host_id != 'local':
        return _overview_payload_remote(client)
    free_output = _run_command(['free', '-m'])
    ulimit_output = _run_command(['bash', '-lc', 'ulimit -a'])
    df_output = _run_command(['df', '-h'])

    version_info = (
        _safe_read_json(os.path.join(dip_home, 'dss-version.json'))
        or _safe_read_json(os.path.join(dip_home, 'config', 'dss-version.json'))
        or {}
    )
    install_ini = _safe_read_text(os.path.join(dip_home, 'install.ini'))
    instance_info = _instance_info_from_install_map(_parse_install_ini_map(install_ini))

    supervisord_log = None
    try:
        supervisord_log = client.get_log('supervisord.log')
    except Exception:
        supervisord_log = _safe_read_text(os.path.join(dip_home, 'run', 'supervisord.log'))

    settings = None
    try:
        settings = client.get_general_settings().get_raw()
    except Exception:
        settings = None

    spark_version = _find_spark_version(settings)
    local_metrics = None
    if not instance_info or not (version_info.get('version') or version_info.get('dssVersion') or version_info.get('product_version')):
        try:
            local_metrics = _host_metrics_macro(client)
        except Exception:
            local_metrics = None
    if isinstance(local_metrics, dict):
        metric_instance_info = _instance_info_from_install_map(local_metrics.get('install') or {})
        for key, value in metric_instance_info.items():
            if value not in (None, '') and not instance_info.get(key):
                instance_info[key] = value
        metric_version = local_metrics.get('version')
        if isinstance(metric_version, dict):
            for key, value in metric_version.items():
                if value not in (None, '') and not version_info.get(key):
                    version_info[key] = value

    return {
        'cpuCores': _get_cpu_cores(),
        'osInfo': _get_os_info(),
        'memoryInfo': _parse_memory_info(free_output),
        'systemLimits': _parse_system_limits(ulimit_output),
        'filesystemInfo': _parse_filesystem_info(df_output),
        'pythonVersion': platform.python_version(),
        'sparkVersion': spark_version,
        'lastRestartTime': _parse_supervisord_restart(supervisord_log),
        'dssVersion': version_info.get('version') or version_info.get('dssVersion') or version_info.get('product_version'),
        'instanceInfo': instance_info,
    }


@bp.route('/api/overview')
def api_overview():
    client = g.client
    dip_home = _dip_home()
    host_id = _safe_request_host_id()
    data = _cache_get(
        'overview',
        _BACKEND_SETTINGS['cache_ttl_overview'],
        lambda: _overview_payload(client, dip_home, host_id),
    )
    return jsonify(data)


@bp.route('/api/host/summary')
def api_host_summary():
    """On-demand, uncached host/memory summary — re-runs the host-metrics
    command set (free -m / df -h / ulimit -a / cpuinfo) and returns the same
    payload as /api/overview. Bypasses the overview cache so the admin can
    refresh just these numbers without busting unrelated cached data, mirroring
    the standalone, uncached /api/host/process-metrics."""
    client = g.client
    return jsonify(_overview_payload(client, _dip_home(), _safe_request_host_id()))


@bp.route('/api/host/process-metrics')
def api_process_metrics():
    """Per-process CPU + memory snapshot from the active host (via macro).

    Host-bound (`ps`/subprocess) so it goes through the process-metrics macro,
    which runs as `dataiku`. Short-cached to keep repeated page loads cheap;
    `?fresh=1` (the table's Refresh button) bypasses the cache so an explicit
    re-run actually re-reads `ps`.
    """
    if request.args.get('fresh'):
        return jsonify(_process_metrics_macro(g.client))
    data = _cache_get(
        'process_metrics',
        _BACKEND_SETTINGS['cache_ttl_overview'],
        lambda: _process_metrics_macro(g.client),
    )
    return jsonify(data)


@bp.route('/api/java-memory')
def api_java_memory():
    dip_home = _dip_home()
    content = _safe_read_text(os.path.join(dip_home, 'bin', 'env-default.sh')) or ''
    return content


@bp.route('/api/sanity-check')
def api_sanity_check():
    t0 = time.time()
    try:
        client = g.client
        if not hasattr(client, 'perform_instance_sanity_check'):
            # Older DSS versions (<14.4) do not expose this API.
            msg = 'perform_instance_sanity_check() not available on this DSS version'
            _LOGGER.warning("[sanity-check] %s", msg)
            return jsonify({'error': msg, 'messages': []}), 501
        result = client.perform_instance_sanity_check(wait=True)
        raw = result._data or {}
        messages = [
            {
                'severity': m.get('severity'),
                'code': m.get('code'),
                'title': m.get('title'),
                'details': m.get('details'),
                'message': m.get('message'),
                'extraInfoSummary': m.get('extraInfoSummary'),
                'extraInfoDetails': m.get('extraInfoDetails'),
            }
            for m in raw.get('messages', [])
        ]
        _LOGGER.info(
            "[sanity-check] ok elapsed=%.0fms messages=%d maxSeverity=%s",
            (time.time() - t0) * 1000.0, len(messages), raw.get('maxSeverity'),
        )
        return jsonify({
            'messages': messages,
            'hasError': raw.get('error', False),
            'hasWarning': raw.get('warning', False),
            'hasSuccess': raw.get('success', False),
            'maxSeverity': raw.get('maxSeverity'),
        })
    except Exception as e:
        _LOGGER.exception(
            "[sanity-check] failed elapsed=%.0fms exc_type=%s",
            (time.time() - t0) * 1000.0, type(e).__name__,
        )
        return jsonify({'error': f"{type(e).__name__}: {e}", 'messages': []}), 500
