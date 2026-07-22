"""Probes: independent raw-data collectors for the K8S Insights rule engine.

Each probe wraps exactly one kubectl call, file read, or DSS config parse. All
probes return:

    {
        'ok': bool,
        'data': Any | None,
        'error': Optional[str],
        'durationMs': int,
    }

so the rule engine can degrade gracefully when one fails (e.g., `kubectl top`
requires metrics-server). The rule layer is pure and never touches I/O.

Probes never raise — they always return a dict. This keeps the macro's top
level error-recovery extremely simple.
"""
import concurrent.futures as cf
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# A `KubectlFn` is a callable that runs a kubectl command. It accepts the args
# string (everything after the word `kubectl`) and returns (returncode, stdout,
# stderr). The macro builds one of these from `dataiku.api_client().get_cluster
# (cluster_id).run_kubectl(...)` — which is the only reliable way to talk to a
# DSS-attached EKS cluster (those clusters don't write a kubeconfig file).
KubectlFn = Callable[[str], Tuple[int, str, str]]


# ---------- error classifier ---------- #


_DNS_RE = re.compile(r'no such host|nxdomain|nodename nor servname provided|name or service not known', re.IGNORECASE)
_NET_RE = re.compile(r'i/o timeout|connection refused|ehostunreach|network is unreachable|connect: connection timed out|dial tcp', re.IGNORECASE)
_AUTH_RE = re.compile(r'\b(unauthorized|401|403|expiredtoken|accessdenied|rbac|forbidden|invalid bearer token|signed in)\b', re.IGNORECASE)
_TLS_RE = re.compile(r'x509|certificate signed by unknown|tls handshake|certificate has expired|bad certificate', re.IGNORECASE)


def classify_kubectl_error(stderr: str) -> str:
    """Bucket a kubectl stderr blob into one of: dns | network | auth | tls | unknown.

    Order matters: TLS messages often mention `x509` plus a hostname, so test
    TLS before DNS. Auth strings sometimes look like network errors when
    proxies inject a 403 page; test auth last among the specifics.
    """
    s = stderr or ''
    if _TLS_RE.search(s):
        return 'tls'
    if _DNS_RE.search(s):
        return 'dns'
    if _NET_RE.search(s):
        return 'network'
    if _AUTH_RE.search(s):
        return 'auth'
    return 'unknown'


# ---------- shared helpers ---------- #


def _now_ms() -> int:
    return int(time.time() * 1000)


# DSS managed/attached EKS clusters store the kubeconfig under one of these
# filenames inside `$DIP_HOME/clusters/<id>/`. Newer DSS uses `kubeconfig`;
# the eks-clusters plugin and some older versions used `kube.config.yaml`.
_KUBECONFIG_FILENAMES = ('kubeconfig', 'kube.config.yaml', 'kube.config', 'kubeconfig.yaml')


def _find_kubeconfig(dip_home: str, cluster_id: str) -> Optional[str]:
    base = os.path.join(dip_home, 'clusters', cluster_id)
    for name in _KUBECONFIG_FILENAMES:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return path
    return None


def _run_kubectl(kubectl: KubectlFn, args: str) -> Dict[str, Any]:
    """Invoke a kubectl runner and uniformize the result envelope."""
    started = _now_ms()
    try:
        rc, out, err = kubectl(args)
    except Exception as exc:
        return {
            'ok': False, 'text': '',
            'error': f'{type(exc).__name__}: {str(exc)[:200]}',
            'rc': None, 'stdoutHead': '', 'stderrFull': '',
            'durationMs': _now_ms() - started,
        }
    elapsed = _now_ms() - started
    out = out or ''
    err = err or ''
    ok = rc == 0
    return {
        'ok': ok,
        'text': out,
        'error': None if ok else (err.strip()[:4000] or f'kubectl exit {rc}'),
        'rc': rc,
        'stdoutHead': out[:2000],
        'stderrFull': err[:4000],
        'durationMs': elapsed,
    }


