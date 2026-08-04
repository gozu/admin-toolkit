"""Code-env build-log parsing: last-attempt isolation, failure taxonomy, error
excerpting, log-derived dates.

Duplicated on purpose from `scripts/codeenv_build_report.py`: that script is a
standalone artifact we hand to customers (one file, `dataikuapi` only), and the
plugin ZIP does not ship `scripts/`, so the backend physically cannot import
it. The parsing core below is a verbatim copy — same names, same regexes — so
the two diff cleanly. Keep them in sync by hand.

Flask-free: the route module owns `g.client` and the SSE plumbing.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

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

# Logs DSS writes when the env is first built; their mtime is our only
# "created on" signal (the code-env API exposes no creation date).
_CREATE_LOGS = ("createPythonEnv.log", "createREnv.log")

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
# Log-listing metadata
# ──────────────────────────────────────────────────────────────────────

def derive_dates(log_entries: Any) -> Tuple[Optional[int], Optional[int]]:
    """(created_ms, last_build_ms) from log listing metadata; either may be None.

    DSS exposes no creation/update date on a code env, so both dates come from
    `list_logs()` mtimes: created = the create*Env log, last build = the newest
    log of any kind.
    """
    created: Optional[int] = None
    last_build: Optional[int] = None
    by_name: Dict[str, Any] = {}
    for entry in log_entries or []:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("lastModified")
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        by_name[str(entry.get("name") or "")] = ts
        if last_build is None or ts > last_build:
            last_build = ts
    for name in _CREATE_LOGS:
        if name in by_name:
            created = by_name[name]
            break
    return created, last_build
