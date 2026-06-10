"""Algorithm-review route: ship adk_notebook libs + scan notebooks into the
webapp's own project for human review (uses the code-env-replace catalog/kernel
helpers to resolve the plugin's managed code env)."""
import os
import re
from typing import Any, Dict, List, Tuple

import dataiku
from flask import Blueprint, jsonify, request

from adk_backend.clients import _local_toolkit_client, _local_toolkit_project
from adk_backend.routes.code_env_replace import (
    _cer_env_catalog,
    _cer_fetch_env_detail,
    _cer_kernel_spec_name,
)
from adk_backend.utils import advanced

bp = Blueprint('algorithm_review', __name__)


# ── Algorithm review: ship adk_notebook libs + scan notebooks into ADMINTOOLKIT ──
#
# Materializes a human-reviewable copy of the webapp's Dataiku-API logic inside the
# ADMINTOOLKIT project: writes the importable shared libraries into the project's
# Python library and creates one Jupyter notebook per scan card (verbatim source).
# Pure DSS-API writes → stays on g.client, no macro. API shapes verified live.

def _adk_review_lib_sources() -> Dict[str, str]:
    """{path-under-lib/python: source_text} for the first-party closure the cards
    import: the whole adk_notebook package plus llm_audit (reached via
    data.llm_audit_report → ``import llm_audit``)."""
    import adk_notebook
    import llm_audit
    out: Dict[str, str] = {}
    pkg_dir = os.path.dirname(os.path.abspath(adk_notebook.__file__))
    for fname in sorted(os.listdir(pkg_dir)):
        if fname.endswith('.py'):
            with open(os.path.join(pkg_dir, fname), 'r', encoding='utf-8') as fh:
                out['adk_notebook/' + fname] = fh.read()
    with open(os.path.abspath(llm_audit.__file__), 'r', encoding='utf-8') as fh:
        out['llm_audit.py'] = fh.read()
    return out


def _adk_review_card_sources() -> Dict[str, Tuple[str, str]]:
    """{notebook_name: (card_filename, source_text)} for the bundled scan cards.

    Cards live in ``adk_notebook/cards/`` (inside python-lib) — that tree is the only
    plugin dir copied into the webapp backend's per-run sandbox, so it's reliably
    present at runtime (the plugin root / notebook-cards/ are NOT copied). Notebook
    name = card filename stem (e.g. ai-compute__model-audit__llm-audit-table)."""
    import adk_notebook
    cards_dir = os.path.join(os.path.dirname(os.path.abspath(adk_notebook.__file__)), 'cards')
    out: Dict[str, Tuple[str, str]] = {}
    if not os.path.isdir(cards_dir):
        return out
    for fname in sorted(os.listdir(cards_dir)):
        if fname.endswith('.py') and '__' in fname:
            with open(os.path.join(cards_dir, fname), 'r', encoding='utf-8') as fh:
                out[fname[:-3]] = (fname, fh.read())
    return out


def _adk_review_resolve_kernel(client: Any) -> Tuple[str, bool, List[str]]:
    """Resolve the Jupyter kernel for the review notebooks.

    The notebooks must run on the plugin's OWN managed code env — the env that ships
    rich + python-dateutil + the cloud SDKs the cards need. The plugin's *declared*
    ``codeEnvName`` is NOT reliable on its own: when a plugin env is rebuilt, DSS can
    create a version-suffixed sibling (``…_managed_1`` / ``_2`` / ``_3``) while the
    declared name lags at the stale base. So resolve the whole managed-env *family*
    (the base name plus its ``_N`` siblings) and pick the NEWEST member that has a
    Jupyter kernel — that is the current env (verified live: a plugin's ``codeEnvName``
    normally points at its highest-suffixed env). Fall back to builtin ``python3`` +
    warn only if no family member has a Jupyter kernel yet (the ``installJupyterSupport``
    build hasn't run). A notebook's kernel is independent of its project's default."""
    try:
        plugin_settings = client.get_plugin('admin-toolkit').get_settings().get_raw()
        declared = str((plugin_settings or {}).get('codeEnvName') or '').strip()
        base = re.sub(r'_\d+$', '', declared)  # strip a trailing _N to get the family base
        if base:
            catalog = _cer_env_catalog(client)
            fam_re = re.compile(r'^' + re.escape(base) + r'(_\d+)?$')

            def _suffix(name: str) -> int:
                match = re.search(r'_(\d+)$', name)
                return int(match.group(1)) if match else 0

            family = sorted(
                (
                    name
                    for (lang, name), env in catalog.items()
                    if lang == 'PYTHON'
                    and fam_re.match(name)
                    and env.get('deploymentMode') in (None, 'PLUGIN_MANAGED')
                ),
                key=_suffix,
                reverse=True,  # newest suffix first; the un-suffixed base counts as 0
            )
            for name in family:
                env = catalog.get(('PYTHON', name)) or {}
                kernel = _cer_kernel_spec_name(env, _cer_fetch_env_detail(client, 'PYTHON', name))
                if kernel:
                    if name == declared:
                        return kernel, False, []
                    return kernel, False, [
                        f"Plugin's declared code env '{declared}' is stale; "
                        f"using newer build '{name}'."
                    ]
            newest = family[0] if family else (declared or 'plugin_admin-toolkit_managed')
            return 'python3', True, [
                f"Plugin code env '{newest}' has no Jupyter kernel yet — rebuild it with "
                "Jupyter support (Administration → Plugins → Code env → Rebuild), then "
                "re-run. Notebooks use the builtin 'python3' kernel meanwhile."
            ]
    except Exception:
        pass
    return 'python3', True, [
        "Could not resolve the plugin code env; notebooks use the builtin 'python3' "
        "kernel. Ensure it has 'rich' + 'python-dateutil' so the cards can run."
    ]


