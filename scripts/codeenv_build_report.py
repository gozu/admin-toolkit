#!/usr/bin/env python
"""
Code environment build-failure report for Dataiku DSS.

Enumerates every code env on an instance, pulls its build log via the admin
API, isolates the most recent build attempt, classifies any failure, and
(optionally) asks the instance's own LLM Mesh for a remediation recommendation.

Why this exists: DSS stores no per-env "last build succeeded" flag. `info` is
empty and `isUptodate` tracks spec drift, not build success -- so after a
platform upgrade rebuilds N envs, the only record of which ones failed is the
text of the build logs themselves.

Usage
  # credentials from .dss-url / .dss-api-key in cwd, or DSS_URL / DSS_API_KEY
  python scripts/codeenv_build_report.py
  python scripts/codeenv_build_report.py --format markdown --out report.md
  python scripts/codeenv_build_report.py --llm auto          # add LLM advice
  python scripts/codeenv_build_report.py --all               # include healthy envs

Needs an admin API key: the log routes are under /admin/code-envs/.

The parsing core (isolate_last_build / classify / extract_error) is duplicated
in python-lib/adk_backend/code_env_build.py, which backs the webapp's Code Envs
-> Broken page; this file stays standalone so it can be handed to a customer.
Keep the two in sync by hand.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import dataikuapi
except ImportError:  # pragma: no cover
    sys.exit("dataikuapi not installed. pip install dataiku-api-client")


# ──────────────────────────────────────────────────────────────────────
# Log structure
# ──────────────────────────────────────────────────────────────────────

# The log is APPEND-ONLY across rebuilds, so we must isolate the most recent
# attempt or a long-fixed env still reports its historical failure.
#
# DSS writes a banner per operation, carrying a title and a timestamp:
#
#   *********************************************************
#   *********************************************************
#   *
#   * install packages
#   *
#   * <AC:papi:KEY:admin> at 2026/08/04 13:13:26.021
#   *
#   * command : .../bin/python -m pip install -r .../req123.txt
#   *
#   *********************************************************
#
# An attempt begins at an "install packages" banner; a successful one is then
# followed by "list packages" (and any resources-init sections), while a failed
# one aborts right there. So the last install banner to EOF is exactly the last
# attempt and its outcome. Clustering by timestamp gap is NOT a valid substitute
# -- an admin who fixes a spec and retries produces two attempts minutes apart.
_BANNER = re.compile(
    r"\*{20,}[ \t]*\n\*{20,}[ \t]*\n\*[ \t]*\n\*[ \t]+(?P<title>[^\n]*?)[ \t]*\n",
)
_INSTALL_TITLE = re.compile(r"install", re.I)

# Preferred log per language; first match wins, rest are fallbacks.
_LOG_PREFERENCE = (
    "updateEnvAccordingToSpec.log",
    "updatePythonEnv.log",
    "updateREnv.log",
    "createPythonEnv.log",
    "createREnv.log",
    "rebuildImage.log",
)

# Lines that are noise in every log and never indicate a problem.
_NOISE = re.compile(
    r"^\s*(\[notice\]|WARNING: You are using pip|WARNING: Running pip as|"
    r"To update, run:|Requirement already satisfied|Collecting |Downloading |"
    r"Using cached |Installing collected packages|Successfully installed)",
)


def isolate_last_build(text: str) -> str:
    """Return only the most recent build attempt from an append-only log."""
    banners = [(m.start(), m.group("title")) for m in _BANNER.finditer(text)]
    if not banners:
        return text
    for pos, title in reversed(banners):
        if _INSTALL_TITLE.search(title):
            return text[pos:]
    # No install section (e.g. an image-rebuild log): fall back to the last
    # section of any kind rather than scanning the whole history.
    return text[banners[-1][0]:]


# ──────────────────────────────────────────────────────────────────────
# Failure classification
# ──────────────────────────────────────────────────────────────────────

# Ordered most-specific first. Each entry: (class, human label, patterns).
_TAXONOMY: List[Tuple[str, str, List[re.Pattern]]] = [
    (
        "VERSION_CONFLICT",
        "Dependency version conflict",
        [
            re.compile(r"ResolutionImpossible", re.I),
            re.compile(r"conflicting dependencies", re.I),
            re.compile(r"cannot install .* because these package versions", re.I),
        ],
    ),
    (
        "MISSING_PACKAGE",
        "Package or version not found on the index",
        [
            re.compile(r"No matching distribution found for", re.I),
            re.compile(r"Could not find a version that satisfies", re.I),
            re.compile(r"404 Client Error.*for url.*simple", re.I),
        ],
    ),
    (
        "PYTHON_VERSION",
        "Package incompatible with the env's Python version",
        [
            re.compile(r"requires a different Python", re.I),
            re.compile(r"requires Python\s*[><=]", re.I),
            re.compile(r"Ignored the following versions that require a different python", re.I),
        ],
    ),
    (
        "BUILD_FAILURE",
        "Native build / compilation failure",
        [
            re.compile(r"error: subprocess-exited-with-error", re.I),
            re.compile(r"Failed building wheel for", re.I),
            re.compile(r"error: command .* failed with exit", re.I),
            re.compile(r"Microsoft Visual C\+\+ .* is required", re.I),
            re.compile(r"gcc: (error|fatal error)", re.I),
            re.compile(r"fatal error: \S+\.h: No such file", re.I),
            re.compile(r"error: metadata-generation-failed", re.I),
        ],
    ),
    (
        "CONDA_ERROR",
        "Conda resolution or channel failure",
        [
            re.compile(r"PackagesNotFoundError", re.I),
            re.compile(r"UnsatisfiableError", re.I),
            re.compile(r"CondaHTTPError", re.I),
            re.compile(r"CondaError", re.I),
        ],
    ),
    (
        "NETWORK",
        "Network / proxy / TLS failure reaching the package index",
        [
            re.compile(r"Temporary failure in name resolution", re.I),
            re.compile(r"(SSLError|SSLCertVerificationError|CERTIFICATE_VERIFY_FAILED)", re.I),
            re.compile(r"ProxyError|ConnectTimeoutError|ReadTimeoutError", re.I),
            re.compile(r"Failed to establish a new connection", re.I),
            re.compile(r"Could not fetch URL", re.I),
        ],
    ),
    (
        "RESOURCES",
        "Disk, quota, or permission failure on the DSS host",
        [
            re.compile(r"No space left on device", re.I),
            re.compile(r"Disk quota exceeded", re.I),
            re.compile(r"Permission denied", re.I),
            re.compile(r"MemoryError|Killed", re.I),
        ],
    ),
    (
        "IMAGE_BUILD",
        "Container image build or push failure",
        [
            re.compile(r'"success":\s*false', re.I),
            re.compile(r"failed to (push|build|solve)", re.I),
            re.compile(r"denied: requested access to the resource is denied", re.I),
        ],
    ),
]

# Generic last-resort failure markers.
_GENERIC_FAIL = [
    re.compile(r"^ERROR: ", re.M),
    re.compile(r"Failed to install (pip|conda) packages", re.I),
    re.compile(r"ERR_CODEENV_UPDATE_FAILED", re.I),
    re.compile(r"exit code: [1-9]", re.I),
    re.compile(r"Traceback \(most recent call last\)", re.M),
]

# `"error": null` / `"success": true` are success markers in the image-push
# JSON summary and must never be read as failures.
_FALSE_POSITIVE = re.compile(r'"error":\s*null|"success":\s*true', re.I)


def classify(block: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (failure_class, human_label), or (None, None) if it looks clean."""
    scrubbed = _FALSE_POSITIVE.sub("", block)
    for cls, label, patterns in _TAXONOMY:
        for pat in patterns:
            if pat.search(scrubbed):
                return cls, label
    for pat in _GENERIC_FAIL:
        if pat.search(scrubbed):
            return "UNCLASSIFIED_FAILURE", "Build failed (unrecognised error shape)"
    return None, None