def _diag(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Pluck diag fields off a _run_kubectl result for spread into probe envelopes."""
    return {
        'rc': raw.get('rc'),
        'stdoutHead': raw.get('stdoutHead', ''),
        'stderrFull': raw.get('stderrFull', ''),
    }


def _kubectl_json(kubectl: KubectlFn, args: str) -> Dict[str, Any]:
    """Run kubectl with -o json appended; parse the result."""
    raw = _run_kubectl(kubectl, args + ' -o json')
    if not raw['ok']:
        return {
            'ok': False, 'data': None, 'error': raw['error'],
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    try:
        data = json.loads(raw['text']) if raw['text'].strip() else {}
        return {
            'ok': True, 'data': data, 'error': None,
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    except json.JSONDecodeError as exc:
        return {
            'ok': False,
            'data': None,
            'error': f'JSON parse failed: {str(exc)[:200]}; head={raw["text"][:200]!r}',
            'durationMs': raw['durationMs'],
            **_diag(raw),
        }


def _read_file(path: str, max_bytes: int = 256 * 1024) -> Optional[str]:
    try:
        with open(path, 'rb') as fh:
            return fh.read(max_bytes).decode('utf-8', errors='replace')
    except OSError:
        return None


# ---------- kubectl probes ---------- #


def probe_pods(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get pods -A')


def probe_nodes(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get nodes')


def probe_daemonsets(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get ds -A')


def probe_replicasets(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get rs -A')


def probe_deployments_all(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get deploy -A')


def probe_deployments_kubesystem(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get deploy -n kube-system')


def probe_pdbs(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get pdb -A')


def probe_events(kubectl: KubectlFn) -> Dict[str, Any]:
    return _kubectl_json(kubectl, 'get events -A --sort-by=.lastTimestamp')


def probe_top_pods(kubectl: KubectlFn) -> Dict[str, Any]:
    """Parse `kubectl top pods --containers --no-headers` output.

    Returns a list of {namespace, pod, container, cpuMilli, memMib} dicts.
    Fails gracefully if metrics-server isn't installed.
    """
    raw = _run_kubectl(kubectl, 'top pods -A --no-headers --containers')
    if not raw['ok']:
        return {
            'ok': False, 'data': None, 'error': raw['error'],
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    rows: List[Dict[str, Any]] = []
    for line in raw['text'].splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        ns, pod, container, cpu, mem = parts[0], parts[1], parts[2], parts[3], parts[4]
        rows.append({
            'namespace': ns,
            'pod': pod,
            'container': container,
            'cpuMilli': _parse_topk_cpu(cpu),
            'memMib': _parse_topk_mem(mem),
        })
    return {
        'ok': True, 'data': rows, 'error': None,
        'durationMs': raw['durationMs'], **_diag(raw),
    }


def probe_top_nodes(kubectl: KubectlFn) -> Dict[str, Any]:
    raw = _run_kubectl(kubectl, 'top nodes --no-headers')
    if not raw['ok']:
        return {
            'ok': False, 'data': None, 'error': raw['error'],
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    rows: List[Dict[str, Any]] = []
    for line in raw['text'].splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        rows.append({
            'node': parts[0],
            'cpuMilli': _parse_topk_cpu(parts[1]),
            'cpuPct': _parse_pct(parts[2]),
            'memMib': _parse_topk_mem(parts[3]),
            'memPct': _parse_pct(parts[4]),
        })
    return {
        'ok': True, 'data': rows, 'error': None,
        'durationMs': raw['durationMs'], **_diag(raw),
    }


def probe_kubectl_version(kubectl: KubectlFn) -> Dict[str, Any]:
    raw = _run_kubectl(kubectl, 'version --client -o json')
    if not raw['ok']:
        return {
            'ok': False, 'data': None, 'error': raw['error'],
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    try:
        return {
            'ok': True, 'data': json.loads(raw['text']), 'error': None,
            'durationMs': raw['durationMs'], **_diag(raw),
        }
    except json.JSONDecodeError:
        return {
            'ok': True, 'data': {'raw': raw['text'][:512]}, 'error': None,
            'durationMs': raw['durationMs'], **_diag(raw),
        }


# ---------- filesystem probes ---------- #


def probe_dss_general_settings(dip_home: str) -> Dict[str, Any]:
    started = _now_ms()
    path = os.path.join(dip_home, 'config', 'general-settings.json')
    text = _read_file(path)
    if text is None:
        return {'ok': False, 'data': None, 'error': f'cannot read {path}', 'durationMs': _now_ms() - started}
    try:
        full = json.loads(text)
    except json.JSONDecodeError as exc:
        return {'ok': False, 'data': None, 'error': f'JSON parse: {exc}', 'durationMs': _now_ms() - started}
    container = (full.get('containerSettings') or {})
    execs = container.get('executionConfigs') or []
    # Strip secrets — keep only the bin-pack-relevant fields.
    cleaned = []
    for cfg in execs:
        if not isinstance(cfg, dict):
            continue
        cleaned.append({
            'name': cfg.get('name'),
            'type': cfg.get('type'),
            'usableBy': cfg.get('usableBy'),
            'kubernetesNamespace': cfg.get('kubernetesNamespace'),
            'memRequestMB': cfg.get('memRequestMB'),
            'memLimitMB': cfg.get('memLimitMB'),
            'cpuRequest': cfg.get('cpuRequest'),
            'cpuLimit': cfg.get('cpuLimit'),
            'gpuRequest': cfg.get('gpuRequest'),
            'nodeSelector': cfg.get('nodeSelector'),
            'tolerations': cfg.get('tolerations'),
            'envBindings': cfg.get('envBindings'),
        })
    return {
        'ok': True,
        'data': {'executionConfigs': cleaned, 'count': len(cleaned)},
        'error': None,
        'durationMs': _now_ms() - started,
    }


def probe_managed_cluster_dir(dip_home: str, cluster_id: str) -> Dict[str, Any]:
    started = _now_ms()
    base = os.path.join(dip_home, 'clusters', cluster_id)
    if not os.path.isdir(base):
        return {'ok': False, 'data': None, 'error': f'no cluster dir: {base}', 'durationMs': _now_ms() - started}
    files = {}
    for name in ('nvidia-device-plugin.yml', 'nvidia-device-plugin.yaml', 'eksctl-config.yaml', 'kubeconfig'):
        path = os.path.join(base, name)
        if os.path.exists(path):
            files[name] = {
                'path': path,
                'size': os.path.getsize(path),
                'mtime': int(os.path.getmtime(path)),
            }
    nvidia_yaml = None
    for cand in ('nvidia-device-plugin.yml', 'nvidia-device-plugin.yaml'):
        path = os.path.join(base, cand)
        text = _read_file(path)
        if text is not None:
            nvidia_yaml = {'path': path, 'text': text}
            break
    return {
        'ok': True,
        'data': {'baseDir': base, 'files': files, 'nvidiaYaml': nvidia_yaml},
        'error': None,
        'durationMs': _now_ms() - started,
    }


def probe_eks_plugin_gpu_driver(dip_home: str) -> Dict[str, Any]:
    started = _now_ms()
    path = os.path.join(dip_home, 'plugins', 'installed', 'eks-clusters', 'python-lib', 'dku_kube', 'gpu_driver.py')
    text = _read_file(path)
    if text is None:
        return {'ok': True, 'data': {'present': False, 'path': path}, 'error': None, 'durationMs': _now_ms() - started}
    refs_main = bool(re.search(r"github\.com/NVIDIA/.*?[/']main[/']", text)) or 'NVIDIA/k8s-device-plugin/main' in text
    return {
        'ok': True,
        'data': {
            'present': True,
            'path': path,
            'size': len(text),
            'fetchesMainBranch': refs_main,
            'snippet': _extract_requests_snippet(text),
        },
        'error': None,
        'durationMs': _now_ms() - started,
    }


def _parse_kubeconfig_meta(text: str) -> Dict[str, Any]:
    """Best-effort context/server extraction from a kubeconfig. PyYAML when the
    kernel env has it; otherwise a line-regex fallback that only trusts the
    unambiguous top-level keys."""
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text) or {}
        if isinstance(doc, dict):
            contexts = [
                c.get('name') for c in (doc.get('contexts') or [])
                if isinstance(c, dict) and c.get('name')
            ]
            server = None
            clusters = doc.get('clusters') or []
            if clusters and isinstance(clusters[0], dict):
                server = (clusters[0].get('cluster') or {}).get('server')
            return {
                'contexts': contexts,
                'currentContext': doc.get('current-context') or None,
                'server': server or None,
            }
    except Exception:
        pass
    cur = re.search(r'^current-context:\s*["\']?([^\s"\']+)', text, re.MULTILINE)
    server = re.search(r'^\s*server:\s*["\']?([^\s"\']+)', text, re.MULTILINE)
    return {
        'contexts': [],
        'currentContext': cur.group(1) if cur else None,
        'server': server.group(1) if server else None,
    }


def probe_kubeconfig_candidates(dip_home: str) -> Dict[str, Any]:
    """Discover kubeconfig files usable for direct `kubectl --kubeconfig` audits.

    Sources, in trust order:
      1. containerized-execution configs in general-settings.json declaring
         kubernetesRuntimeConfig.kubeConfigPath (+ optional kubeCtlContext)
      2. $KUBECONFIG of the DSS service user (may be a colon-separated list)
      3. ~/.kube/config
      4. ~/.kube/<dir>/config — one level deep, the common multi-cluster layout

    Non-existent declared paths are still returned (exists=False) so the UI can
    say "declared but missing" instead of silently dropping them.
    """
    started = _now_ms()
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(path: Any, source: str, context: Any = None, exec_config: Any = None) -> None:
        raw = str(path or '').strip()
        if not raw:
            return
        expanded = os.path.expanduser(raw)
        real = os.path.realpath(expanded)
        if real in seen:
            return
        # A missing file is only worth reporting when something declared it
        # (exec config / $KUBECONFIG); an empty default location is just noise.
        if not os.path.isfile(expanded) and source not in ('exec-config', 'env'):
            return
        seen.add(real)
        entry: Dict[str, Any] = {
            'path': expanded,
            'realPath': real,
            'displayPath': _home_shortened(expanded),
            'source': source,
            'execConfig': exec_config or None,
            'context': str(context).strip() if context else None,
            'exists': os.path.isfile(expanded),
            'contexts': [],
            'currentContext': None,
            'server': None,
        }
        if entry['exists']:
            text = _read_file(expanded)
            if text:
                entry.update(_parse_kubeconfig_meta(text))
        out.append(entry)

    gs_text = _read_file(os.path.join(dip_home, 'config', 'general-settings.json'), max_bytes=8 * 1024 * 1024)
    if gs_text:
        try:
            full = json.loads(gs_text)
        except json.JSONDecodeError:
            full = {}
        for cfg in ((full.get('containerSettings') or {}).get('executionConfigs') or []):
            if not isinstance(cfg, dict):
                continue
            krc = cfg.get('kubernetesRuntimeConfig') or {}
            if isinstance(krc, dict) and krc.get('kubeConfigPath'):
                add(krc.get('kubeConfigPath'), 'exec-config',
                    context=krc.get('kubeCtlContext'), exec_config=cfg.get('name'))

    for part in (os.environ.get('KUBECONFIG') or '').split(os.pathsep):
        add(part, 'env')

    home = os.path.expanduser('~')
    add(os.path.join(home, '.kube', 'config'), 'home')
    kube_dir = os.path.join(home, '.kube')
    try:
        for name in sorted(os.listdir(kube_dir)):
            sub = os.path.join(kube_dir, name)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, 'config')):
                add(os.path.join(sub, 'config'), 'home-dir')
    except OSError:
        pass

    return {'ok': True, 'data': out, 'error': None, 'durationMs': _now_ms() - started}


def _home_shortened(path: str) -> str:
    home = os.path.expanduser('~')
    if home and home != '~' and path.startswith(home + os.sep):
        return '~' + path[len(home):]
    return path


def probe_clusters_list(dip_home: str) -> Dict[str, Any]:
    """Walk $DIP_HOME/clusters/ for DSS-managed cluster IDs."""
    started = _now_ms()
    base = os.path.join(dip_home, 'clusters')
    if not os.path.isdir(base):
        return {'ok': True, 'data': [], 'error': None, 'durationMs': _now_ms() - started}
    out: List[Dict[str, Any]] = []
    try:
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                continue
            kubeconfig = _find_kubeconfig(dip_home, name)
            try:
                dir_files = sorted(os.listdir(d))
            except OSError:
                dir_files = []
            out.append({
                'id': name,
                'baseDir': d,
                'hasKubeconfig': kubeconfig is not None,
                'kubeconfig': kubeconfig,
                'dirFiles': dir_files,
            })
    except OSError as exc:
        return {'ok': False, 'data': None, 'error': str(exc), 'durationMs': _now_ms() - started}
    return {'ok': True, 'data': out, 'error': None, 'durationMs': _now_ms() - started}


# ---------- tiny parsers ---------- #


def _parse_topk_cpu(value: str) -> int:
    """`kubectl top` CPU strings: '500m', '1234m', '1' (cores)."""
    s = (value or '').strip()
    if not s:
        return 0
    if s.endswith('m'):
        try:
            return int(float(s[:-1]))
        except ValueError:
            return 0
    try:
        return int(float(s) * 1000)
    except ValueError:
        return 0


def _parse_topk_mem(value: str) -> int:
    """`kubectl top` memory strings: '1234Mi', '2Gi'."""
    s = (value or '').strip()
    if not s:
        return 0
    suffixes = [('Mi', 1.0), ('Gi', 1024.0), ('Ki', 1.0 / 1024), ('Ti', 1024.0 * 1024)]
    for suf, mult in suffixes:
        if s.endswith(suf):
            try:
                return int(float(s[:-len(suf)]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _parse_pct(value: str) -> float:
    s = (value or '').strip().rstrip('%')
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_requests_snippet(text: str) -> Optional[str]:
    """Pull the relevant `requests.get(...)` line for context in findings."""
    for line in text.splitlines():
        if 'requests.get' in line and 'github.com/NVIDIA' in line:
            return line.strip()
    return None


# ---------- GPU code scan ---------- #


# Keyword set used to decide whether a pod's DSS code object touches a GPU at
# all. Matching is intentionally *generous*: the only consumer (the GPU-pod-
# not-using-GPU rule) fires solely on ZERO matches, so a spurious match merely
# suppresses a finding (safe), while a missed keyword would fire one wrongly
# (the costly failure). Bare framework names (xgboost/lightgbm/transformers/tf)
# are weak signals kept for recall — that's an accepted trade since the blast
# radius is bounded by the zero-match condition.
GPU_KEYWORDS = frozenset({
    # Deep-learning frameworks
    'torch', 'pytorch', 'tensorflow', 'tf', 'keras',
    'jax', 'jaxlib', 'flax', 'mxnet', 'paddle',
    # CUDA / low-level
    'cuda', 'cupy', 'numba.cuda', 'pycuda',
    '.cuda(', 'device="cuda"', "device='cuda'",
    # RAPIDS GPU dataframe/ML
    'cudf', 'cuml', 'cugraph', 'rapids',
    # GPU monitoring libs (code that polls the GPU itself)
    'pynvml', 'nvidia-smi', 'gpustat', 'nvitop',
    # Inference / LLM stacks
    'onnxruntime-gpu', 'transformers', 'accelerate', 'deepspeed',
    'bitsandbytes', 'vllm', 'tensorrt',
    # Vector search
    'faiss-gpu',
    # Gradient-boosting GPU switches
    'gpu_hist', "device='gpu'", 'device="gpu"',
    'xgboost', 'lightgbm',
})


def _compile_gpu_keyword(kw: str) -> "re.Pattern":
    """Word-like keywords (module/identifier names) match on word boundaries so
    'tf' inside 'utf-8' doesn't count; keywords carrying punctuation (e.g.
    '.cuda(', "device='cuda'") match as plain case-insensitive substrings."""
    if re.fullmatch(r'[a-z0-9_]+', kw):
        return re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
    return re.compile(re.escape(kw), re.IGNORECASE)


_GPU_KEYWORD_PATTERNS: Dict[str, "re.Pattern"] = {kw: _compile_gpu_keyword(kw) for kw in GPU_KEYWORDS}


def scan_gpu_keywords(source: Optional[str]) -> List[str]:
    """Return the sorted GPU-library keywords present in `source` (pure)."""
    if not source:
        return []
    found = {kw for kw, pat in _GPU_KEYWORD_PATTERNS.items() if pat.search(source)}
    return sorted(found)


def _gpu_keyword_snippet(source: str, matched: List[str]) -> Optional[str]:
    """First source line containing any matched keyword, trimmed (context only)."""
    pats = [_GPU_KEYWORD_PATTERNS[kw] for kw in matched if kw in _GPU_KEYWORD_PATTERNS]
    for line in (source or '').splitlines():
        if any(p.search(line) for p in pats):
            return line.strip()[:200]
    return None


# ---------- DSS execution-pod identity ---------- #


# DSS stamps execution pods with identity metadata. The *annotation* form
# carries the unsanitized values (project key in its real case, object id with
# spaces intact); the *label* form is k8s-sanitized (lowercased, spaces→dashes)
# and will NOT resolve against the DSS API/URLs — so annotations are primary
# and labels are only a last-resort fallback. Confirmed annotation keys seen on
# a live DSS GPU pod: 'dataiku.com/dku-project-key', 'dataiku.com/dku-notebook-id'.
# (Verify the recipe-id / execution-type / submitter keys against a live pod —
# they degrade gracefully to None when absent.)
_DKU_PROJECT_KEY_KEYS = ('dataiku.com/dku-project-key',)
_DKU_NOTEBOOK_ID_KEYS = ('dataiku.com/dku-notebook-id',)
_DKU_RECIPE_ID_KEYS = ('dataiku.com/dku-recipe-id',)
_DKU_EXEC_TYPE_KEYS = ('dataiku.com/dku-execution-type',)
_DKU_SUBMITTER_KEYS = (
    'dataiku.com/dku-submitted-by', 'dataiku.com/dku-user',
    'dataiku.com/dku-author', 'dataiku.com/dku-login',
)


def _meta_lookup(annotations: Dict[str, str], labels: Dict[str, str], keys: Tuple[str, ...]) -> Optional[str]:
    """First non-empty value across `keys`; annotations preferred over labels."""
    for k in keys:
        v = annotations.get(k)
        if v:
            return v
    for k in keys:
        v = labels.get(k)
        if v:
            return v
    return None


def _dku_identity(pod: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Resolve {projectKey, objectType, objectId, submitter, execType} for a DSS
    execution pod from its annotations (preferred) / labels (fallback). Pure."""
    meta = (pod or {}).get('metadata') or {}
    annotations = meta.get('annotations') or {}
    labels = meta.get('labels') or {}
    project_key = _meta_lookup(annotations, labels, _DKU_PROJECT_KEY_KEYS)
    exec_type = _meta_lookup(annotations, labels, _DKU_EXEC_TYPE_KEYS)
    notebook_id = _meta_lookup(annotations, labels, _DKU_NOTEBOOK_ID_KEYS)
    recipe_id = _meta_lookup(annotations, labels, _DKU_RECIPE_ID_KEYS)
    submitter = _meta_lookup(annotations, labels, _DKU_SUBMITTER_KEYS)

    et = (exec_type or '').lower()
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    if 'notebook' in et:
        object_type, object_id = 'notebook', notebook_id
    elif 'recipe' in et:
        object_type, object_id = 'recipe', recipe_id
    # Fall back to whichever id annotation is present when exec-type is missing.
    if object_type is None:
        if notebook_id:
            object_type, object_id = 'notebook', notebook_id
        elif recipe_id:
            object_type, object_id = 'recipe', recipe_id

    return {
        'projectKey': project_key,
        'objectType': object_type,
        'objectId': object_id,
        'submitter': submitter,
        'execType': exec_type,
    }


