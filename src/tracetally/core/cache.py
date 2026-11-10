# SPDX-License-Identifier: Apache-2.0
"""How an export's input count relates to its cache counters.

``included``
    ``input_tokens`` already contains cache-read tokens. OpenAI ``prompt_tokens``,
    Gemini ``promptTokenCount``, and OpenAI-compatible gateways work this way.
    The cost engine subtracts the cache read once. Cache writes, when present,
    are extra and are not subtracted.

``embedded``
    OTel GenAI and OpenLLMetry put cache reads and cache writes inside
    ``gen_ai.usage.input_tokens`` for every provider. The cost engine subtracts
    both once, then prices each part at its own rate.

``separate``
    Native Anthropic usage, including Claude on Bedrock and Vertex in JSONL
    and Langfuse, reports cache reads and cache writes separately from
    ``input_tokens``. The cost engine does not subtract.

``absent``
    The export had no cache-read and no cache-write field. Nothing is
    subtracted and no cache tokens are invented.

The flag depends on the source format and the provider. Readers do not
rewrite ``input_tokens``. The cost engine is the only place that subtracts.
"""

from __future__ import annotations

CacheAccounting = str

INCLUDED = "included"
EMBEDDED = "embedded"
SEPARATE = "separate"
ABSENT = "absent"
_KNOWN = frozenset({INCLUDED, EMBEDDED, SEPARATE, ABSENT})

# OpenAI-compatible gateways bill cached tokens the way OpenAI does: the
# prompt total already includes the cache read.
_INCLUDED_PROVIDERS = frozenset(
    {
        "openai",
        "azure",
        "azure_ai",
        "azure.ai.openai",
        "azure.ai.inference",
        "azure_openai",
        "azure-openai",
        "gemini",
        "google",
        "gcp.gemini",
        "google-gemini",
        "deepseek",
        "xai",
        "x_ai",
        "groq",
        "openrouter",
        "together_ai",
        "fireworks_ai",
        "perplexity",
    }
)


def cache_accounting(
    provider: str,
    model: str,
    *,
    has_cache: bool,
    explicit: str | None = None,
    source: str | None = None,
) -> str:
    """Pick the accounting flag for one call.

    An explicit flag wins. With no cache fields the result is ``absent``.
    OTel spans are ``embedded`` for every provider. JSONL and Langfuse keep
    Anthropic, including Bedrock and Vertex Claude, on ``separate``.
    """
    if explicit is not None:
        text = explicit.strip().casefold()
        if text not in _KNOWN:
            raise ValueError(
                "cache_accounting: cache_accounting must be included, embedded, separate, or absent"
            )
        return text
    if not has_cache:
        return ABSENT
    if (source or "").strip().casefold() == "otel":
        return EMBEDDED
    billed = provider.strip().casefold()
    name = model.strip().casefold()
    if _anthropic_tokens(billed, name):
        return SEPARATE
    if billed in _INCLUDED_PROVIDERS:
        return INCLUDED
    if (
        billed
        in {
            "vertex",
            "vertex_ai",
            "vertexai",
            "vertex-ai",
            "gcp.vertex_ai",
        }
        and "gemini" in name
    ):
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
