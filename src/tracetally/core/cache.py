# SPDX-License-Identifier: Apache-2.0
"""How an export's input count relates to its cache counters.

``included``
    ``input_tokens`` already contains cache-read tokens. OpenAI ``prompt_tokens``
    and Gemini ``promptTokenCount`` work this way. The cost engine subtracts
    the cache read once and prices that portion at the cache-read rate.

``separate``
    ``input_tokens`` excludes cache reads and cache writes. Anthropic usage
    works this way, including Claude on Bedrock and Vertex. The cost engine
    does not subtract. Cache reads use the cache-read rate. Cache writes use
    ``cache_creation_input_token_cost`` when that rate is published.

``absent``
    The export had no cache-read and no cache-write field. Nothing is
    subtracted and no cache tokens are invented.

Readers set this flag. They do not rewrite ``input_tokens``. The cost engine
is the only place that subtracts, and only for ``included``.
"""

from __future__ import annotations

CacheAccounting = str

INCLUDED = "included"
SEPARATE = "separate"
ABSENT = "absent"
_KNOWN = frozenset({INCLUDED, SEPARATE, ABSENT})

_INCLUDED_PROVIDERS = frozenset(
    {
        "openai",
        "azure",
        "azure_ai",
        "azure.ai.openai",
        "azure_openai",
        "azure-openai",
        "gemini",
        "google",
        "gcp.gemini",
        "google-gemini",
    }
)


def cache_accounting(
    provider: str,
    model: str,
    *,
    has_cache: bool,
    explicit: str | None = None,
) -> str:
    """Pick the accounting flag for one call.

    An explicit ``included`` / ``separate`` / ``absent`` value wins. With no
    cache fields the result is ``absent``. A provider that is neither
    OpenAI-style nor Anthropic-style stays ``separate`` so a cache counter is
    never subtracted twice.
    """
    if explicit is not None:
        text = explicit.strip().casefold()
        if text not in _KNOWN:
            raise ValueError(
                "cache_accounting: cache_accounting must be included, separate, or absent"
            )
        return text
    if not has_cache:
        return ABSENT
    billed = provider.strip().casefold()
    name = model.strip().casefold()
    if _anthropic_tokens(billed, name):
        return SEPARATE
    if billed in _INCLUDED_PROVIDERS:
        return INCLUDED
    if billed in {"vertex", "vertex_ai", "vertexai", "vertex-ai"} and "gemini" in name:
        return INCLUDED
    return SEPARATE


def _anthropic_tokens(provider: str, model: str) -> bool:
    if provider in {"anthropic", "claude"} or model.startswith("claude"):
        return True
    hosted = provider in {
        "bedrock",
        "aws",
        "amazon",
        "aws.bedrock",
        "vertex",
        "vertex_ai",
        "vertexai",
        "vertex-ai",
    }
    return hosted and ("anthropic" in model or "claude" in model)