def _adk_review_audit_code_env_kernel(client: Any) -> str:
    """Create-or-reuse a managed 'admintoolkitaudit' env (rich + dateutil + Jupyter
    support; dataiku APIs are auto-provided) and trigger its build; return its kernel."""
    name = 'admintoolkitaudit'
    if ('PYTHON', name) not in _cer_env_catalog(client):
        env = client.create_code_env('PYTHON', name, 'DESIGN_MANAGED')
        settings = env.get_settings()
        settings.set_required_packages('rich', 'python-dateutil')
        settings.get_raw().setdefault('desc', {})['installJupyterSupport'] = True
        settings.save()
        env.update_packages(wait=False)  # async build
    return 'py-dku-venv-' + name


def _adk_review_card_title(source_text: str, fallback: str) -> str:
    """First non-empty line of the card's leading docstring (its display title)."""
    match = re.search(r'"""(.*?)"""', source_text, re.S)
    if match:
        for line in match.group(1).strip().splitlines():
            if line.strip():
                return line.strip()
    return fallback


_ADK_REVIEW_PREFLIGHT_CELL = '''\
try:
    import rich
except ImportError:
    print(
        "This notebook needs the 'rich' package, which isn't in the current kernel's code env.\\n"
        "Fix: switch the kernel (Kernel menu -> Change kernel) to a code env that has rich:\\n"
        "  - 'admintoolkitaudit'  (create it via the webapp's 'Create review notebooks' action,\\n"
        "     ticking 'create a dedicated code env'), or\\n"
        "  - the plugin env  'plugin_admin-toolkit_managed'."
    )
    raise SystemExit("rich is not available in this kernel - see the note above.")
'''


