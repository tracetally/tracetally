# SPDX-License-Identifier: Apache-2.0
"""Deterministic model-id matching. No network and no fuzzy scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from tracetally.pricing.catalog import Catalog, CatalogEntry

_CANON = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "gcp.gemini": "gemini",
    "google-gemini": "gemini",
    "vertex": "vertex_ai",
    "vertexai": "vertex_ai",
    "vertex_ai": "vertex_ai",
    "vertex-ai": "vertex_ai",
    "gcp.vertex_ai": "vertex_ai",
    "bedrock": "bedrock",
    "aws": "bedrock",
    "amazon": "bedrock",
    "aws.bedrock": "bedrock",
    "azure": "azure",
    "azure.ai.openai": "azure",
    "azure.ai.inference": "azure",
    "azure_openai": "azure",
    "azure-openai": "azure",
    "xai": "xai",
    "x_ai": "xai",
    "ollama": "ollama",
    "vllm": "vllm",
    "tgi": "tgi",
    "text-generation-inference": "tgi",
    "nim": "nim",
    "huggingface": "huggingface",
    "hf": "huggingface",
}

_COMPATIBLE: dict[str, frozenset[str]] = {
    "openai": frozenset({"openai", "text-completion-openai"}),
    "azure": frozenset({"azure", "azure_ai"}),
    "anthropic": frozenset({"anthropic"}),
    "bedrock": frozenset({"bedrock", "bedrock_converse", "bedrock_mantle"}),
    "vertex_ai": frozenset(
        {
            "vertex_ai",
            "vertex_ai-language-models",
            "vertex_ai-anthropic_models",
            "vertex_ai-mistral_models",
            "vertex_ai-llama_models",
            "vertex_ai-meta_models",
            "vertex_ai-ai21_models",
            "vertex_ai-image-models",
            "vertex_ai-code-models",
        }
    ),
    "gemini": frozenset({"gemini", "google"}),
}

# Left-hand side of a ``provider/model`` id. Anything else with one slash is
# treated as a Hugging Face ``org/model`` name.
_KNOWN_PREFIXES = frozenset(
    set(_CANON)
    | set(_COMPATIBLE)
    | {
        "groq",
        "mistral",
        "cohere",
        "fireworks_ai",
        "together_ai",
        "deepseek",
        "xai",
        "openrouter",
        "perplexity",
        "ai21",
        "cloudflare",
        "databricks",
        "sagemaker",
        "watsonx",
        "replicate",
        "deepinfra",
        "friendliai",
        "anyscale",
        "github_copilot",
        "azure_ai",
        "bedrock_converse",
        "bedrock_mantle",
        "text-completion-openai",
        "vertex_ai-language-models",
    }
)

_SELF_HOSTED_PROVIDERS = frozenset(
    {
        "ollama",
        "vllm",
        "tgi",
        "text-generation-inference",
        "nim",
        "nvidia-nim",
        "huggingface",
        "hf",
        "local",
        "localhost",
        "llamacpp",
        "llama.cpp",
    }
)
_SELF_HOSTED_PREFIXES = (
    "ollama/",
    "vllm/",
    "tgi/",
    "nim/",
    "huggingface/",
    "hf/",
)
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
_DATE_SUFFIXES = (
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),
    re.compile(r"-\d{8}$"),
    re.compile(r"@\d{4}.*$"),
)
_REGION = re.compile(
    r"^(?:us-gov|us-east-1|us-west-2|eu-west-1|eu-central-1|global|apac|us|eu|au|jp|ca|uk)\."
)
_FT = re.compile(r"^ft:([^:]+)")


@dataclass(frozen=True)
class Match:
    entry: CatalogEntry
    step: str
    label: str


def canon(provider: str) -> str:
    key = provider.strip().casefold()
    return _CANON.get(key, key)


def cost_unknown_reason(provider: str, model: str, base_url: str | None) -> str | None:
    """Why this call must not be given a list price. ``None`` when it might."""
    if _loopback(base_url):
        return "base URL is loopback; cost unknown"
    billed = canon(provider)
    if billed in _SELF_HOSTED_PROVIDERS:
        return "self-hosted provider; cost unknown"
    name = model.strip()
    lowered = name.casefold()
    for prefix in _SELF_HOSTED_PREFIXES:
        if lowered.startswith(prefix):
            return "self-hosted model; cost unknown"
    if "/" in name and " " not in name:
        left, right = name.split("/", 1)
        if right and left.casefold() not in _KNOWN_PREFIXES:
            return "huggingface org/model name; cost unknown"
    return None


def resolve_match(
    catalog: Catalog,
    provider: str,
    model: str,
    aliases: dict[str, str],
) -> Match | None:
    """Match ``model`` for the provider that billed.

    Order: exact, provider prefix, LiteLLM ``alias_of``, date/snapshot suffix,
    Bedrock region prefix, user aliases, then an OpenAI ``ft:`` base.
    """
    billed = canon(provider)
    name = model.strip()
    if not name:
        return None
    if billed in {"", "unknown"} and "/" not in name and not name.startswith("ft:"):
        found = _via_alias(catalog, billed, name, aliases)
        return found
    found = _steps(catalog, billed, name)
    if found is not None:
        return found
    stripped = _strip_date(name)
    if stripped != name:
        found = _steps(catalog, billed, stripped)
        if found is not None:
            return Match(found.entry, "date_suffix", "approx.")
    if billed == "bedrock":
        regional = _strip_region(name)
        if regional != name:
            found = _steps(catalog, billed, regional)
            if found is None:
                regional_dated = _strip_date(regional)
                if regional_dated != regional:
                    found = _steps(catalog, billed, regional_dated)
            if found is not None:
                return Match(found.entry, "region_prefix", "approx.")
    aliased = _via_alias(catalog, billed, name, aliases)
    if aliased is not None:
        return aliased
    return _fine_tune(catalog, billed, name)


def _via_alias(catalog: Catalog, billed: str, name: str, aliases: dict[str, str]) -> Match | None:
    target = aliases.get(name)
    if target is None:
        target = aliases.get(f"{billed}/{name}")
    if target is None or target == name:
        return None
    next_provider, next_model = _split_alias(target, billed)
    found = resolve_match(catalog, next_provider, next_model, {})
    if found is None:
        return None
    return Match(found.entry, "user_alias", found.label)


def _fine_tune(catalog: Catalog, billed: str, name: str) -> Match | None:
    if not name.startswith("ft:"):
        return None
    matched = _FT.match(name)
    if matched is None:
        return None
    base = matched.group(1)
    listed = f"ft:{base}"
    if name != listed:
        found = _steps(catalog, "openai", listed)
        if found is not None and not found.entry.zero_price:
            return Match(found.entry, "fine_tune", "")
    found = resolve_match(catalog, "openai" if billed in {"", "unknown"} else billed, base, {})
    if found is None and billed != "openai":
        found = resolve_match(catalog, "openai", base, {})
    if found is None:
        return None
    return Match(found.entry, "fine_tune", "estimate")


def _steps(catalog: Catalog, billed: str, name: str) -> Match | None:
    exact = catalog.get(name)
    if exact is not None and exact.has_rates and _compatible(billed, exact):
        return Match(exact, "exact", "")
    prefixed = catalog.get(f"{billed}/{name}")
    if prefixed is not None and prefixed.has_rates:
        return Match(prefixed, "provider_prefix", "")
    for candidate in (exact, prefixed):
        if candidate is None or not candidate.alias_of:
            continue
        target = _follow(catalog, candidate.alias_of, billed, depth=0)
        if target is not None:
            return Match(target, "alias_of", "")
    if "/" in name:
        left, right = name.split("/", 1)
        if right and canon(left) == billed and left.casefold() in _KNOWN_PREFIXES:
            return _steps(catalog, billed, right)
    return None


def _follow(catalog: Catalog, key: str, billed: str, *, depth: int) -> CatalogEntry | None:
    if depth > 5 or not key:
        return None
    entry = catalog.get(key)
    if entry is None:
        return None
    if entry.has_rates and _compatible(billed, entry):
        return entry
    if entry.alias_of:
        return _follow(catalog, entry.alias_of, billed, depth=depth + 1)
    return None


def _compatible(billed: str, entry: CatalogEntry) -> bool:
    if not entry.provider:
        return True
    if billed in {"", "unknown"}:
        return True
    allowed = _COMPATIBLE.get(billed)
    if allowed is None:
        return entry.provider == billed or entry.key.startswith(f"{billed}/")
    if entry.provider in allowed:
        return True
    return entry.key.startswith(f"{billed}/")


def _split_alias(target: str, billed: str) -> tuple[str, str]:
    if "/" in target:
        left, right = target.split("/", 1)
        if right and left.casefold() in _KNOWN_PREFIXES:
            return canon(left), right
    return billed, target


def _strip_date(name: str) -> str:
    for pattern in _DATE_SUFFIXES:
        matched = pattern.search(name)
        if matched and matched.start() > 0:
            return name[: matched.start()]
    return name


def _strip_region(name: str) -> str:
    matched = _REGION.match(name)
    if matched is None:
        return name
    return name[matched.end() :]


def _loopback(base_url: str | None) -> bool:
    if base_url is None:
        return False
    text = base_url.strip()
    if not text:
        return False
    candidate = text if "://" in text else f"http://{text}"
    host = urlparse(candidate).hostname
    if host is None:
        return False
    return host.casefold().strip("[]") in _LOOPBACK