def extract_error(block: str, max_lines: int = 14, max_chars: int = 1800) -> str:
    """Pull the salient error lines out of a build log block."""
    lines = block.splitlines()
    hits: List[int] = []
    for i, ln in enumerate(lines):
        if _FALSE_POSITIVE.search(ln) or _NOISE.match(ln):
            continue
        if re.search(
            r"^\s*ERROR|error:|Traceback|ResolutionImpossible|CondaError|"
            r"UnsatisfiableError|PackagesNotFoundError|No matching distribution|"
            r"Could not find a version|conflicting dependencies|"
            r"The conflict is caused by|The user requested|no space left|"
            r"Permission denied|fatal error",
            ln,
            re.I,
        ):
            hits.append(i)

    if not hits:
        tail = [l for l in lines if l.strip() and not _NOISE.match(l)]
        return "\n".join(tail[-max_lines:])[:max_chars]

    keep: List[int] = []
    for i in hits:
        for j in range(i, min(i + 3, len(lines))):
            if j not in keep:
                keep.append(j)
    keep.sort()

    out: List[str] = []
    seen = set()
    for i in keep:
        ln = lines[i].rstrip()
        if not ln.strip() or _NOISE.match(ln):
            continue
        # pip's "(from versions: 0.9, 0.10, ...)" can be thousands of chars.
        ln = re.sub(
            r"\(from versions:.{60,}?\)",
            "(from versions: <long list elided>)",
            ln,
        )
        if ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
        if len(out) >= max_lines:
            break
    return "\n".join(out)[:max_chars]


