"""
Code env usage -> per-object package import report for Dataiku DSS.

Run this from a Jupyter notebook attached to the TARGET code environment
so importlib.metadata reports the same packages that env has installed.

Flow:
  1. client.list_code_env_usages() -> filter to TARGET_ENV
  2. For each project with usages, collect Python source per-object using
     the verbatim gospel patterns from project_standards_check_spec.py
  3. AST-parse imports, map module names -> installed pip distributions
  4. Print, for each object, the packages it uses.
"""

import ast
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

import dataiku
import dataikuapi

# ──────────────────────────────────────────────────────────────────────
# CONFIG — edit these two lines
# ──────────────────────────────────────────────────────────────────────
TARGET_ENV_NAME = "py_my_env"   # code env to audit
TARGET_ENV_LANG = "PYTHON"      # PYTHON only
PRINT_CODE = False              # set True to dump each object's source before parsing

# ──────────────────────────────────────────────────────────────────────
# Installed-package introspection (runs against THIS interpreter)
# ──────────────────────────────────────────────────────────────────────
try:
    import importlib.metadata as ilmd
except ImportError:
    import importlib_metadata as ilmd  # type: ignore


def build_module_to_package_map() -> Tuple[Dict[str, str], Set[str]]:
    """Return (top_level_module -> distribution_name, all_distribution_names)."""
    mod_to_pkg: Dict[str, str] = {}
    all_pkgs: Set[str] = set()
    for dist in ilmd.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if not name:
            continue
        all_pkgs.add(name)
        top = None
        try:
            top = dist.read_text("top_level.txt")
        except Exception:
            top = None
        if top:
            for line in top.splitlines():
                tl = line.strip()
                if tl:
                    mod_to_pkg.setdefault(tl, name)
        else:
            guess = re.sub(r"[-_.]+", "_", name).lower()
            mod_to_pkg.setdefault(guess, name)
            mod_to_pkg.setdefault(name.replace("-", "_"), name)
    return mod_to_pkg, all_pkgs


MOD_TO_PKG, INSTALLED_PKGS = build_module_to_package_map()

STDLIB = set(getattr(sys, "stdlib_module_names", set())) | {
    "os", "sys", "re", "json", "math", "time", "datetime", "collections",
    "itertools", "functools", "logging", "subprocess", "pathlib", "typing",
    "io", "csv", "random", "hashlib", "base64", "uuid", "shutil", "glob",
    "tempfile", "threading", "multiprocessing", "concurrent", "abc", "enum",
    "argparse", "warnings", "traceback", "inspect", "dataclasses", "ast",
    "copy", "operator", "string", "textwrap", "unicodedata", "decimal",
    "fractions", "statistics", "pickle", "shelve", "sqlite3", "xml", "html",
    "email", "http", "urllib", "ssl", "socket", "asyncio", "queue", "weakref",
    "gc", "platform", "ctypes", "struct", "zlib", "gzip", "bz2", "lzma",
    "tarfile", "zipfile", "configparser", "contextlib", "atexit", "signal",
    "select", "selectors", "errno", "stat", "fnmatch", "linecache",
    "importlib", "pkgutil", "builtins", "__future__",
}


# ──────────────────────────────────────────────────────────────────────
# Source-code fetchers — VERBATIM from project_standards_check_spec.py
# (reshaped to take `project` as a parameter instead of self.project)
# ──────────────────────────────────────────────────────────────────────
def _get_python_recipes(project) -> List[Tuple[str, str, str]]:
    """Get all Python recipe code: [(name, code, type)]"""
    recipes = []
    for recipe in project.list_recipes():
        if recipe["type"] == "python":
            recipe_name = recipe["name"]
            try:
                recipe_obj = project.get_recipe(recipe_name)
                code = recipe_obj.get_settings().get_code()
                if code:
                    recipes.append((f"recipe:{recipe_name}", code, "recipe"))
            except Exception:
                pass
    return recipes


def _get_webapp_code(project) -> List[Tuple[str, str, str]]:
    """Get Python code from webapps: [(name, code, type)]"""
    webapps = []
    python_webapp_types = ['DASH', 'STANDARD', 'BOKEH']

    try:
        for webapp in project.list_webapps():
            if webapp.get("type") in python_webapp_types:
                webapp_id = webapp.get("id")
                try:
                    webapp_obj = project.get_webapp(webapp_id)
                    params = webapp_obj.get_settings().get_raw().get("params", {})
                    code = params.get("python")
                    if code:
                        webapps.append((f"webapp:{webapp_id}", code, "webapp"))
                except Exception:
                    pass
    except Exception:
        pass

    return webapps