_SAFE_K8S_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


def is_safe_k8s_name(value: Optional[str]) -> bool:
    """True for a k8s name/namespace safe to splice into a kubectl arg string:
    no whitespace, no shell metacharacters. Defense-in-depth before exec."""
    if not value or len(value) > 253:
        return False
    return bool(_SAFE_K8S_NAME_RE.match(value))


# ---------- nvidia-smi parsers ---------- #


def parse_nvidia_smi_gpu(text: Optional[str]) -> Dict[str, Any]:
    """Parse `nvidia-smi --query-gpu=utilization.gpu,memory.used
    --format=csv,noheader,nounits` — one CSV row per GPU: 'util%, mem_MiB'.

    Returns {'ok', 'utilPct' (max across GPUs), 'memUsedMib' (max), 'gpuCount'}.
    ok=False when nothing parsed (garbage / empty)."""
    rows: List[Tuple[int, int]] = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            continue
        try:
            util = int(float(parts[0]))
            mem = int(float(parts[1]))
        except ValueError:
            continue
        rows.append((util, mem))
    if not rows:
        return {'ok': False, 'utilPct': 0, 'memUsedMib': 0, 'gpuCount': 0}
    return {
        'ok': True,
        'utilPct': max(r[0] for r in rows),
        'memUsedMib': max(r[1] for r in rows),
        'gpuCount': len(rows),
    }


