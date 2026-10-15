# SPDX-License-Identifier: Apache-2.0
"""Columnar in-memory store.

Numeric columns use ``array`` so a long trace file stays compact. Group-by
indexes used by the later checks are not built yet. Readers stream into
``append_call`` and do not retain the raw file.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tracetally.core.schema import Capability, LLMCall, Span, Status

_MISSING_HTTP = -1


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
    endpoint: list[str] = field(default_factory=list)
    provider: list[str] = field(default_factory=list)
    model: list[str] = field(default_factory=list)
    start_time: list[datetime] = field(default_factory=list)
    duration_ms: array[float] = field(default_factory=lambda: array("d"))
    input_tokens: array[int] = field(default_factory=lambda: array("q"))
    output_tokens: array[int] = field(default_factory=lambda: array("q"))
    cached_input_tokens: array[int] = field(default_factory=lambda: array("q"))
    reasoning_tokens: array[int] = field(default_factory=lambda: array("q"))
    request_count: array[int] = field(default_factory=lambda: array("q"))
    status: list[str] = field(default_factory=list)
    http_status: array[int] = field(default_factory=lambda: array("i"))
    error_type: list[str | None] = field(default_factory=list)
    is_batch: array[int] = field(default_factory=lambda: array("b"))
    reported_cost_usd: list[Decimal | None] = field(default_factory=list)
    content_hash: list[str | None] = field(default_factory=list)
    prefix_hashes: list[tuple[tuple[int, str], ...]] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inputs: list[InputStat] = field(default_factory=list)
    capabilities: frozenset[Capability] | None = None

    def __len__(self) -> int:
        return len(self.source)

    def append_call(self, call: LLMCall) -> None:
        self.source.append(call.source)
        self.record_id.append(call.record_id)
        self.trace_id.append(call.trace_id)
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
        self.reasoning_tokens.append(call.reasoning_tokens or 0)
        self.request_count.append(call.request_count)
        self.status.append(
            call.status.value if isinstance(call.status, Status) else str(call.status)
        )
        self.http_status.append(
            _MISSING_HTTP if call.http_status is None else int(call.http_status)
        )
        self.error_type.append(call.error_type)
        self.is_batch.append(1 if call.is_batch else 0)
        self.reported_cost_usd.append(call.reported_cost_usd)
        self.content_hash.append(call.content_hash)
        self.prefix_hashes.append(call.prefix_hashes or ())

    def append_span(self, span: Span) -> None:
        self.spans.append(span)

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
