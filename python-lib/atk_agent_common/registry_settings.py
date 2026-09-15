"""Registry hints from both legacy execution settings and DSS 15 build configs."""
import re


def registry_hint(settings):
    cs = settings.get('containerSettings') if isinstance(settings, dict) else None
    if not isinstance(cs, dict):
        return None
    configs = cs.get('executionConfigs') or []
    builds = cs.get('buildConfigs') or []
    if not isinstance(configs, list) or not isinstance(builds, list):
        return None
    by_name = {c.get('name'): c for c in builds if isinstance(c, dict) and isinstance(c.get('name'), str)}
    ordered = [c for c in configs if isinstance(c, dict)]
    ordered.sort(key=lambda c: c.get('name') != cs.get('defaultExecutionConfig'))
    generic = cs.get('executionConfigsGenericOverrides')
    if isinstance(generic, dict):
        ordered.append(generic)
    candidates = []
    for config in ordered:
        candidates.append(config.get('repositoryURL'))
        reference = config.get('imageBuildConfig')
        build = by_name.get(reference, {}) if isinstance(reference, str) else {}
        for key in ('dockerBuilderConfig', 'dkuInClusterBuilderConfig'):
            builder = build.get(key) or {}
            pushes = builder.get('pushConfigs') if isinstance(builder, dict) else None
            if isinstance(pushes, list):
                candidates.extend(p.get('repositoryURL') for p in pushes if isinstance(p, dict))
    patterns = (
        ('ecr', r'^(?:https?://)?\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(?:/|$)'),
        ('acr', r'^(?:https?://)?[a-zA-Z0-9]+\.azurecr\.io(?:/|$)'),
        ('gar', r'^(?:https?://)?(?:[a-z0-9-]+-docker\.pkg\.dev|(?:[a-z0-9-]+\.)?gcr\.io)(?:/|$)'),
    )
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        url = candidate.strip()
        for provider, pattern in patterns:
            if re.match(pattern, url, re.I):
                return {'provider': provider, 'registryUrl': url}
    return None