# ──────────────────────────────────────────────────────────────────────
# Harvest
# ──────────────────────────────────────────────────────────────────────

def connect(url: Optional[str], key: Optional[str], insecure: bool):
    def _read(path: str) -> Optional[str]:
        try:
            with open(path) as fh:
                return fh.read().strip()
        except OSError:
            return None

    url = url or os.environ.get("DSS_URL") or _read(".dss-url")
    key = key or os.environ.get("DSS_API_KEY") or _read(".dss-api-key")
    if not url or not key:
        sys.exit(
            "No DSS credentials. Provide --url/--api-key, set DSS_URL/DSS_API_KEY, "
            "or place .dss-url and .dss-api-key in the working directory."
        )
    client = dataikuapi.DSSClient(url, key)
    if insecure:
        import urllib3

        urllib3.disable_warnings()
        client._session.verify = False
    return client


def inspect_env(client, entry: Dict[str, Any]) -> Dict[str, Any]:
    name, lang = entry["envName"], entry["envLang"]
    rec: Dict[str, Any] = {
        "env": name,
        "lang": lang,
        "deployment_mode": entry.get("deploymentMode"),
        "python": entry.get("pythonInterpreter"),
        "status": "UNKNOWN",
        "failure_class": None,
        "failure_label": None,
        "log": None,
        "last_build": None,
        "error": "",
        "recommendation": None,
    }
    try:
        env = client.get_code_env(lang, name)
        logs = {l["name"]: l for l in env.list_logs()}
    except Exception as exc:
        rec["status"] = "LOG_UNAVAILABLE"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec

    log_name = next((n for n in _LOG_PREFERENCE if n in logs), None)
    if log_name is None:
        rec["status"] = "NO_BUILD_LOG"
        rec["error"] = f"No recognised build log (found: {sorted(logs)})"
        return rec

    try:
        text = env.get_log(log_name)
    except Exception as exc:
        rec["status"] = "LOG_UNAVAILABLE"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec

    block = isolate_last_build(text)
    rec["log"] = log_name
    ts = logs[log_name].get("lastModified")
    if ts:
        rec["last_build"] = _dt.datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")

    cls, label = classify(block)
    if cls:
        rec.update(status="FAILED", failure_class=cls, failure_label=label,
                   error=extract_error(block))
    else:
        rec["status"] = "OK"
    return rec


