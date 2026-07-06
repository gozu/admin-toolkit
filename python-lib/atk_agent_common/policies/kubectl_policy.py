"""kubectl mutation policy — verb/kind/namespace/token whitelist.

Commands are arg strings (everything after the word ``kubectl``), matching the
``cluster.run_kubectl(args)`` runner. The macro re-validates every command
with :func:`validate` immediately before running it — the plan-side check is
UX only. ``apply`` is only accepted in the exact form ``apply -f {manifest}``
(optionally ``-n <ns>``): the macro substitutes the placeholder with its own
tempfile after :func:`validate_manifest` approved every document.
"""

import shlex

ALLOWED_VERBS = ('apply', 'patch', 'delete', 'label', 'annotate', 'scale', 'rollout')

# kind aliases → canonical namespaced-mutable kind. Secrets deliberately absent.
_KIND_ALIASES = {
    'po': 'pod', 'pod': 'pod', 'pods': 'pod',
    'deploy': 'deployment', 'deployment': 'deployment', 'deployments': 'deployment',
    'ds': 'daemonset', 'daemonset': 'daemonset', 'daemonsets': 'daemonset',
    'sts': 'statefulset', 'statefulset': 'statefulset', 'statefulsets': 'statefulset',
    'rs': 'replicaset', 'replicaset': 'replicaset', 'replicasets': 'replicaset',
    'job': 'job', 'jobs': 'job',
    'cj': 'cronjob', 'cronjob': 'cronjob', 'cronjobs': 'cronjob',
    'svc': 'service', 'service': 'service', 'services': 'service',
    'cm': 'configmap', 'configmap': 'configmap', 'configmaps': 'configmap',
    'pdb': 'poddisruptionbudget', 'poddisruptionbudget': 'poddisruptionbudget',
    'poddisruptionbudgets': 'poddisruptionbudget',
    'hpa': 'horizontalpodautoscaler', 'horizontalpodautoscaler': 'horizontalpodautoscaler',
    'horizontalpodautoscalers': 'horizontalpodautoscaler',
    'ing': 'ingress', 'ingress': 'ingress', 'ingresses': 'ingress',
}
ALLOWED_KINDS = frozenset(_KIND_ALIASES.values())

# Explicitly forbidden kinds get a pointed refusal (everything not whitelisted
# is refused anyway — this is for a clearer message on the dangerous ones).
FORBIDDEN_KINDS = frozenset((
    'node', 'nodes', 'no',
    'namespace', 'namespaces', 'ns',
    'pv', 'persistentvolume', 'persistentvolumes',
    'pvc', 'persistentvolumeclaim', 'persistentvolumeclaims',
    'clusterrole', 'clusterroles', 'clusterrolebinding', 'clusterrolebindings',
    'role', 'roles', 'rolebinding', 'rolebindings',
    'crd', 'crds', 'customresourcedefinition', 'customresourcedefinitions',
    'storageclass', 'storageclasses', 'sc',
    'mutatingwebhookconfiguration', 'mutatingwebhookconfigurations',
    'validatingwebhookconfiguration', 'validatingwebhookconfigurations',
    'secret', 'secrets',
    'serviceaccount', 'serviceaccounts', 'sa',
    'priorityclass', 'priorityclasses',
    'apiservice', 'apiservices',
))

# Tokens that must never appear anywhere in an agent-built command: blast-
# radius wideners, force flags, and anything that redirects auth/targeting.
FORBIDDEN_TOKENS = frozenset((
    '--all', '--all-namespaces', '-A',
    '--force', '--grace-period=0', '--now',
    '--kubeconfig', '--context', '--cluster', '--server', '--user',
    '--as', '--as-group', '--as-uid', '--token', '--username', '--password',
    '--insecure-skip-tls-verify', '--raw', '--cascade=orphan',
    '--prune',
))

PROTECTED_NAMESPACES = ('kube-system', 'kube-public', 'kube-node-lease')

# In kube-system, only these verb×kind combinations are permitted (the
# nvidia-device-plugin daemonset case and its close relatives).
_KUBE_SYSTEM_VERBS = frozenset(('patch', 'label', 'annotate', 'rollout'))
_KUBE_SYSTEM_KINDS = frozenset(('daemonset', 'deployment'))

MANIFEST_PLACEHOLDER = '{manifest}'

# Dash-flags known to consume the following token when written without '='.
_VALUE_FLAGS = frozenset((
    '-n', '--namespace', '-p', '--patch', '--type', '-l', '--selector',
    '-f', '--filename', '--replicas', '--timeout', '-o', '--output',
    '--patch-file', '--field-selector',
))


def _tokenize(args):
    if isinstance(args, (list, tuple)):
        return [str(t) for t in args]
    return shlex.split(str(args or ''))


def _refuse(reason):
    return False, reason, None


def _parse_flags_and_positionals(tokens):
    """Split tokens into (positionals, flag_map). flag_map keeps the LAST value
    per flag. Unknown boolean flags are kept in flag_map with value True."""
    positionals = []
    flags = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith('-'):
            if '=' in tok:
                key, val = tok.split('=', 1)
                flags[key] = val
            elif tok in _VALUE_FLAGS and i + 1 < len(tokens):
                flags[tok] = tokens[i + 1]
                i += 1
            else:
                flags[tok] = True
        else:
            positionals.append(tok)
        i += 1
    return positionals, flags


def _namespace_from_flags(flags):
    return flags.get('-n') or flags.get('--namespace') or 'default'


