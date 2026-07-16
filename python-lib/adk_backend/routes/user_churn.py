"""User churn / seat-reassignment facts — per-account lifecycle timestamps.

DSS keeps no "disabled date": the public API exposes `creationDate`, the
`enabled` flag (list_users) and the login/session snapshot
(list_users_activity: lastSuccessfulLogin / lastFailedLogin /
lastSessionActivity) — shapes verified live on akaos (DSS 14). For a disabled
account the best available end-of-life proxy is its last recorded activity;
accounts deleted outright vanish from the snapshot entirely (survivorship
caveat surfaced in the UI). This endpoint returns per-account *facts* only —
the yearly churn roll-up, the seat-reassignment pool model and the dormancy
classification are pure client-side derivations (utils/userChurn.ts), so UI
thresholds can move without a refetch.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, g, jsonify

from adk_backend.caching import _cache_get
from adk_backend.clients import _sdk_fetch
from adk_backend.routes.adoption import _activity_dict, _licensing_summary
from adk_backend.settings import _BACKEND_SETTINGS

bp = Blueprint('user_churn', __name__)
_LOGGER = logging.getLogger(__name__)

_USER_CHURN_CACHE_KEY = 'user_churn'


def _ms(value: Any) -> Any:
    """Epoch-ms passthrough: keep positive ints, drop 0/None/garbage (DSS uses
    0 for "never")."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _user_churn_data(client: Any) -> Dict[str, Any]:
    users = _sdk_fetch(
        'list_users',
        _BACKEND_SETTINGS['cache_ttl_users'],
        lambda: client.list_users(),
    ) or []
    activities = _sdk_fetch(
        'list_users_activity',
        _BACKEND_SETTINGS['cache_ttl_users'],
        lambda: client.list_users_activity(),
    ) or []

    activity_by_login: Dict[str, Dict[str, Any]] = {}
    for a in activities:
        act = _activity_dict(a)
        login = act.get('login') or ''
        if login:
            activity_by_login[login] = act

    accounts = []
    for u in users:
        login = u.get('login') or ''
        if not login:
            continue
        act = activity_by_login.get(login, {})
        created_ms = _ms(u.get('creationDate'))
        last_login_ms = _ms(act.get('lastSuccessfulLogin'))
        last_activity_ms = _ms(act.get('lastSessionActivity'))
        enabled = u.get('enabled') is True

        # End-of-life proxy for disabled accounts, best evidence first:
        # session activity ≥ login ≥ creation (an account disabled before its
        # first login churns at its creation date).
        effective_end_ms = None
        end_source = None
        if not enabled:
            if last_activity_ms is not None or last_login_ms is not None:
                effective_end_ms = max(last_activity_ms or 0, last_login_ms or 0)
                end_source = (
                    'activity'
                    if effective_end_ms == (last_activity_ms or 0)
                    else 'login'
                )
            elif created_ms is not None:
                effective_end_ms = created_ms
                end_source = 'created'

        accounts.append({
            'login': login,
            'displayName': u.get('displayName') or login,
            'email': u.get('email') or None,
            'userProfile': u.get('userProfile') or None,
            'groups': [name for name in (u.get('groups') or []) if name],
            'enabled': enabled,
            'sourceType': u.get('sourceType') or None,
            'creationDateMs': created_ms,
            'lastSuccessfulLoginMs': last_login_ms,
            'lastFailedLoginMs': _ms(act.get('lastFailedLogin')),
            'lastSessionActivityMs': last_activity_ms,
            'effectiveEndMs': effective_end_ms,
            'endSource': end_source,
        })

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        'ok': True,
        'generatedAtMs': now_ms,
        'accounts': accounts,
        'licensing': _licensing_summary(client),
    }


@bp.route('/api/users/churn')
def api_users_churn():
    """Per-account lifecycle snapshot for the Users → Churn page.

    Host-scoped cache (same routing as /api/adoption): _cache_get prefixes the
    active X-DSS-Host-Id, so a remote host's users are never served for the
    local one.
    """
    client = g.client

    def loader():
        return _user_churn_data(client)

    data = _cache_get(
        _USER_CHURN_CACHE_KEY, _BACKEND_SETTINGS['cache_ttl_users'], loader,
    )
    return jsonify(data)
