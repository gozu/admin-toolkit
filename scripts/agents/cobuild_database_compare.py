"""Database comparison on one owned table; restore DB Health config afterward.

Credentials come from the already configured audit connection and stay in memory.
This fixture requires a quiet backend restart window.
"""
import secrets
import time
from urllib.parse import urlsplit


def fixtures(run):
    from atk_agent_common import audit, config
    from atk_agent_common.client import ToolkitClient
    c = run.dss
    plugin = c.get_plugin('admin-toolkit')
    original = plugin.get_settings().get_raw()['config']
    connection = audit.resolve_connection(config.resolve(original))
    params = c.get_connection(connection).get_info()['params']
    password = params.get('password')
    if not password:
        raise RuntimeError('Configured database credential is unavailable')
    parts = urlsplit(run.tk.base_url).path.strip('/').split('/')
    webapp = c.get_project(parts[parts.index('web-apps-backends') + 1]).get_webapp(parts[-1])
    table = 'atk_ab_' + secrets.token_hex(6)
    db = audit._connect(connection)
    db.autocommit = True
    before_index = None
    changed_config = False

    def query(sql):
        with db.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall() if cur.description else []

    def restart():
        webapp.start_or_restart_backend().wait_for_result()
        # Rebuild the authenticated client after restart; no stale unlock cookie.
        run.tk = ToolkitClient(config.resolve(plugin.get_settings().get_raw()['config']))
        end = time.monotonic() + 90
        while time.monotonic() < end:
            try:
                if run.tk.get('/api/mode').get('mode') == 'live':
                    return
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError('Backend did not become ready after config restart')

    try:
        assert query("SELECT to_regclass('public.%s')" % table)[0][0] is None
        settings = plugin.get_settings()
        settings.get_raw()['config'].update({'dbhealth_connection': connection,
                                             'dbhealth_password': password})
        settings.save()
        changed_config = True
        restart()

        def reset():
            nonlocal before_index
            query('DROP TABLE IF EXISTS public.' + table)
            query('CREATE TABLE public.' + table + ' (id integer PRIMARY KEY, value integer)')
            query('INSERT INTO public.' + table + ' SELECT i, i FROM generate_series(1,10) i')
            query('UPDATE public.' + table + ' SET value=value+1')
            before_index = query("SELECT pg_relation_filenode('public.%s_pkey')" % table)[0][0]

        def verify(kind):
            def check(_):
                assert query('SELECT count(*) FROM public.' + table)[0][0] == 10
                if kind == 'reindex':
                    assert query("SELECT pg_relation_filenode('public.%s_pkey')" % table)[0][0] != before_index
                else:
                    column = 'last_vacuum' if kind == 'vacuum' else 'last_analyze'
                    end = time.monotonic() + 20
                    while time.monotonic() < end:
                        query('SELECT pg_stat_clear_snapshot()')
                        if query("SELECT %s FROM pg_stat_user_tables WHERE relname='%s'" % (column, table))[0][0]:
                            break
                        time.sleep(1)
                    else:
                        raise AssertionError('Maintenance timestamp did not update')
                return {'owned_table_rows': 10, 'maintenance_verified': kind}
            return check

        with run.gates(['db_health', 'db-vacuum', 'db-analyze', 'db-reindex']):
            run.read('db_health', {'connection': connection, 'view': 'tables', 'top_n': 2})
            for kind in ['vacuum', 'analyze', 'reindex']:
                run.action('db-' + kind, {'connection': connection, 'table': table}, reset, verify(kind),
                           'One owned 10-row table; verify maintenance and preserve row count; temporary DB Health credential restored')
    finally:
        errors = []
        try:
            query('DROP TABLE IF EXISTS public.' + table)
        except Exception as exc:
            errors.append('table: ' + type(exc).__name__)
        db.close()
        if changed_config:
            try:
                settings = plugin.get_settings()
                for key in ['dbhealth_connection', 'dbhealth_password']:
                    if key in original:
                        settings.get_raw()['config'][key] = original[key]
                    else:
                        settings.get_raw()['config'].pop(key, None)
                settings.save()
                restart()
            except Exception as exc:
                errors.append('config: ' + type(exc).__name__)
        run.save({'capability': '_database_fixture', 'status': 'cleanup_failed' if errors else 'deleted',
                  'errors': errors})
