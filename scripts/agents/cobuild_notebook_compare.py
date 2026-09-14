"""Start only an owned empty notebook, compare shutdown, preserve its file."""
import secrets
import time
from urllib.parse import urlsplit

import requests


def fixtures(run):
    c, tk = run.dss, run.tk
    key = 'ATKAB' + secrets.token_hex(4).upper()
    project = notebook = None
    session = requests.Session()
    session.headers['X-DKU-APITicket'] = c.get_ticket()
    url = urlsplit(tk.base_url)
    base = url.scheme + '://' + url.netloc
    content = {'nbformat': 4, 'nbformat_minor': 2, 'cells': [], 'metadata': {
        'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}}}

    def wait_for(check):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(1)
        raise RuntimeError('Owned notebook did not reach expected state')

    def stop_owned():
        for item in notebook.get_sessions() or []:
            notebook.unload(session_id=item['sessionId'])
        wait_for(lambda: not notebook.get_sessions())

    try:
        project = c.create_project(key, 'ADTK notebook shutdown comparison', 'admin')
        notebook = project.create_jupyter_notebook('comparison', content)

        def reset():
            stop_owned()
            # Initialize Jupyter's browser CSRF cookie before its POST API.
            session.get(base + '/jupyter/notebooks/' + key + '/comparison.ipynb', timeout=60)
            token = session.cookies.get('_xsrf')
            if not token:
                raise RuntimeError('Jupyter did not issue its CSRF cookie')
            session.headers['X-XSRFToken'] = token
            # DSS's shipped Jupyter frontend uses this session-start contract.
            response = session.post(base + '/jupyter/api/sessions', json={
                'path': key + '/comparison.ipynb', 'type': 'notebook', 'name': '',
                'kernel': {'name': 'python3', 'id': None}}, timeout=60)
            response.raise_for_status()
            wait_for(lambda: len(notebook.get_sessions() or []) == 1)
            tk.post('/api/cache/clear')

        def verify(result):
            wait_for(lambda: not notebook.get_sessions())
            assert result['result']['shutdownCount'] == 1
            assert not result['result'].get('errors')
            assert notebook.get_content().get_raw()['cells'] == []
            return {'owned_kernel_stopped': True, 'notebook_file_preserved': True}

        with run.gates(['notebook-kernels-shutdown']):
            run.action('notebook-kernels-shutdown', {'projectKey': key}, reset, verify,
                       'One active empty notebook in a disposable project; file retained after shutdown')
    finally:
        errors = []
        if notebook:
            try:
                stop_owned()
            except Exception as exc:
                errors.append('kernel: ' + type(exc).__name__)
        if project:
            try:
                project.delete()
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        session.close()
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
