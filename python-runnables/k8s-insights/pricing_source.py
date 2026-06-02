"""Live EC2 on-demand pricing via the vantage.sh `instances.json` mirror.

vantage.sh maintains the `ec2instances.info` data feed — an MIT-licensed
mirror of AWS's published on-demand pricing across every commercial region.
One ~197 MB JSON document covers every instance type × every region, so a
single fetch per day eliminates per-lookup round-trips.

Two-tier cache:

  1. On-disk distilled JSON at `<DIP_HOME>/caches/k8s-insights/vantage_pricing.json`
     (~300 KB after distillation). 24-hour TTL — on-demand prices change at
     most monthly so staleness is fine.
  2. In-process dict, loaded from the distilled file once per Python process.

On any failure `PricingSourceError` is raised — the audit envelope's
`pricingStatus` surfaces the condition to the UI and cost rules are
suppressed via the `_pricing` virtual probe.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

SOURCE_NAME = 'vantage-ec2-instances'
SOURCE_URL = 'https://instances.vantage.sh/instances.json'
CACHE_REL_PATH = ('caches', 'k8s-insights', 'vantage_pricing.json')
CACHE_TTL_SEC = 24 * 3600
FETCH_TIMEOUT_SEC = 60
USER_AGENT = 'admin-toolkit-k8s-insights/0.4 (+vantage-mirror)'

_LOG = logging.getLogger(__name__)

# In-process cache: {region: {instance_type: usd_per_hour}}.
_IN_MEM: Optional[Dict[str, Dict[str, float]]] = None
_IN_MEM_FETCHED_AT: float = 0.0
_LOCK = threading.Lock()


class PricingSourceError(Exception):
    """Raised when the pricing source is unusable. Carries a structured
    reason so the audit envelope can surface a meaningful error."""

    def __init__(self, source: str, reason: str):
        super().__init__(f'{source}: {reason}')
        self.source = source
        self.reason = reason


def _cache_path(dip_home: Optional[str]) -> str:
    root = dip_home or tempfile.gettempdir()
    return os.path.join(root, *CACHE_REL_PATH)


def _read_on_disk_cache(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get('priceByType'), dict):
        return None
    return doc


def _fetch_and_distill() -> Dict[str, Dict[str, float]]:
    """Pull instances.json, distill to {region: {instance_type: usd_per_hour}}.

    Raises PricingSourceError on network / parse / schema failures.
    """
    req = urllib.request.Request(SOURCE_URL, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise PricingSourceError(SOURCE_NAME, f'HTTP {exc.code} from vantage.sh')
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise PricingSourceError(SOURCE_NAME, f'network error: {type(exc).__name__}: {str(exc)[:200]}')

    try:
        instances = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PricingSourceError(SOURCE_NAME, f'parse error: {exc}')

    if not isinstance(instances, list) or not instances:
        raise PricingSourceError(SOURCE_NAME, f'schema: expected non-empty list, got {type(instances).__name__}')

    distilled: Dict[str, Dict[str, float]] = {}
    for entry in instances:
        if not isinstance(entry, dict):
            continue
        itype = entry.get('instance_type')
        pricing = entry.get('pricing')
        if not itype or not isinstance(pricing, dict):
            continue
        for region, by_os in pricing.items():
            if not isinstance(by_os, dict):
                continue
            linux = by_os.get('linux')
            if not isinstance(linux, dict):
                continue
            ondemand = linux.get('ondemand')
            if ondemand in (None, '', 'N/A'):
                continue
            try:
                price = float(ondemand)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            distilled.setdefault(region, {})[itype] = price

    if not distilled:
        raise PricingSourceError(SOURCE_NAME, 'schema: no usable linux ondemand prices in vantage payload')
    return distilled


def _write_on_disk_cache(path: str, distilled: Dict[str, Dict[str, float]]) -> None:
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        _LOG.warning('pricing_source: cannot create cache dir %s: %s', parent, exc)
        return
    doc = {
        'fetchedAt': int(time.time() * 1000),
        'source': SOURCE_NAME,
        'sourceUrl': SOURCE_URL,
        'priceByType': distilled,
    }
    fd, tmp_path = tempfile.mkstemp(prefix='vantage_pricing.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, separators=(',', ':'))
        os.replace(tmp_path, path)
    except OSError as exc:
        _LOG.warning('pricing_source: cannot write cache %s: %s', path, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _load_or_refresh(dip_home: Optional[str]) -> Dict[str, Dict[str, float]]:
    """Return the in-memory distilled map, fetching from upstream / disk as needed."""
    global _IN_MEM, _IN_MEM_FETCHED_AT
    now = time.time()
    with _LOCK:
        if _IN_MEM is not None and (now - _IN_MEM_FETCHED_AT) < CACHE_TTL_SEC:
            return _IN_MEM

        path = _cache_path(dip_home)
        on_disk = _read_on_disk_cache(path)
        if on_disk is not None:
            age_sec = max(0.0, now - (on_disk.get('fetchedAt', 0) / 1000.0))
            if age_sec < CACHE_TTL_SEC:
                _IN_MEM = on_disk['priceByType']
                _IN_MEM_FETCHED_AT = now - age_sec
                return _IN_MEM

        # Cold or stale — fetch fresh.
        try:
            distilled = _fetch_and_distill()
        except PricingSourceError:
            # Fall back to stale on-disk data if we have any — beats no pricing.
            if on_disk is not None:
                _LOG.warning('pricing_source: upstream fetch failed, using stale on-disk cache')
                _IN_MEM = on_disk['priceByType']
                _IN_MEM_FETCHED_AT = on_disk.get('fetchedAt', 0) / 1000.0
                return _IN_MEM
            raise
        _write_on_disk_cache(path, distilled)
        _IN_MEM = distilled
        _IN_MEM_FETCHED_AT = now
        return _IN_MEM


def get_on_demand_usd_per_hour(
    instance_type: str,
    region: str = 'us-west-2',
    dip_home: Optional[str] = None,
) -> float:
    """Return the hourly USD on-demand (Linux) price for `instance_type` in `region`.

    Raises PricingSourceError when the source is unusable or the requested
    `(instance_type, region)` pair isn't covered.
    """
    if not instance_type:
        raise PricingSourceError(SOURCE_NAME, 'instance_type is empty')
    if not region:
        raise PricingSourceError(SOURCE_NAME, 'region is empty')

    price_map = _load_or_refresh(dip_home)
    region_map = price_map.get(region)
    if not region_map:
        raise PricingSourceError(SOURCE_NAME, f'coverage: region {region!r} not in dataset')
    price = region_map.get(instance_type)
    if price is None:
        raise PricingSourceError(SOURCE_NAME, f'coverage: {instance_type}@{region} not in dataset')
    return float(price)