def harvest(client, workers: int = 8) -> List[Dict[str, Any]]:
    envs = client.list_code_envs()
    out: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(inspect_env, client, e): e for e in envs}
        for fut in concurrent.futures.as_completed(futures):
            out.append(fut.result())
    order = {"FAILED": 0, "LOG_UNAVAILABLE": 1, "NO_BUILD_LOG": 2, "UNKNOWN": 3, "OK": 4}
    out.sort(key=lambda r: (order.get(r["status"], 9), r["env"].lower()))
    return out


# ──────────────────────────────────────────────────────────────────────
# LLM recommendations (via the instance's own LLM Mesh)
# ──────────────────────────────────────────────────────────────────────

_PROMPT = """You are advising a Dataiku DSS administrator whose code environments \
failed to rebuild after a platform upgrade.

Code environment : {env}
Language         : {lang} ({python})
Failure category : {label}

Build log excerpt:
---
{error}
---

Give the administrator a remediation in at most 4 sentences. Name the specific \
package(s) and version(s) involved. State the concrete fix (a version to pin, a \
constraint to loosen, a system library to install, a proxy/index setting to \
correct). If the log is insufficient to be certain, say what to check next. \
Do not restate the log or add pleasantries."""


def pick_llm(client, requested: str) -> Optional[Tuple[str, str]]:
    """Return (project_key, llm_id) for the LLM to use, or None."""
    for pk in client.list_project_keys():
        try:
            llms = client.get_project(pk).list_llms()
        except Exception:
            continue
        usable = []
        for l in llms:
            d = l.to_dict() if hasattr(l, "to_dict") else l
            lid = d.get("id", "")
            # Saved-model agents are not plain completion endpoints.
            if lid.startswith("agent:"):
                continue
            usable.append((lid, d.get("friendlyName") or lid))
        if not usable:
            continue
        if requested not in ("auto", "", None):
            for lid, _ in usable:
                if lid == requested:
                    return pk, lid
            continue
        # Prefer a mid-tier model: strong enough to diagnose, cheap enough for 17 calls.
        for needle in ("sonnet", "opus", "gpt-4", "gemini"):
            for lid, _ in usable:
                if needle in lid.lower():
                    return pk, lid
        return pk, usable[0][0]
    return None


def add_recommendations(client, rows: List[Dict[str, Any]], requested: str,
                        workers: int = 4) -> Optional[str]:
    targets = [r for r in rows if r["status"] == "FAILED"]
    if not targets:
        return None
    picked = pick_llm(client, requested)
    if not picked:
        for r in targets:
            r["recommendation"] = "(no LLM available on this instance)"
        return None
    project_key, llm_id = picked
    project = client.get_project(project_key)

    def _one(rec: Dict[str, Any]) -> None:
        try:
            comp = project.get_llm(llm_id).new_completion()
            comp.with_message(
                _PROMPT.format(
                    env=rec["env"], lang=rec["lang"], python=rec.get("python") or "n/a",
                    label=rec["failure_label"], error=rec["error"],
                ),
                role="user",
            )
            resp = comp.execute()
            rec["recommendation"] = (
                resp.text.strip() if resp.success else f"(LLM call failed: {resp})"
            )
        except Exception as exc:
            rec["recommendation"] = f"(LLM error: {type(exc).__name__}: {exc})"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, targets))
    return llm_id


# ──────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────

