# SPDX-License-Identifier: Apache-2.0
"""Canonical records. Pydantic runs at the boundary, not inside hot loops.

Readers build these with ``model_construct`` on the success path and only run
full validation to name the field when a record is rejected.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracetally.core.money import Range


class Status(str, Enum):
    OK = "ok"
    ERROR = "error"


class SpanKind(str, Enum):
    LLM = "llm"
    TOOL = "tool"
    RETRIEVER = "retriever"
    AGENT = "agent"
    OTHER = "other"


class Capability(str, Enum):
    """What a reader can supply. Each check declares the capabilities it needs."""

    PER_CALL = "per_call"
    LATENCY = "latency"
    TRACE_TREE = "trace_tree"
    PROMPT_HASHES = "prompt_hashes"
    ERRORS = "errors"
    CACHED_TOKENS = "cached_tokens"
    TOOL_SPANS = "tool_spans"


class Effort(str, Enum):
    S = "S"
    M = "M"
    L = "L"


class Level(str, Enum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class LLMCall(BaseModel):
    """One LLM generation or chat-completion attempt."""

    model_config = ConfigDict(extra="ignore")

    source: str
    record_id: str
    trace_id: str | None = None
    parent_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    endpoint: str
    provider: str
    model: str
    operation: str | None = None
    start_time: datetime
    duration_ms: float | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)
    # included: input_tokens contains cache reads (OpenAI, Gemini).
    # separate: input_tokens excludes cache reads and cache writes (Anthropic).
    # absent: the export had no cache fields.
    cache_accounting: str = "absent"
    reasoning_tokens: int | None = Field(default=None, ge=0)
    request_count: int = Field(default=1, ge=1)
    status: Status
    http_status: int | None = None
    error_type: str | None = None
    attempt: int | None = Field(default=None, ge=0)
    is_batch: bool | None = None
    service_tier: str | None = None
    base_url: str | None = None
    tokens_known: bool = True
    interactive: bool | None = None
    prefix_hashes: tuple[tuple[int, str], ...] | None = None
    content_hash: str | None = None
    # Prompt text. Readers leave this empty unless the run asks to keep it.
    content: str | None = None
    tool_name: str | None = None
    args_hash: str | None = None
    reported_cost_usd: Decimal | None = None
    attrs: dict[str, str] = Field(default_factory=dict)


class Span(BaseModel):
    """A non-LLM span kept so later loop detection can see tools and agents."""

    model_config = ConfigDict(extra="ignore")

    source: str
    trace_id: str | None = None
    span_id: str
    parent_id: str | None = None
    name: str
    kind: SpanKind
    start_time: datetime
    duration_ms: float | None = None
    status: Status
    args_hash: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trace_ids: list[str] = Field(default_factory=list)
    count: int | None = None
    endpoint: str | None = None
    detail: str | None = None


class Finding(BaseModel):
    """One ranked check result. Dollar fields are a low/mid/high monthly range."""

    model_config = ConfigDict(extra="ignore")

    id: str
    check: str
    title: str
    severity: Level
    est_monthly_usd: Range
    effort: Effort
    quality_risk: Level
    confidence: Level
    evidence: list[Evidence] = Field(default_factory=list)
    recommendation: str
    caveats: list[str] = Field(default_factory=list)
    what_if: bool = False


class SkippedCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    check: str
    reason: str


class InputDigest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    sha256: str
    records: int
    skipped: int = 0
    spans: int = 0


class RunResult(BaseModel):
    """Serialised form of a full run. ``findings.json`` is this model.

    ``schema_version`` is a public contract. Summary JSON from ``tracetally
    summary --json`` stays a separate document (``summary_schema_version``).
    ``skipped_checks`` is the analyze run (capabilities, ``--only``, ``--skip``,
    and a missing tier map).     ``summary`` embeds that command's own document,
    including its capability-only skip list. ``records_dropped`` counts
    records removed because a request, span, or trace id was already seen
    in an earlier file.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    tracetally_version: str
    generated_at: str = ""
    redacted: bool = False
    include_content: bool = False
    show_footer: bool = True
    price_table_id: str
    price_table_retrieved_on: str
    price_source: str = ""
    price_url: str = ""
    price_sha256: str = ""
    priced_share: str = ""
    currency: str = "USD"
    fx_rate: str = "1"
    methodology: str = ""
    monthly_factor: str = "1"
    monthly_caveats: list[str] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    skipped_checks: list[SkippedCheck] = Field(default_factory=list)
    inputs: list[InputDigest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    records_dropped: int = 0
    content_excerpts: list[dict[str, str]] = Field(default_factory=list)
