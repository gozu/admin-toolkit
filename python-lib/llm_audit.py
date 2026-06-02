"""LLM model upgrade audit module.

Adapted from in-progress/litellm_model_upgrade_audit.py (CLI tool) for use as a
library inside the admin-toolkit webapp backend. The classification rules and
grouping logic are unchanged; we extend PROVIDERS so that reseller pricing
(Bedrock, Azure, Snowflake) folds into the same canonical model families as the
direct provider APIs, and we add a webapp-side adapter that takes a DSS
project.list_llms() row and returns a classification verdict.

The pricing source is LiteLLM's public pricing JSON. No hardcoded prices.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

USER_AGENT = "Mozilla/5.0 (compatible; admin-toolkit-llm-audit/1.0)"

# Reseller litellm_provider values fold into the canonical family of their
# upstream model — the user requirement is "same model, same verdict, regardless
# of where you bought it." Bedrock anthropic.* models join the Anthropic family,
# Azure gpt-* deployments join the OpenAI family, etc.
PROVIDERS = {
    "openai": {
        "display": "OpenAI",
        "litellm_providers": {"openai", "text-completion-openai", "azure", "azure_ai"},
        "strip_prefixes": ("azure/", "azure_ai/"),
    },
    "anthropic": {
        "display": "Anthropic",
        "litellm_providers": {"anthropic", "bedrock", "bedrock_converse"},
        "strip_prefixes": ("bedrock/",),
    },
    "gemini": {
        "display": "Google Gemini",
        "litellm_providers": {"gemini", "vertex_ai-language-models"},
        "strip_prefixes": ("gemini/", "vertex_ai/"),
    },
}

ALLOWED_MODES = {"chat", "completion", "responses"}

NON_LLM_NAME_TERMS = {
    "audio", "computer-use", "dall-e", "embedding", "image", "imagen",
    "learnlm", "live-preview", "lyria", "moderation", "native-audio",
    "realtime", "robotics", "sora", "speech", "tts", "transcribe", "veo",
    "whisper",
}

SPECIALIZED_NAME_TERMS = {
    "codex", "deep-research", "experimental", "gemini-exp", "gemma",
    "search-api", "search-preview",
}

# Regional / availability prefixes used by Bedrock cross-region inference profiles.
_BEDROCK_REGION_PREFIXES = ("global.", "us.", "eu.", "apac.", "au.", "ap.")
# Provider prefixes baked into Bedrock model IDs (and some Azure deployments).
_BEDROCK_PROVIDER_PREFIXES = (
    "anthropic.", "meta.", "mistral.", "amazon.", "cohere.", "ai21.",
    "deepseek.",
)
# Bedrock model version suffix, e.g. "-v1:0", "-v2:0", "-v1".
_BEDROCK_VERSION_SUFFIX_RE = re.compile(r"-v\d+(?::\d+)?$")
# Bedrock region-bracketed path, e.g. "ap-northeast-1/" or "us-gov-east-1/".
_BEDROCK_REGION_PATH_RE = re.compile(r"^[a-z]{2,5}(?:-[a-z0-9]+){1,3}/")

# Fuzzy patterns used as a last-resort inference for opaque Azure deployment
# names (e.g. "USNPDGPT35", "prod-gpt4-turbo") that literal / canonicalized
# candidates fail to match. Ordered most specific to least specific so that
# "gpt-5.2" wins over "gpt-5" when both could match. Case-insensitive.
AZURE_FUZZY_PATTERNS = [
    (re.compile(r"gpt[\W_]?5[\W_]?2", re.I), "gpt-5.2"),
    (re.compile(r"gpt[\W_]?5[\W_]?1", re.I), "gpt-5.1"),
    (re.compile(r"gpt[\W_]?5", re.I), "gpt-5"),
    (re.compile(r"gpt[\W_]?4[\W_]?1", re.I), "gpt-4.1"),
    (re.compile(r"gpt[\W_]?4[\W_]?o", re.I), "gpt-4o"),
    (re.compile(r"gpt[\W_]?4[\W_]?turbo", re.I), "gpt-4-turbo"),
    (re.compile(r"gpt[\W_]?4", re.I), "gpt-4"),
    (re.compile(r"gpt[\W_]?3[\W_]?5", re.I), "gpt-3.5-turbo"),
    (re.compile(r"o4[\W_]?mini", re.I), "o4-mini"),
    (re.compile(r"o3[\W_]?mini", re.I), "o3-mini"),
    (re.compile(r"o3", re.I), "o3"),
    (re.compile(r"o1", re.I), "o1"),
]


def azure_fuzzy_infer(raw: str) -> str | None:
    """Infer a canonical OpenAI model from an opaque Azure deployment name."""
    if not raw:
        return None
    for pattern, canon in AZURE_FUZZY_PATTERNS:
        if pattern.search(raw):
            return canon
    return None


@dataclass
class ModelGroup:
    provider: str
    family: str
    canonical_model: str
    input_price: Decimal
    output_price: Decimal
    version: tuple[Decimal, ...]
    aliases: set[str] = field(default_factory=set)


@dataclass
class AuditFamily:
    provider: str
    family: str
    current: ModelGroup
    current_aliases: list[str]
    ripoff: list[ModelGroup]
    obsolete: list[ModelGroup]


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, "replace"))


def _decimal_from_cost(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _per_million(value: Any) -> Decimal | None:
    cost = _decimal_from_cost(value)
    if cost is None:
        return None
    return cost * Decimal("1000000")


def _decimal_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _strip_provider_path_prefixes(model: str, provider_key: str) -> str:
    """Strip litellm-style path prefixes like 'azure/', 'bedrock/', 'gemini/'."""
    for prefix in PROVIDERS[provider_key]["strip_prefixes"]:
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    return model


def _strip_bedrock_decorations(model: str) -> str:
    """Strip Bedrock-specific region/provider prefixes and version suffix.

    Examples:
      bedrock/us-gov-west-1/anthropic.claude-3-5-sonnet-20240620-v1:0
        -> claude-3-5-sonnet-20240620
      us.anthropic.claude-opus-4-5-20251101-v1:0
        -> claude-opus-4-5-20251101
      us.meta.llama4-maverick-17b-instruct-v1:0
        -> llama4-maverick-17b-instruct
    """
    # Strip "bedrock/" or "bedrock_converse/" path prefix.
    for prefix in ("bedrock/", "bedrock_converse/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    # Strip "<region>/" segment (e.g. "us-east-1/", "us-gov-west-1/").
    model = _BEDROCK_REGION_PATH_RE.sub("", model)
    # Strip "invoke/" sub-prefix (some Bedrock entries use it).
    if model.startswith("invoke/"):
        model = model[len("invoke/"):]
    # Strip leading region prefix like "us.", "eu.", "global.".
    for prefix in _BEDROCK_REGION_PREFIXES:
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    # Strip leading provider prefix like "anthropic.", "meta.".
    for prefix in _BEDROCK_PROVIDER_PREFIXES:
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    # Strip Bedrock version suffix like "-v1:0".
    model = _BEDROCK_VERSION_SUFFIX_RE.sub("", model)
    return model


def _strip_date_suffix(model: str) -> str:
    # LiteLLM also uses "@YYYYMMDD" / "@YYYY-MM-DD" snapshot syntax (e.g.
    # "claude-haiku-4-5@20251001"); collapse those before the dash variants.
    model = re.sub(r"@\d{4}-\d{2}-\d{2}$", "", model)
    model = re.sub(r"@\d{8}$", "", model)
    model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    model = re.sub(r"-\d{8}$", "", model)
    model = re.sub(r"-(?:preview-)?\d{2}-\d{4}$", "-preview", model)
    model = re.sub(r"-(?:preview-)?\d{4}$", "-preview", model)
    model = re.sub(r"-(\d{4})(?=-preview$)", "", model)
    model = re.sub(r"-001$", "", model)
    return model


def canonical_model(provider_key: str, model: str) -> str:
    """Canonicalize a raw model identifier under a given provider family."""
    model = _strip_provider_path_prefixes(model.lower(), provider_key)
    model = _strip_bedrock_decorations(model)
    # Snowflake Cortex prefixes its OpenAI relays with "openai-"; collapse.
    if model.startswith("openai-"):
        model = model[len("openai-"):]
    model = _strip_date_suffix(model)

    if provider_key == "openai":
        # Azure naming quirk: "gpt-35-turbo" / "gpt-35-turbo-16k" / etc. should
        # map to the canonical "gpt-3.5-turbo" form so version parsing yields
        # (3, 5) instead of (35,) — otherwise this fake "version 35" wins the
        # GPT flagship family as "current".
        model = re.sub(r"^gpt-35-turbo\b", "gpt-3.5-turbo", model)
        model = re.sub(r"^gpt-(5(?:\.\d+)?)-chat(?:-latest)?$", r"gpt-\1", model)
        model = re.sub(r"^gpt-(3\.5-turbo)-instruct(?:-preview)?$", r"gpt-\1-instruct", model)
        model = re.sub(r"^gpt-(3\.5-turbo)(?:-preview)?$", r"gpt-\1", model)

    if provider_key == "anthropic":
        model = re.sub(r"^claude-4-(opus|sonnet|haiku)$", r"claude-\1-4", model)
        model = re.sub(r"^claude-(opus|sonnet|haiku)-4$", r"claude-\1-4", model)
        model = re.sub(r"^claude-3-5-haiku$", "claude-haiku-3-5", model)

    if provider_key == "gemini":
        model = re.sub(r"-customtools$", "", model)

    return model


def _accepts_text_input(info: dict[str, Any]) -> bool:
    modalities = info.get("supported_modalities")
    if modalities is None:
        return True
    return isinstance(modalities, list) and "text" in modalities


def _has_text_output(info: dict[str, Any]) -> bool:
    modalities = info.get("supported_output_modalities")
    if modalities is None:
        return True
    return isinstance(modalities, list) and "text" in modalities


def _model_name_has_term(model: str, terms: set[str]) -> bool:
    normalized = model.lower().replace("_", "-")
    return any(term in normalized for term in terms)


def _provider_key_for(info: dict[str, Any]) -> str | None:
    litellm_provider = info.get("litellm_provider")
    for provider_key, rule in PROVIDERS.items():
        if litellm_provider in rule["litellm_providers"]:
            return provider_key
    return None


def _extract_numbers(value: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(part) for part in re.findall(r"\d+(?:\.\d+)?", value))


def _extract_anthropic_version(model: str) -> tuple[Decimal, ...]:
    if model.startswith(("claude-opus-", "claude-sonnet-", "claude-haiku-")):
        return _extract_numbers(model.split("-", 2)[2])
    return _extract_numbers(model)


def _extract_gemini_version(model: str) -> tuple[Decimal, ...]:
    match = re.match(r"^gemini-(\d+(?:\.\d+)?)(?:-|$)", model)
    if not match:
        return (Decimal("-1"),)
    return tuple(Decimal(part) for part in match.group(1).split("."))


def _extract_openai_gpt_version(model: str) -> tuple[Decimal, ...]:
    if model.startswith("chatgpt-4o"):
        return (Decimal("4"), Decimal("0"))
    match = re.match(r"^gpt-(\d+(?:\.\d+)?)", model)
    if not match:
        return (Decimal("-1"),)
    return tuple(Decimal(part) for part in match.group(1).split("."))


def _extract_openai_o_version(model: str) -> tuple[Decimal, ...]:
    match = re.match(r"^o(\d+)", model)
    if not match:
        return (Decimal("-1"),)
    return (Decimal(match.group(1)),)


def family_for(provider_key: str, model: str) -> tuple[str, tuple[Decimal, ...]] | None:
    if provider_key == "anthropic":
        if "opus" in model:
            return "Opus", _extract_anthropic_version(model)
        if "sonnet" in model:
            return "Sonnet", _extract_anthropic_version(model)
        if "haiku" in model:
            return "Haiku", _extract_anthropic_version(model)
        return None

    if provider_key == "gemini":
        if "flash-lite" in model:
            return "Flash-Lite", _extract_gemini_version(model)
        if "flash" in model:
            return "Flash", _extract_gemini_version(model)
        if "pro" in model:
            return "Pro", _extract_gemini_version(model)
        return None

    if provider_key == "openai":
        if re.match(r"^gpt-\d+(?:\.\d+)?-pro\b", model):
            return "GPT pro", _extract_openai_gpt_version(model)
        if "-nano" in model and model.startswith("gpt-"):
            return "GPT nano", _extract_openai_gpt_version(model)
        if "-mini" in model and model.startswith("gpt-"):
            return "GPT mini", _extract_openai_gpt_version(model)
        if re.match(r"^o\d+-pro\b", model):
            return "Reasoning pro", _extract_openai_o_version(model)
        if re.match(r"^o\d+-mini\b", model):
            return "Reasoning mini", _extract_openai_o_version(model)
        if re.match(r"^o\d+\b", model):
            return "Reasoning", _extract_openai_o_version(model)
        if model.startswith(("gpt-", "chatgpt-", "davinci-", "babbage-")):
            return "GPT flagship", _extract_openai_gpt_version(model)
        return None

    return None


def collect_groups(raw: dict[str, Any]) -> dict[tuple[str, str], dict[str, ModelGroup]]:
    """Group LiteLLM pricing entries into (provider, family) buckets."""
    grouped: dict[tuple[str, str], dict[str, ModelGroup]] = {}

    for raw_model, info in raw.items():
        if not isinstance(info, dict):
            continue

        provider_key = _provider_key_for(info)
        if provider_key is None:
            continue

        if info.get("mode") not in ALLOWED_MODES:
            continue
        if not _accepts_text_input(info) or not _has_text_output(info):
            continue

        canonical = canonical_model(provider_key, raw_model)
        if raw_model.startswith("ft:"):
            continue
        if _model_name_has_term(canonical, NON_LLM_NAME_TERMS):
            continue
        if _model_name_has_term(canonical, SPECIALIZED_NAME_TERMS):
            continue

        family_info = family_for(provider_key, canonical)
        if family_info is None:
            continue
        family, version = family_info

        input_price = _per_million(info.get("input_cost_per_token"))
        output_price = _per_million(info.get("output_cost_per_token"))
        if input_price is None or output_price is None:
            continue

        provider = PROVIDERS[provider_key]["display"]
        family_key = (provider, family)
        by_model = grouped.setdefault(family_key, {})
        existing = by_model.get(canonical)
        if existing is None:
            by_model[canonical] = ModelGroup(
                provider=provider,
                family=family,
                canonical_model=canonical,
                input_price=input_price,
                output_price=output_price,
                version=version,
                aliases={raw_model},
            )
            continue

        existing.aliases.add(raw_model)
        # When the same canonical name appears under multiple resellers (e.g.
        # claude-3-5-sonnet via Bedrock vs direct Anthropic), prefer the lower
        # input price as the canonical price for the model. This avoids treating
        # cross-reseller variation as a status difference.
        if input_price < existing.input_price:
            existing.input_price = input_price
            existing.output_price = output_price

    return grouped


def _model_sort_key(group: ModelGroup) -> tuple[Any, ...]:
    return (tuple(-part for part in group.version), group.canonical_model)


def build_audit(grouped: dict[tuple[str, str], dict[str, ModelGroup]]) -> list[AuditFamily]:
    audit: list[AuditFamily] = []
    for (provider, family), models in grouped.items():
        candidates = [g for g in models.values() if g.version and g.version[0] >= 0]
        if not candidates:
            continue
        current = max(candidates, key=lambda g: (g.version, -g.input_price, -g.output_price))
        ripoff: list[ModelGroup] = []
        obsolete: list[ModelGroup] = []
        for group in models.values():
            if group.canonical_model == current.canonical_model:
                continue
            if group.input_price > current.input_price or group.output_price > current.output_price:
                ripoff.append(group)
            else:
                obsolete.append(group)
        audit.append(
            AuditFamily(
                provider=provider,
                family=family,
                current=current,
                current_aliases=sorted(current.aliases),
                ripoff=sorted(ripoff, key=_model_sort_key),
                obsolete=sorted(obsolete, key=_model_sort_key),
            )
        )
    audit.sort(key=lambda item: (item.provider, item.family))
    return audit


def lookup_json(audit: list[AuditFamily]) -> dict[str, dict[str, Any]]:
    """Flat lookup of canonical_model + every alias -> classification payload."""
    lookup: dict[str, dict[str, Any]] = {}
    for item in audit:
        for status, groups in (("current", [item.current]), ("ripoff", item.ripoff), ("obsolete", item.obsolete)):
            for group in groups:
                payload = {
                    "status": status,
                    "provider": item.provider,
                    "family": item.family,
                    "canonical_model": group.canonical_model,
                    "current_model": item.current.canonical_model,
                    "current_price": {
                        "input_usd_per_1m_tokens": _decimal_number(item.current.input_price),
                        "output_usd_per_1m_tokens": _decimal_number(item.current.output_price),
                    },
                    "model_price": {
                        "input_usd_per_1m_tokens": _decimal_number(group.input_price),
                        "output_usd_per_1m_tokens": _decimal_number(group.output_price),
                    },
                }
                lookup[group.canonical_model] = payload
                for alias in group.aliases:
                    lookup[alias] = payload
    return dict(sorted(lookup.items()))


# -------- Webapp adapter layer --------


class PricingFetchError(Exception):
    pass


def build_lookup(timeout: int = 30) -> dict[str, dict[str, Any]]:
    """Fetch the LiteLLM catalog and produce the flat classification lookup."""
    try:
        raw = fetch_json(LITELLM_PRICING_URL, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise PricingFetchError(str(exc)) from exc
    return lookup_json(build_audit(collect_groups(raw)))


# DSS LLM types whose row is a meta-wrapper with no upstream model.
NOT_APPLICABLE_TYPES = frozenset({"SAVED_MODEL_AGENT", "RETRIEVAL_AUGMENTED"})


_PROVIDER_KEY_BY_DSS_TYPE = {
    "OPENAI": "openai",
    "AZURE_OPENAI_DEPLOYMENT": "openai",
    "ANTHROPIC": "anthropic",
    "BEDROCK": None,           # determined per model (Anthropic vs Meta vs Mistral...)
    "VERTEX": "gemini",
    "SNOWFLAKE_CORTEX": None,  # determined per model
    "HUGGINGFACE_TRANSFORMER_LOCAL": None,
    "CUSTOM": None,
    "SAVED_MODEL_AGENT": None,
    "RETRIEVAL_AUGMENTED": None,
}


def _candidate_provider_keys(dss_type: str, raw_model: str) -> list[str]:
    """Return ordered provider keys to attempt canonicalization with."""
    forced = _PROVIDER_KEY_BY_DSS_TYPE.get(dss_type)
    if forced is not None:
        return [forced]
    # Inferable from model name shape.
    rm = (raw_model or "").lower()
    if "anthropic." in rm or "claude" in rm:
        return ["anthropic"]
    if "gemini" in rm:
        return ["gemini"]
    if "meta." in rm or "llama" in rm:
        # Llama models aren't in our classified families, but we still try
        # all providers below to record an explicit "unknown".
        return ["openai", "anthropic", "gemini"]
    return ["openai", "anthropic", "gemini"]


def normalize_for_audit(dss_type: str, model: str | None, deployment: str | None) -> list[str]:
    """Generate ordered candidate lookup keys for a given DSS LLM row.

    The classification lookup is keyed by canonical model name plus aliases. We
    try the raw string, the path-prefixed forms (e.g. azure/x, gemini/x), and
    the fully canonicalized form under each plausible provider family.
    """
    raw = (model or deployment or "").strip()
    if not raw:
        return []

    candidates: list[str] = [raw]

    # Path-prefixed variants — these match LiteLLM aliases that include the
    # reseller prefix.
    if dss_type == "AZURE_OPENAI_DEPLOYMENT":
        candidates.append(f"azure/{raw}")
    if dss_type == "VERTEX":
        candidates.append(f"gemini/{raw}")
        candidates.append(f"vertex_ai/{raw}")
    if dss_type == "BEDROCK":
        candidates.append(f"bedrock/{raw}")
    if dss_type == "SNOWFLAKE_CORTEX":
        candidates.append(f"snowflake/{raw}")

    # Provider-canonical forms.
    for pk in _candidate_provider_keys(dss_type, raw):
        canon = canonical_model(pk, raw)
        if canon and canon not in candidates:
            candidates.append(canon)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _custom_unwrap(llm_row: dict[str, Any], connections_by_name: dict[str, Any] | None) -> str | None:
    """For a CUSTOM LLM, look up its connection's plugin model config and return
    the upstream model string (customConfig.model or .model_id), if any."""
    if not connections_by_name:
        return None
    conn = connections_by_name.get(llm_row.get("connection") or "")
    if not isinstance(conn, dict):
        return None
    params = conn.get("params")
    if not isinstance(params, dict):
        return None
    models = params.get("models")
    if not isinstance(models, list):
        return None
    target_id = llm_row.get("model")
    for entry in models:
        if not isinstance(entry, dict):
            continue
        if target_id and entry.get("id") != target_id:
            continue
        cc = entry.get("customConfig")
        if not isinstance(cc, dict):
            continue
        for key in ("model_id", "model"):
            value = cc.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def classify_llm(
    llm_row: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    connections_by_name: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a single project.list_llms() row against the LiteLLM lookup.

    Returns a dict with keys:
      status: one of {current, obsolete, ripoff, unknown, not_applicable}
      effectiveModel: the model string we actually classified (after CustomLLM unwrap)
      matchedKey: the lookup key we matched on, or None
      provider, family, currentModel, modelInputPrice, modelOutputPrice,
      currentInputPrice, currentOutputPrice (all None if unknown / not_applicable)
    """
    dss_type = llm_row.get("type") or ""
    raw_model = llm_row.get("model")
    deployment = llm_row.get("deployment")

    if dss_type in NOT_APPLICABLE_TYPES:
        return {
            "status": "not_applicable",
            "effectiveModel": None,
            "matchedKey": None,
            "provider": None,
            "family": None,
            "currentModel": None,
            "modelInputPrice": None,
            "modelOutputPrice": None,
            "currentInputPrice": None,
            "currentOutputPrice": None,
        }

    effective = raw_model or deployment
    if dss_type == "CUSTOM":
        unwrapped = _custom_unwrap(llm_row, connections_by_name)
        if unwrapped:
            effective = unwrapped

    candidates = normalize_for_audit(dss_type, effective, deployment if dss_type == "AZURE_OPENAI_DEPLOYMENT" else None)
    matched_key = None
    hit = None
    for c in candidates:
        if c in lookup:
            matched_key = c
            hit = lookup[c]
            break

    # Azure-only fuzzy fallback: opaque enterprise deployment names like
    # "USNPDGPT35" never match literal candidates. Apply after literal lookup
    # so explicit deployments still win. On a fuzzy hit, annotate the
    # effective model so the UI can show "(inferred from name)".
    if hit is None and dss_type == "AZURE_OPENAI_DEPLOYMENT":
        inferred = azure_fuzzy_infer(effective or deployment or "")
        if inferred and inferred in lookup:
            matched_key = inferred
            hit = lookup[inferred]
            effective = f"{inferred} (inferred from name)"

    if hit is None:
        return {
            "status": "unknown",
            "effectiveModel": effective,
            "matchedKey": None,
            "provider": None,
            "family": None,
            "currentModel": None,
            "modelInputPrice": None,
            "modelOutputPrice": None,
            "currentInputPrice": None,
            "currentOutputPrice": None,
        }

    return {
        "status": hit["status"],
        "effectiveModel": effective,
        "matchedKey": matched_key,
        "provider": hit.get("provider"),
        "family": hit.get("family"),
        "currentModel": hit.get("current_model"),
        "modelInputPrice": (hit.get("model_price") or {}).get("input_usd_per_1m_tokens"),
        "modelOutputPrice": (hit.get("model_price") or {}).get("output_usd_per_1m_tokens"),
        "currentInputPrice": (hit.get("current_price") or {}).get("input_usd_per_1m_tokens"),
        "currentOutputPrice": (hit.get("current_price") or {}).get("output_usd_per_1m_tokens"),
    }


def summarize_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {"current": 0, "obsolete": 0, "ripoff": 0, "unknown": 0, "not_applicable": 0}
    distinct: dict[str, set[str]] = {"obsolete": set(), "ripoff": set()}
    total = 0
    projects: set[str] = set()
    for r in rows:
        total += 1
        s = r.get("status") or "unknown"
        counts[s] = counts.get(s, 0) + 1
        if s in distinct:
            key = r.get("effectiveModel") or r.get("rawModel") or ""
            if key:
                distinct[s].add(key)
        pk = r.get("projectKey")
        if pk:
            projects.add(pk)
    return {
        "llmsTotal": total,
        "projectsScanned": len(projects),
        "countsByStatus": counts,
        "distinctModelsByStatus": {k: len(v) for k, v in distinct.items()},
    }
