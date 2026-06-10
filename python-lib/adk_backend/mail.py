"""DSS mail-channel helpers — channel listing/resolution + the configured
outreach channel. Shared by the email outreach tools and the feedback route
(and backend.py's /api/mail-channels)."""
import logging
from typing import Any, Dict, List, Optional

from adk_backend.clients import _active_dss_client

_LOGGER = logging.getLogger(__name__)


def _get_configured_mail_channel() -> str:
    """Read the outreach_mail_channel plugin param (empty string if unset)."""
    try:
        raw = _active_dss_client().get_plugin('admin-toolkit').get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        return (config.get('outreach_mail_channel') or '').strip()
    except Exception:
        return ''


def _list_mail_channels(client: Any, diagnostics: Optional[List[str]] = None) -> List[Dict[str, str]]:
    diag = diagnostics if diagnostics is not None else []
    channels: List[Dict[str, str]] = []

    raw_items = client.list_messaging_channels(channel_family='mail')
    diag.append(f"raw_items={len(raw_items) if isinstance(raw_items, list) else '?'}")

    for item in raw_items:
        raw = item.get_raw()
        channel_id = raw.get('id')
        family = str(raw.get('family') or '').lower()
        channel_type = str(raw.get('type') or '').lower()
        label = raw.get('label') or channel_id

        if family and family != 'mail':
            continue
        if not family and channel_type and channel_type not in ('smtp', 'mail'):
            continue

        if not channel_id:
            continue
        channels.append({
            'id': str(channel_id),
            'label': str(label or channel_id),
        })

    unique: Dict[str, Dict[str, str]] = {}
    for channel in channels:
        unique[channel['id']] = channel

    result = list(unique.values())
    diag.append(f"filtered={len(channels)} deduped={len(result)}")
    if not result:
        _LOGGER.warning(
            "[tools] _list_mail_channels: no mail channels found — diag: %s",
            "; ".join(diag),
        )
    return result


def _get_mail_channel(client: Any, requested_id: Optional[str]) -> Any:
    channels = _list_mail_channels(client)
    if not channels:
        return None

    selected = channels[0]
    if requested_id:
        for channel in channels:
            if channel['id'] == requested_id:
                selected = channel
                break

    channel_id = selected['id']
    if not hasattr(client, 'get_messaging_channel'):
        channel = None
    else:
        try:
            channel = client.get_messaging_channel(channel_id)
            if channel is not None:
                return channel
        except Exception:
            channel = None

    if hasattr(client, 'list_messaging_channels'):
        for attempt in (
            lambda: client.list_messaging_channels(as_type='objects', channel_family='mail'),
            lambda: client.list_messaging_channels(as_type='objects'),
        ):
            try:
                items = attempt()
            except Exception:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                item_id = None
                if hasattr(item, 'id'):
                    try:
                        item_id = str(getattr(item, 'id'))
                    except Exception:
                        item_id = None
                if not item_id and hasattr(item, 'get_id'):
                    try:
                        item_id = str(item.get_id())
                    except Exception:
                        item_id = None
                if item_id and item_id == channel_id:
                    return item
    return None
