# SPDX-License-Identifier: Apache-2.0
"""Columnar in-memory store.

Numeric columns use ``array`` so a long trace file stays compact. Readers
stream into ``append_call`` and do not retain the raw file. ``indexes()``
groups calls once; appending invalidates that cache.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tracetally.core.schema import Capability, LLMCall, Span, Status

_MISSING_HTTP = -1
_MISSING_ATTEMPT = -1
_INTERACTIVE_UNKNOWN = 0
_INTERACTIVE_TRUE = 1
_INTERACTIVE_FALSE = 2


@dataclass
class StoreIndexes:
    by_endpoint: dict[str, list[int]]
    by_trace: dict[str, list[int]]
    spans_by_trace: dict[str, list[int]]


@dataclass
class InputStat:
    path: str
    sha256: str
    records: int = 0
    spans: int = 0
    skipped: int = 0


@dataclass
class TraceStore:
    source: list[str] = field(default_factory=list)
    record_id: list[str] = field(default_factory=list)
    trace_id: list[str | None] = field(default_factory=list)
    parent_id: list[str | None] = field(default_factory=list)
    session_id: list[str | None] = field(default_factory=list)
    user_id: list[str | None] = field(default_factory=list)
    endpoint: list[str] = field(default_factory=list)
    provider: list[str] = field(default_factory=list)
    model: list[str] = field(default_factory=list)
    start_time: list[datetime] = field(default_factory=list)
    duration_ms: array[float] = field(default_factory=lambda: array("d"))
    input_tokens: array[int] = field(default_factory=lambda: array("q"))
    output_tokens: array[int] = field(default_factory=lambda: array("q"))
    cached_input_tokens: array[int] = field(default_factory=lambda: array("q"))
    cache_creation_tokens: array[int] = field(default_factory=lambda: array("q"))
    cache_accounting: list[str] = field(default_factory=list)
    reasoning_tokens: array[int] = field(default_factory=lambda: array("q"))
    request_count: array[int] = field(default_factory=lambda: array("q"))
    status: list[str] = field(default_factory=list)
    http_status: array[int] = field(default_factory=lambda: array("i"))
    error_type: list[str | None] = field(default_factory=list)
    attempt: array[int] = field(default_factory=lambda: array("i"))
    is_batch: array[int] = field(default_factory=lambda: array("b"))
    interactive: array[int] = field(default_factory=lambda: array("b"))
    service_tier: list[str | None] = field(default_factory=list)
    base_url: list[str | None] = field(default_factory=list)
    tokens_known: list[bool] = field(default_factory=list)
    reported_cost_usd: list[Decimal | None] = field(default_factory=list)
    content_hash: list[str | None] = field(default_factory=list)
    content: list[str | None] = field(default_factory=list)
    prefix_hashes: list[tuple[tuple[int, str], ...]] = field(default_factory=list)
    tool_name: list[str | None] = field(default_factory=list)
    args_hash: list[str | None] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inputs: list[InputStat] = field(default_factory=list)
    capabilities: frozenset[Capability] | None = None
    readers: set[str] = field(default_factory=set)
    _indexes: StoreIndexes | None = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.source)

    def append_call(self, call: LLMCall) -> None:
        self.source.append(call.source)
        self.record_id.append(call.record_id)
        self.trace_id.append(call.trace_id)
        self.parent_id.append(call.parent_id)
        self.session_id.append(call.session_id)
        self.user_id.append(call.user_id)
        self.endpoint.append(call.endpoint)
        self.provider.append(call.provider)
        self.model.append(call.model)
        self.start_time.append(call.start_time)
        if call.duration_ms is None:
            self.duration_ms.append(float("nan"))
        else:
            self.duration_ms.append(float(call.duration_ms))
        self.input_tokens.append(call.input_tokens)
        self.output_tokens.append(call.output_tokens)
        self.cached_input_tokens.append(call.cached_input_tokens or 0)
        self.cache_creation_tokens.append(call.cache_creation_tokens or 0)
        self.cache_accounting.append(call.cache_accounting or "absent")
        self.reasoning_tokens.append(call.reasoning_tokens or 0)
        self.request_count.append(call.request_count)
        self.status.append(
            call.status.value if isinstance(call.status, Status) else str(call.status)
        )
        self.http_status.append(
            _MISSING_HTTP if call.http_status is None else int(call.http_status)
        )
        self.error_type.append(call.error_type)
        self.attempt.append(_MISSING_ATTEMPT if call.attempt is None else int(call.attempt))
        self.is_batch.append(1 if call.is_batch else 0)
        self.interactive.append(_interactive_code(call.interactive))
        self.service_tier.append(call.service_tier)
        self.base_url.append(call.base_url)
        self.tokens_known.append(bool(call.tokens_known))
        self.reported_cost_usd.append(call.reported_cost_usd)
        self.content_hash.append(call.content_hash)
        self.content.append(call.content)
        self.prefix_hashes.append(call.prefix_hashes or ())
        self.tool_name.append(call.tool_name)
        self.args_hash.append(call.args_hash)
        self._indexes = None

    def append_span(self, span: Span) -> None:
        self.spans.append(span)
        self._indexes = None

    def indexes(self) -> StoreIndexes:
        cached = self._indexes
        if cached is not None:
            return cached
        by_endpoint: dict[str, list[int]] = {}
        by_trace: dict[str, list[int]] = {}
        for index, endpoint in enumerate(self.endpoint):
            by_endpoint.setdefault(endpoint, []).append(index)
            trace_id = self.trace_id[index]
            if trace_id:
                by_trace.setdefault(trace_id, []).append(index)
        spans_by_trace: dict[str, list[int]] = {}
        for index, span in enumerate(self.spans):
            if span.trace_id:
                spans_by_trace.setdefault(span.trace_id, []).append(index)
        cached = StoreIndexes(
            by_endpoint=by_endpoint,
            by_trace=by_trace,
            spans_by_trace=spans_by_trace,
        )
        self._indexes = cached
        return cached

    def attempt_at(self, index: int) -> int | None:
        value = int(self.attempt[index])
        if value == _MISSING_ATTEMPT:
            return None
        return value

    def interactive_at(self, index: int) -> bool | None:
        value = int(self.interactive[index])
        if value == _INTERACTIVE_TRUE:
            return True
        if value == _INTERACTIVE_FALSE:
            return False
        return None

    def duration_at(self, index: int) -> float | None:
        value = float(self.duration_ms[index])
        if math.isnan(value):
            return None
        return value

    def http_status_at(self, index: int) -> int | None:
        value = int(self.http_status[index])
        if value == _MISSING_HTTP:
            return None
        return value

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def note_capabilities(self, caps: frozenset[Capability]) -> None:
        if self.capabilities is None:
            self.capabilities = caps
        else:
            self.capabilities = self.capabilities & caps

    def note_reader(self, name: str) -> None:
        self.readers.add(name)


def _interactive_code(value: bool | None) -> int:
    if value is True:
        return _INTERACTIVE_TRUE
    if value is False:
        return _INTERACTIVE_FALSE
    return _INTERACTIVE_UNKNOWN