def _grace_period_zero(tokens):
    for i, tok in enumerate(tokens):
        if tok == '--grace-period' and i + 1 < len(tokens) and tokens[i + 1].strip() == '0':
            return True
        if tok.startswith('--grace-period=') and tok.split('=', 1)[1].strip() == '0':
            return True
    return False


def validate(args):
    """Validate one kubectl arg string. Returns (ok, reason, parsed).

    parsed (on success) = {'verb', 'kind', 'name', 'namespace', 'argv'}.
    Never raises on malformed input — refuses instead.
    """
    try:
        tokens = _tokenize(args)
    except ValueError as exc:
        return _refuse('unparseable command: %s' % exc)
    if tokens and tokens[0] == 'kubectl':
        tokens = tokens[1:]
    if not tokens:
        return _refuse('empty command')

    for tok in tokens:
        base = tok.split('=', 1)[0] if tok.startswith('--') else tok
        if tok in FORBIDDEN_TOKENS or base in FORBIDDEN_TOKENS:
            return _refuse('forbidden token %r' % tok)
    if _grace_period_zero(tokens):
        return _refuse('forbidden token --grace-period=0')

    verb = tokens[0]
    if verb not in ALLOWED_VERBS:
        return _refuse('verb %r not allowed (allowed: %s)' % (verb, ', '.join(ALLOWED_VERBS)))

    rest = tokens[1:]
    if verb == 'rollout':
        if not rest or rest[0] != 'restart':
            return _refuse('only `rollout restart` is allowed')
        rest = rest[1:]

    positionals, flags = _parse_flags_and_positionals(rest)
    namespace = _namespace_from_flags(flags)

    if verb == 'apply':
        manifest_ref = flags.get('-f') or flags.get('--filename')
        if manifest_ref != MANIFEST_PLACEHOLDER:
            return _refuse('apply is only allowed as `apply -f %s` — the macro supplies the '
                           'manifest file after validating manifest_yaml' % MANIFEST_PLACEHOLDER)
        if positionals:
            return _refuse('unexpected arguments to apply: %s' % positionals)
        parsed = {'verb': 'apply', 'kind': None, 'name': None,
                  'namespace': namespace, 'argv': tokens}
        if namespace in PROTECTED_NAMESPACES:
            return _refuse('apply is forbidden in namespace %r' % namespace)
        return True, 'ok', parsed

    if not positionals:
        return _refuse('missing resource kind')
    kind_tok = positionals[0]
    name = positionals[1] if len(positionals) > 1 else None
    if '/' in kind_tok:
        kind_tok, name = kind_tok.split('/', 1)
    kind_lc = kind_tok.lower()
    if kind_lc in FORBIDDEN_KINDS:
        return _refuse('kind %r is forbidden (cluster-scoped or security-sensitive)' % kind_tok)
    kind = _KIND_ALIASES.get(kind_lc)
    if kind is None:
        return _refuse('kind %r is not in the namespaced-mutable whitelist' % kind_tok)
    if not name and verb in ('patch', 'delete', 'label', 'annotate', 'scale', 'rollout'):
        return _refuse('a specific resource name is required (no selector-wide mutations)')

    if namespace in PROTECTED_NAMESPACES:
        if verb == 'delete':
            return _refuse('delete is forbidden in namespace %r' % namespace)
        if namespace != 'kube-system':
            return _refuse('mutations are forbidden in namespace %r' % namespace)
        if verb not in _KUBE_SYSTEM_VERBS or kind not in _KUBE_SYSTEM_KINDS:
            return _refuse('in kube-system only patch/label/annotate/rollout-restart on '
                           'daemonsets/deployments is allowed')

    parsed = {'verb': verb, 'kind': kind, 'name': name,
              'namespace': namespace, 'argv': tokens}
    return True, 'ok', parsed


def validate_manifest(manifest_yaml):
    """Apply the kind/namespace policy to every document of a YAML manifest.

    Returns (ok, reason, docs) where docs = [{'kind', 'name', 'namespace'}].
    Fails CLOSED when PyYAML is unavailable.
    """
    if not (manifest_yaml or '').strip():
        return False, 'empty manifest', []
    try:
        import yaml
    except ImportError:
        return False, ('PyYAML is not installed in this environment — manifests cannot be '
                       'policy-checked, so apply is refused (fail closed)'), []
    try:
        docs = [d for d in yaml.safe_load_all(manifest_yaml) if d is not None]
    except yaml.YAMLError as exc:
        return False, 'manifest YAML parse failed: %s' % str(exc)[:200], []
    if not docs:
        return False, 'manifest contains no documents', []
    summaries = []
    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            return False, 'manifest document %d is not a mapping' % i, []
        kind_raw = str(doc.get('kind') or '')
        meta = doc.get('metadata') or {}
        name = meta.get('name')
        namespace = meta.get('namespace') or 'default'
        kind_lc = kind_raw.lower()
        if kind_lc in FORBIDDEN_KINDS:
            return False, 'manifest document %d: kind %r is forbidden' % (i, kind_raw), []
        kind = _KIND_ALIASES.get(kind_lc)
        if kind is None:
            return False, ('manifest document %d: kind %r is not in the namespaced-mutable '
                           'whitelist' % (i, kind_raw)), []
        if namespace in PROTECTED_NAMESPACES:
            return False, ('manifest document %d targets protected namespace %r — apply is '
                           'forbidden there' % (i, namespace)), []
        if not name:
            return False, 'manifest document %d has no metadata.name' % i, []
        summaries.append({'kind': kind, 'name': name, 'namespace': namespace})
    return True, 'ok', summaries