def _get_notebook_code(project) -> List[Tuple[str, str, str]]:
    """Get Python code cells from Jupyter notebooks: [(name, code, type)]"""
    notebooks = []

    try:
        for notebook in project.list_jupyter_notebooks():
            try:
                content = notebook.get_content().get_raw()
                cells = content.get('cells', [])

                code_parts = []
                for cell in cells:
                    if cell.get('cell_type') == 'code':
                        source = cell.get('source', [])
                        if isinstance(source, list):
                            code_parts.append(''.join(source))
                        else:
                            code_parts.append(source)

                if code_parts:
                    combined_code = '\n\n'.join(code_parts)
                    notebooks.append((f"notebook:{notebook.notebook_name}", combined_code, "notebook"))
            except Exception:
                pass
    except Exception:
        pass

    return notebooks


def _get_scenario_code(project) -> List[Tuple[str, str, str]]:
    """Get Python code from custom Python scenarios: [(name, code, type)]"""
    scenarios = []

    try:
        for scenario_info in project.list_scenarios():
            if scenario_info.get("type") == "custom_python":
                scenario_id = scenario_info.get("id")
                try:
                    scenario = project.get_scenario(scenario_id)
                    definition = scenario.get_definition()
                    code = definition.get("script")
                    if code:
                        scenarios.append((f"scenario:{scenario_id}", code, "scenario"))
                except Exception:
                    pass
            elif scenario_info.get("type") == "step_based":
                scenario_id = scenario_info.get("id")
                try:
                    scenario = project.get_scenario(scenario_id)
                    steps = scenario.get_settings().raw_steps
                    for i, step in enumerate(steps):
                        if step.get("type") == "custom_python":
                            code = step.get("params", {}).get("script")
                            if code:
                                scenarios.append((f"scenario:{scenario_id}:step_{i}", code, "scenario"))
                except Exception:
                    pass
    except Exception:
        pass

    return scenarios


def collect_project_code(project) -> Dict[str, str]:
    """Build {source_key: code} for one project across all supported object types."""
    out: Dict[str, str] = {}
    for entries in (
        _get_python_recipes(project),
        _get_webapp_code(project),
        _get_notebook_code(project),
        _get_scenario_code(project),
    ):
        for source_key, code, _src_type in entries:
            out[source_key] = code
    return out


# ──────────────────────────────────────────────────────────────────────
# Map a usage row to the source_key produced by the gospel collectors
# ──────────────────────────────────────────────────────────────────────
def _to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("get_raw", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                v = fn()
                if isinstance(v, dict):
                    return v
            except Exception:
                pass
    try:
        return dict(obj)
    except Exception:
        return {}


def usage_names(u: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    """Return (project_key, label, candidate_names) — every plausible identifier
    we can use to match this usage row against a gospel-collected source_key."""
    project_key = (
        u.get("projectKey")
        or (u.get("projectSummary") or {}).get("projectKey")
        or (u.get("project") or {}).get("key")
        or ""
    )
    names: List[str] = []
    for v in (
        u.get("objectId"),
        u.get("objectName"),
        u.get("targetId"),
        u.get("targetName"),
        u.get("displayName"),
        u.get("id"),
        u.get("name"),
    ):
        if isinstance(v, str) and v.strip() and v not in names:
            names.append(v)
    obj_type = (
        u.get("objectType") or u.get("targetType") or u.get("usageType")
        or u.get("envUsage") or u.get("type") or "?"
    )
    label = f"{obj_type}:{names[0] if names else '?'}"
    return project_key or "?", label, names


# ──────────────────────────────────────────────────────────────────────
# Import extraction
# ──────────────────────────────────────────────────────────────────────
def extract_imports(source: str) -> Set[str]:
    if not source:
        return set()
    mods: Set[str] = set()
    parsed = None
    try:
        parsed = ast.parse(source)
    except SyntaxError:
        cleaned = "\n".join(
            "" if line.lstrip().startswith(("%", "!", "?")) else line
            for line in source.splitlines()
        )
        try:
            parsed = ast.parse(cleaned)
        except Exception:
            parsed = None
    if parsed is not None:
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for n in node.names:
                    if n.name:
                        mods.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.level or 0) == 0:
                    mods.add(node.module.split(".")[0])
    for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w\.]*)", source, re.MULTILINE):
        mods.add(m.group(1).split(".")[0])
    return mods


def imports_to_packages(modules: Set[str]) -> Tuple[Set[str], Set[str]]:
    used: Set[str] = set()
    unresolved: Set[str] = set()
    for mod in modules:
        if mod in STDLIB:
            continue
        pkg = MOD_TO_PKG.get(mod) or MOD_TO_PKG.get(mod.lower())
        if pkg is None:
            unresolved.add(mod)
        else:
            used.add(pkg)
    return used, unresolved


# ──────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────
client = dataiku.api_client()

print(f"Python: {sys.executable}")
print(f"Auditing code env: {TARGET_ENV_LANG}:{TARGET_ENV_NAME}")
print(f"Installed distributions visible to this interpreter: {len(INSTALLED_PKGS)}")
print("(If that count looks wrong, attach the notebook to the target env and re-run.)\n")

