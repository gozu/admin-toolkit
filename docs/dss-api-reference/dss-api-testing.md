# Testing with the Dataiku DSS Python API

## Setup

```bash
pip3 install --break-system-packages dataiku-api-client
```

The API key and URL are in the project root:
- `.dss-api-key` — API key for auth
- `.dss-url` — DSS instance URL (remote)
- Localhost: `http://localhost:10000`

## Quick Start

```python
import dataikuapi, json

API_KEY = open('.dss-api-key').read().strip()
client = dataikuapi.DSSClient('http://localhost:10000', api_key=API_KEY)
```

## Common Operations

### List projects
```python
projects = client._perform_json('GET', '/projects/')
for p in projects:
    print(p.get('projectKey'), p.get('ownerLogin'))
```

### Get project settings
```python
proj = client.get_project('MY_PROJECT')
settings = proj.get_settings().get_raw()
default_py_env = settings.get('settings', {}).get('codeEnvs', {}).get('python', {}).get('envName')
```

### List & inspect recipes
```python
project_key = 'MY_PROJECT'
recipes = client._perform_json('GET', f'/projects/{project_key}/recipes/')
for rec in recipes:
    name = rec.get('name')
    detail = client._perform_json('GET', f'/projects/{project_key}/recipes/{name}')
    env_sel = detail.get('params', {}).get('envSelection', {})
    print(f'{name}: envMode={env_sel.get("envMode")} envName={env_sel.get("envName")}')
```

### List notebooks
```python
notebooks = client._perform_json('GET', f'/projects/{project_key}/jupyter-notebooks/')
```

### List code envs
```python
envs = client._perform_json('GET', '/admin/code-envs/')
for env in envs:
    print(f'{env.get("envLang")}:{env.get("envName")} owner={env.get("owner")}')
```

### Get code env detail
```python
detail = client._perform_json('GET', '/admin/code-envs/PYTHON/my_env_name')
print(json.dumps(detail, indent=2))
```

## Webapp Backend Endpoints

The webapp backend runs behind DSS's reverse proxy. You **cannot** call webapp
endpoints directly via the `dataikuapi` client (it prepends `/dip/publicapi/`).

Use `requests` directly if you need to hit webapp routes:

```python
import requests
headers = {'Authorization': f'Bearer {API_KEY}'}
# This returns HTML (DSS login page) — webapp auth doesn't work via API key alone
r = requests.get('http://localhost:10000/plugins/admin-toolkit/webapps/admin-toolkit/api/overview', headers=headers)
```

**Note**: Webapp backend endpoints are best tested via the browser or by
replicating the logic in a standalone Python script using `dataikuapi`.

## Useful Patterns

### Replicate the backend's project code env scan
```python
project_key = 'PYTHONAUDIT_TEST'
proj = client.get_project(project_key)
settings = proj.get_settings().get_raw()
default_env = settings.get('settings', {}).get('codeEnvs', {}).get('python', {}).get('envName')

recipes = client._perform_json('GET', f'/projects/{project_key}/recipes/')
for rec in recipes:
    detail = client._perform_json('GET', f'/projects/{project_key}/recipes/{rec["name"]}')
    env = detail.get('params', {}).get('envSelection', {})
    env_name = env.get('envName') or f'(inherits: {default_env})'
    print(f'{rec["name"]}: {env_name}')
```

### Check what data the frontend receives
Add debug logging to the frontend debug panel (`log()` in ToolsView.tsx) and
press 'd' in the browser to view the panel. The debug panel uses:
```typescript
dispatch({ type: 'ADD_DEBUG_LOG', payload: { scope: 'tools-email', level: 'info', message } });
```