def render_table(rows: List[Dict[str, Any]], show_all: bool) -> str:
    shown = rows if show_all else [r for r in rows if r["status"] != "OK"]
    total, failed = len(rows), sum(1 for r in rows if r["status"] == "FAILED")
    lines = [
        f"Code environments: {total} total, {failed} failed, "
        f"{sum(1 for r in rows if r['status'] == 'OK')} ok, "
        f"{sum(1 for r in rows if r['status'] not in ('OK', 'FAILED'))} indeterminate",
        "",
    ]
    if not shown:
        lines.append("No failing code environments found.")
        return "\n".join(lines)

    w = max(len(r["env"]) for r in shown)
    lines.append(f"{'ENV'.ljust(w)}  {'STATUS':<16} {'CATEGORY':<22} LAST BUILD")
    lines.append("-" * (w + 60))
    for r in shown:
        lines.append(
            f"{r['env'].ljust(w)}  {r['status']:<16} "
            f"{(r['failure_class'] or '-'):<22} {r['last_build'] or '-'}"
        )
    lines.append("")
    for r in shown:
        if r["status"] == "OK":
            continue
        lines += ["=" * 72, f"{r['env']}  [{r['failure_label'] or r['status']}]",
                  f"  log: {r['log'] or '-'}   last build: {r['last_build'] or '-'}", ""]
        lines += ["  " + l for l in (r["error"] or "(no detail)").splitlines()]
        if r.get("recommendation"):
            lines += ["", "  RECOMMENDATION:"]
            lines += ["    " + l for l in r["recommendation"].splitlines()]
        lines.append("")
    return "\n".join(lines)


def render_markdown(rows: List[Dict[str, Any]], show_all: bool, llm_id: Optional[str]) -> str:
    shown = rows if show_all else [r for r in rows if r["status"] != "OK"]
    failed = sum(1 for r in rows if r["status"] == "FAILED")
    out = [
        "# Code environment build report",
        "",
        f"- Environments scanned: **{len(rows)}**",
        f"- Failed: **{failed}**",
        f"- Indeterminate: **{sum(1 for r in rows if r['status'] not in ('OK','FAILED'))}**",
    ]
    if llm_id:
        out.append(f"- Recommendations generated by: `{llm_id}`")
    out += ["", "## Summary", "",
            "| Code env | Status | Category | Last build |",
            "| --- | --- | --- | --- |"]
    for r in shown:
        out.append(
            f"| `{r['env']}` | {r['status']} | {r['failure_label'] or '-'} | "
            f"{r['last_build'] or '-'} |"
        )
    out += ["", "## Detail", ""]
    for r in shown:
        if r["status"] == "OK":
            continue
        out += [f"### `{r['env']}`", "",
                f"**{r['failure_label'] or r['status']}** — log `{r['log'] or '-'}`, "
                f"last build {r['last_build'] or '-'}", "",
                "```", (r["error"] or "(no detail)").strip(), "```", ""]
        if r.get("recommendation"):
            out += ["**Recommendation**", "", r["recommendation"].strip(), ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--api-key")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (self-signed DSS certs)")
    ap.add_argument("--llm", default="none",
                    help="'auto', an LLM Mesh id, or 'none' (default) to skip advice")
    ap.add_argument("--format", choices=("table", "json", "markdown"), default="table")
    ap.add_argument("--all", action="store_true", help="include healthy envs")
    ap.add_argument("--out", help="write to a file instead of stdout")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    client = connect(args.url, args.api_key, args.insecure)
    rows = harvest(client, workers=args.workers)

    llm_id = None
    if args.llm and args.llm.lower() != "none":
        llm_id = add_recommendations(client, rows, args.llm)

    if args.format == "json":
        payload = {
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "llm": llm_id,
            "counts": {
                "total": len(rows),
                "failed": sum(1 for r in rows if r["status"] == "FAILED"),
                "ok": sum(1 for r in rows if r["status"] == "OK"),
            },
            "environments": rows if args.all else [r for r in rows if r["status"] != "OK"],
        }
        text = json.dumps(payload, indent=2)
    elif args.format == "markdown":
        text = render_markdown(rows, args.all, llm_id)
    else:
        text = render_table(rows, args.all)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