def parse_nvidia_smi_compute_apps(text: Optional[str]) -> Dict[str, int]:
    """Parse `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader`
    — one row per attached compute process: 'pid, used_memory[ MiB]'.

    Returns {'procCount', 'memMib' (summed)}. Empty output → zero processes."""
    proc_count = 0
    mem_total = 0
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(',')]
        if not parts or not parts[0]:
            continue
        try:
            int(parts[0])  # first column is a PID; require it numeric
        except ValueError:
            continue
        proc_count += 1
        if len(parts) >= 2:
            digits = re.sub(r'[^0-9]', '', parts[1])
            if digits:
                mem_total += int(digits)
    return {'procCount': proc_count, 'memMib': mem_total}


# ---------- GPU pod probe (code scan + live nvidia-smi) ---------- #


def _pod_gpu_request(pod: Dict[str, Any]) -> int:
    """Sum nvidia.com/gpu requests across a pod's containers."""
    total = 0
    for c in (((pod or {}).get('spec') or {}).get('containers') or []):
        req = ((c or {}).get('resources') or {}).get('requests') or {}
        try:
            total += int(req.get('nvidia.com/gpu') or 0)
        except (TypeError, ValueError):
            pass
    return total


def _live_nvidia_smi(kubectl: KubectlFn, namespace: str, pod_name_: str) -> Dict[str, Any]:
    """`kubectl exec <pod> -- nvidia-smi ...` for a single pod. Never raises.

    Returns {gpuUtilPct, gpuComputeProcCount, gpuComputeMemMib, gpuBusy,
    nvidiaSmiOk, nvidiaSmiError}. gpuBusy is generous toward "legit" use — a
    process holding a CUDA context counts even at 0% instantaneous util, so we
    don't kill a job that's momentarily between kernel launches. Any failure
    (RBAC-forbidden exec, missing nvidia-smi, terminating pod) → nvidiaSmiOk
    False and is NOT treated as busy (caller falls back to code-scan-only)."""
    base = {
        'gpuUtilPct': None, 'gpuComputeProcCount': None, 'gpuComputeMemMib': None,
        'gpuBusy': False, 'nvidiaSmiOk': False, 'nvidiaSmiError': None,
    }
    if not (is_safe_k8s_name(namespace) and is_safe_k8s_name(pod_name_)):
        return {**base, 'nvidiaSmiError': 'unsafe pod identifier'}
    # No -i/-t: there is no TTY in a macro kernel.
    util_raw = _run_kubectl(
        kubectl,
        f'exec {pod_name_} -n {namespace} -- nvidia-smi '
        '--query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits',
    )
    if not util_raw['ok']:
        return {**base, 'nvidiaSmiError': (util_raw.get('error') or 'kubectl exec failed')[:300]}
    parsed = parse_nvidia_smi_gpu(util_raw['text'])
    if not parsed['ok']:
        return {**base, 'nvidiaSmiError': f'unparseable nvidia-smi output: {util_raw["text"][:120]!r}'}
    # Attached compute processes (optional refinement — failure is non-fatal).
    apps = {'procCount': 0, 'memMib': 0}
    apps_raw = _run_kubectl(
        kubectl,
        f'exec {pod_name_} -n {namespace} -- nvidia-smi '
        '--query-compute-apps=pid,used_memory --format=csv,noheader',
    )
    if apps_raw['ok']:
        apps = parse_nvidia_smi_compute_apps(apps_raw['text'])
    util_pct = parsed['utilPct']
    proc_count = apps['procCount']
    return {
        'gpuUtilPct': util_pct,
        'gpuComputeProcCount': proc_count,
        'gpuComputeMemMib': apps['memMib'],
        'gpuBusy': util_pct > 0 or proc_count > 0,
        'nvidiaSmiOk': True,
        'nvidiaSmiError': None,
    }