raw_usages = client.list_code_env_usages() or []
target = []
for u in raw_usages:
    d = _to_dict(u)
    env = d.get("envName") or d.get("codeEnvName") or ""
    lang = (d.get("envLang") or d.get("codeEnvLang") or d.get("language") or "PYTHON").upper()
    if env == TARGET_ENV_NAME and lang == TARGET_ENV_LANG.upper():
        target.append(d)
print(f"Found {len(target)} usage row(s) for this env\n")

# Group usages by project so we list each project's code only once
usages_by_project: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
for u in target:
    pkey = (
        u.get("projectKey")
        or (u.get("projectSummary") or {}).get("projectKey")
        or (u.get("project") or {}).get("key")
        or "?"
    )
    usages_by_project[pkey].append(u)

def fmt(items) -> str:
    if not items:
        return "    (none)"
    return "\n".join(f"    • {p}" for p in sorted(items))


bar = "=" * 78
print(f"\n{bar}\n  PER-OBJECT PACKAGE USAGE — env {TARGET_ENV_LANG}:{TARGET_ENV_NAME}\n{bar}")

def _name_after_colon(source_key: str) -> str:
    return source_key.split(":", 1)[1] if ":" in source_key else source_key


pkg_obj_count: Dict[str, int] = defaultdict(int)
objects_scanned = 0

for pkey, project_usages in sorted(usages_by_project.items()):
    print(f"\n── PROJECT {pkey} ──")

    if pkey == "?" or not pkey:
        for u in project_usages:
            _, label, _ = usage_names(u)
            print(f"\n  [{label}]  (skipped: missing project key)")
        continue

    try:
        project = client.get_project(pkey)
    except Exception as e:
        for u in project_usages:
            _, label, _ = usage_names(u)
            print(f"\n  [{label}]  (skipped: get_project failed: {e})")
        continue

    code_by_key = collect_project_code(project)
    # Lower-cased lookup index: name-after-colon -> [(source_key, code), ...]
    by_name_lc: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for sk, c in code_by_key.items():
        by_name_lc[_name_after_colon(sk).lower()].append((sk, c))

    for u in sorted(project_usages, key=lambda x: (x.get("objectName") or x.get("objectId") or "")):
        _, label, names = usage_names(u)

        # Find matching source_keys by name-suffix (case-insensitive).
        # For step-based scenarios, multiple step entries share a scenario id prefix,
        # so also match source_keys that *begin with* one of the names.
        matched: List[Tuple[str, str]] = []
        seen_keys: Set[str] = set()
        for n in names:
            n_lc = n.lower()
            for sk, c in by_name_lc.get(n_lc, []):
                if sk not in seen_keys:
                    matched.append((sk, c))
                    seen_keys.add(sk)
            for k_lc, pairs in by_name_lc.items():
                if k_lc.startswith(n_lc + ":"):
                    for sk, c in pairs:
                        if sk not in seen_keys:
                            matched.append((sk, c))
                            seen_keys.add(sk)

        if PRINT_CODE:
            print(f"\n  RAW USAGE ROW: {u}")

        if not matched:
            print(f"\n  [{label}]  (no code matched in project; tried names: {names})")
            continue

        for sk, code in matched:
            if PRINT_CODE:
                print(f"\n  [{sk}] — SOURCE ({len(code)} chars):")
                print("    " + "─" * 74)
                for line in code.splitlines():
                    print(f"    │ {line}")
                print("    " + "─" * 74)

            used, _unresolved = imports_to_packages(extract_imports(code))
            objects_scanned += 1
            for pkg in used:
                pkg_obj_count[pkg] += 1
            print(f"\n  [{sk}] — {len(used)} package(s):")
            print(fmt(used))

# ──────────────────────────────────────────────────────────────────────
# Instance-wide rollup (uses only data already collected above)
# ──────────────────────────────────────────────────────────────────────
print(f"\n{bar}\n  INSTANCE-WIDE PACKAGE USAGE — env {TARGET_ENV_LANG}:{TARGET_ENV_NAME}\n{bar}")
print(f"\nObjects scanned: {objects_scanned}")
print(f"Installed packages in env: {len(INSTALLED_PKGS)}")

used_pkgs = {p for p, n in pkg_obj_count.items() if n > 0}
unused_pkgs = INSTALLED_PKGS - used_pkgs

print(f"\nUsed by ≥1 object ({len(used_pkgs)}) — package: count of objects:")
if used_pkgs:
    for pkg, n in sorted(pkg_obj_count.items(), key=lambda kv: (-kv[1], kv[0].lower())):
        if n > 0:
            print(f"    • {pkg}: {n}")
else:
    print("    (none)")

print(f"\nNever used by any scanned object ({len(unused_pkgs)}):")
print(fmt(unused_pkgs))

print("\nDone.")