def _adk_review_build_nbformat(card_filename: str, source_text: str, kernel_name: str) -> Dict[str, Any]:
    """nbformat-v4 notebook: markdown header + a `rich` preflight cell + the verbatim
    card code cell."""
    title = _adk_review_card_title(source_text, card_filename)
    markdown = [
        "### %s\n" % title,
        "\n",
        "_Verbatim review copy of `notebook-cards/%s`._\n" % card_filename,
        "\n",
        "_Requires a kernel with the `rich` package (e.g. `admintoolkitaudit`, or the plugin env)._\n",
        "\n",
        "Imports the shared logic from the `adk_notebook` project library; "
        "run the cells below to reproduce the matching webapp card.",
    ]
    display = 'Python 3' if kernel_name == 'python3' else kernel_name
    return {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": markdown},
            {"cell_type": "code", "metadata": {"tags": ["preflight"]}, "execution_count": None,
             "outputs": [], "source": _ADK_REVIEW_PREFLIGHT_CELL.splitlines(keepends=True)},
            {"cell_type": "code", "metadata": {}, "execution_count": None,
             "outputs": [], "source": source_text.splitlines(keepends=True)},
        ],
        "metadata": {
            "kernelspec": {"name": kernel_name, "display_name": display, "language": "python"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _adk_review_ensure_folder(parent: Any, name: str) -> Any:
    """get-or-add a child library folder (add_folder raises if it already exists)."""
    try:
        return parent.add_folder(name)
    except Exception:
        return parent.get_folder(name)


def _adk_review_write_library_file(lib: Any, rel_under_python: str, text: str) -> None:
    """Write text to lib/python/<rel_under_python>, creating folders as needed.
    Overwriting a fixed path is idempotent (verified: re-runs update, never duplicate)."""
    segments = rel_under_python.split('/')
    folder = _adk_review_ensure_folder(lib, 'python')
    for seg in segments[:-1]:
        folder = _adk_review_ensure_folder(folder, seg)
    try:
        lib_file = folder.add_file(segments[-1])
    except Exception:
        lib_file = folder.get_file(segments[-1])
    # Encode to UTF-8 bytes: passing str makes the SDK send a Latin-1 body, which
    # blows up on the em-dashes / "›" in the source files (verified live).
    lib_file.write(text.encode('utf-8'))


def _adk_review_upsert_notebook(project: Any, name: str, content: Dict[str, Any],
                                existing_names: set) -> str:
    """Create the notebook, or replace it if it already exists.

    create_jupyter_notebook raises on a duplicate name, and DSSNotebookContent in
    some DSS versions exposes no content-setter (only get_raw/save), so re-create via
    delete+create — verified idempotent (re-runs update content, never duplicate)."""
    if name in existing_names:
        try:
            project.get_jupyter_notebook(name).delete()
        except Exception:
            pass
        project.create_jupyter_notebook(name, content)
        return 'updated'
    project.create_jupyter_notebook(name, content)
    return 'created'


@bp.route('/api/algorithm-review/create', methods=['POST'])
@advanced
def api_algorithm_review_create():
    """Write the adk_notebook shared libraries + one verbatim notebook per scan card
    into the project that hosts this webapp (on the local instance), for human review
    of the Dataiku-API code."""
    client = _local_toolkit_client()            # LOCAL instance (not the remote host-selector)
    project = _local_toolkit_project()           # the project the webapp is added to
    project_key = dataiku.default_project_key()

    body = request.get_json(silent=True) or {}
    if body.get('createCodeEnv'):
        try:
            kernel_name, kernel_fallback = _adk_review_audit_code_env_kernel(client), False
            warnings = ["Code env 'admintoolkitaudit' is building (~a few min) — reopen the notebooks once it's ready."]
        except Exception as exc:
            kernel_name, kernel_fallback, warnings = _adk_review_resolve_kernel(client)
            warnings = ["Couldn't create 'admintoolkitaudit' (%s); used '%s'." % (str(exc)[:120], kernel_name)] + warnings
    else:
        kernel_name, kernel_fallback, warnings = _adk_review_resolve_kernel(client)

    # 1. Shared libraries → project Python library (self-contained import closure).
    lib = project.get_library()
    lib_written: List[str] = []
    lib_errors: List[Dict[str, str]] = []
    for rel_path, text in sorted(_adk_review_lib_sources().items()):
        try:
            _adk_review_write_library_file(lib, rel_path, text)
            lib_written.append('python/' + rel_path)
        except Exception as exc:
            lib_errors.append({'file': rel_path, 'error': str(exc)[:500]})

    # 2. One Jupyter notebook per scan card (idempotent upsert by name).
    try:
        existing = client._perform_json('GET', '/projects/%s/jupyter-notebooks/' % project_key)
        existing_names = {(n.get('name') if isinstance(n, dict) else n) for n in (existing or [])}
    except Exception:
        existing_names = set()

    notebooks: List[Dict[str, Any]] = []
    for nb_name, (card_filename, source_text) in sorted(_adk_review_card_sources().items()):
        entry: Dict[str, Any] = {'file': card_filename, 'notebookName': nb_name}
        try:
            content = _adk_review_build_nbformat(card_filename, source_text, kernel_name)
            entry['status'] = _adk_review_upsert_notebook(project, nb_name, content, existing_names)
        except Exception as exc:
            entry['status'] = 'failed'
            entry['error'] = str(exc)[:500]
        notebooks.append(entry)

    return jsonify({
        'projectKey': project_key,
        'kernelEnv': kernel_name,
        'kernelFallbackUsed': kernel_fallback,
        'warnings': warnings,
        'library': {'written': lib_written, 'errors': lib_errors},
        'notebooks': notebooks,
        'createdCount': sum(1 for n in notebooks if n.get('status') == 'created'),
        'updatedCount': sum(1 for n in notebooks if n.get('status') == 'updated'),
        'failedCount': sum(1 for n in notebooks if n.get('status') == 'failed'),
    })
