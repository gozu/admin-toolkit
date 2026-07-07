"""DB-domain actuator actions (B-api on the db-health routes).

db-reindex mirrors the legacy db-vacuum/db-analyze pattern: the table name is
validated against pg_stat_user_tables inside the backend route (never
interpolated raw), and the rubric's scale gate applies — the agents may only
PROPOSE runtime-DB maintenance on ~1000+-user instances; below that,
surface-only.
"""

from ..errors import ToolkitError
from . import _base


def _plan_db_reindex(client, host, target, params):
    connection = _base.require_str(target, 'connection', 'db-reindex')
    table = _base.require_str(target, 'table', 'db-reindex')
    data = client.get('/api/tools/db-health/tables', host=host,
                      params={'connection': connection, 'limit': 500}, heavy=True)
    row = next((t for t in data.get('tables') or [] if t.get('name') == table), None)
    if row is None:
        raise ToolkitError('Table %r not found on connection %r.' % (table, connection),
                           remediation="List tables with db_health view='tables'.")
    return {'connection': connection, 'table': table}, {
        'summary': 'REINDEX table %s on connection %s.' % (table, connection),
        'deadTuples': row.get('deadTuples'),
        'rowCount': row.get('rowCount'),
        'totalSize': row.get('totalSize'),
        'note': 'REINDEX takes an exclusive lock on the table for the duration — '
                'plan for a maintenance window on busy tables.',
    }


def _exec_db_reindex(client, host, target):
    result = client.post('/api/tools/db-health/reindex', host=host, red=True,
                         json={'connection': target['connection'],
                               'table': target['table']})
    if isinstance(result, dict) and result.get('error'):
        raise ToolkitError('db-reindex failed: %s' % result['error'])
    return result


SPECS = [
    _base.spec('db-reindex',
               'db-reindex {connection, table}', 'amber',
               _plan_db_reindex, _exec_db_reindex),
]