def probe_gpu_pod_code(
    pods: List[Dict[str, Any]],
    read_object_source: Callable[[str, str, str], Tuple[Optional[str], Optional[str]]],
    kubectl: Optional[KubectlFn],
) -> Dict[str, Any]:
    """For every GPU-requesting pod, resolve its DSS code object and decide
    whether the code touches a GPU; for the candidates whose code uses NO GPU
    library, refine with a live `nvidia-smi` exec.

    `read_object_source(project_key, kind, object_id) -> (source|None, error)`
    is injected from runnable.py so this stays dataiku-free; `kubectl` is the
    existing runner (None disables the live tier — code-scan-only fallback).

    Returns an envelope whose `data` maps "{ns}/{name}" -> per-pod entry. Never
    raises: per-pod failures are recorded on the entry, not propagated."""
    started = _now_ms()
    entries: Dict[str, Dict[str, Any]] = {}
    candidates: List[str] = []  # pod keys for Tier-2 live nvidia-smi (no-keyword or unreadable code)
    for pod in (pods or []):
        gpu_req = _pod_gpu_request(pod)
        if gpu_req <= 0:
            continue
        meta = (pod or {}).get('metadata') or {}
        ns = meta.get('namespace') or ''
        name = meta.get('name') or ''
        node = ((pod or {}).get('spec') or {}).get('nodeName') or ''
        pod_key = f'{ns}/{name}'
        ident = _dku_identity(pod)
        entry: Dict[str, Any] = {
            'namespace': ns, 'name': name, 'node': node, 'requestedGpu': gpu_req,
            'projectKey': ident['projectKey'], 'objectType': ident['objectType'],
            'objectId': ident['objectId'], 'submitter': ident['submitter'],
            'execType': ident['execType'],
            'resolved': False, 'gpuKeywordsFound': False, 'matchedKeywords': [],
            'snippet': None, 'sourceChars': 0,
            'gpuUtilPct': None, 'gpuComputeProcCount': None, 'gpuComputeMemMib': None,
            'gpuBusy': False, 'nvidiaSmiOk': False, 'nvidiaSmiError': None, 'error': None,
        }
        if not (ident['projectKey'] and ident['objectType'] and ident['objectId']):
            entry['error'] = 'could not resolve DSS object identity from pod annotations/labels'
            entries[pod_key] = entry
            continue
        # Tier 1: code scan.
        source, err = read_object_source(ident['projectKey'], ident['objectType'], ident['objectId'])
        if err or source is None:
            # Code object couldn't be read — most often the pod belongs to another
            # DSS instance sharing this cluster, so its project isn't local. We have
            # no static signal, but a live nvidia-smi exec still works on any pod, so
            # queue it for Tier 2 and let the rule fall back to live GPU evidence.
            entry['error'] = (err or 'no source returned')[:300]
            entries[pod_key] = entry
            candidates.append(pod_key)
            continue
        entry['resolved'] = True
        entry['sourceChars'] = len(source)
        matched = scan_gpu_keywords(source)
        entry['matchedKeywords'] = matched
        entry['gpuKeywordsFound'] = bool(matched)
        if matched:
            entry['snippet'] = _gpu_keyword_snippet(source, matched)
        else:
            candidates.append(pod_key)  # Tier 2 candidate
        entries[pod_key] = entry

    # Tier 2: live nvidia-smi, candidates only, parallelized (candidates are few).
    if candidates and kubectl is not None:
        def _check(pod_key: str) -> Tuple[str, Dict[str, Any]]:
            e = entries[pod_key]
            return pod_key, _live_nvidia_smi(kubectl, e['namespace'], e['name'])
        max_workers = min(8, len(candidates))
        with cf.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='gpusmi') as pool:
            for pod_key, live in pool.map(_check, candidates):
                entries[pod_key].update(live)

    return {'ok': True, 'data': entries, 'error': None, 'durationMs': _now_ms() - started}
