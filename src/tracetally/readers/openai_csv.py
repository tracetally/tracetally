# SPDX-License-Identifier: Apache-2.0
"""OpenAI usage-dashboard CSV, in cost-only mode.

OpenAI documents the export as CSV (activity or cost) and documents the Usage
and Costs API fields. It does not publish one header row for the dashboard
file. This reader accepts those API names and a few aliases
(``n_context_tokens_total``, ``n_generated_tokens_total``, ``date``,
``amount``, ``Cached Input Tokens``, ``Cost ($)``). A column it does not know
is ignored.

Rows are aggregated: ``request_count`` is the request total, not 1. There is
no latency, trace tree, prompt, or error status, so the reader declares none
of those capabilities and checks that need them are skipped.

Audio tokens are not folded into the text counts. ``amount_cents`` is rejected
rather than converted. Cached tokens are treated as included in
``input_tokens``, which is how the Usage API defines that field.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from tracetally.config import ReaderConfig
from tracetally.core.schema import Capability, LLMCall, Span, Status
from tracetally.readers.base import BaseReader
from tracetally.readers.csvutil import normalize_key, open_csv, split_header
from tracetally.readers.mapping import first, split_field_error
from tracetally.readers.parse import parse_bool, parse_decimal, parse_time, parse_whole_int

_COLUMN_MAP = {
    "start_time": "start_time",
    "start_time_iso": "start_time",
    "date": "start_time",
    "day": "start_time",
    "timestamp": "start_time",
    "bucket_start_time": "start_time",
    "model": "model",
    "model_id": "model",
    "snapshot_id": "model",
    "line_item": "line_item",
    "project_id": "project_id",
    "project": "project",
    "project_name": "project_name",
    "user_id": "user_id",
    "num_model_requests": "request_count",
    "n_requests": "request_count",
    "requests": "request_count",
    "request_count": "request_count",
    "num_requests": "request_count",
    "input_tokens": "input_tokens",
    "n_context_tokens_total": "input_tokens",
    "prompt_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "n_generated_tokens_total": "output_tokens",
    "completion_tokens": "output_tokens",
    "input_cached_tokens": "cached_input_tokens",
    "cached_input_tokens": "cached_input_tokens",
    "cached_tokens": "cached_input_tokens",
    "n_cached_context_tokens": "cached_input_tokens",
    "input_uncached_tokens": "input_uncached_tokens",
    "output_reasoning_tokens": "reasoning_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "input_audio_tokens": "input_audio_tokens",
    "output_audio_tokens": "output_audio_tokens",
    "batch": "batch",
    "is_batch": "batch",
    "service_tier": "service_tier",
    "amount_value": "cost",
    "amount": "cost",
    "cost": "cost",
    "cost_usd": "cost",
    "costs": "cost",
    "amount_currency": "currency",
    "currency": "currency",
    "amount_cents": "amount_cents",
    "api_key_id": "api_key_id",
    "organization_id": "organization_id",
    "end_time": "end_time",
}
_USAGE_MARKERS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "num_model_requests",
        "n_context_tokens_total",
        "n_generated_tokens_total",
        "amount_value",
        "amount",
        "cost",
        "cost_usd",
        "costs",
        "amount_cents",
    }
)


class OpenAICsvReader(BaseReader):
    """Aggregated usage rows. Capabilities are intentionally empty: cost-only."""

    name: ClassVar[str] = "openai_csv"
    capabilities: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(self) -> None:
        super().__init__()
        self._loose_time = False
        self._missing_requests = False
        self._tokens_blank = False
        self._audio = False
        self._cache_adjusted = False
        self._reasoning_gap = False

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        text = head.decode("utf-8", errors="ignore").lstrip("\ufeff").lstrip()
        if not text or text[0] in "{[":
            return 0.0
        header = text.splitlines()[0]
        try:
            _, cells = split_header(header)
        except csv.Error:
            return 0.0
        keys = {normalize_key(cell) for cell in cells if cell.strip()}
        if {"trace_id", "parent_observation_id"} & keys:
            return 0.0
        has_model = bool({"model", "line_item", "snapshot_id", "model_id"} & keys)
        if has_model and keys & _USAGE_MARKERS:
            return 0.95
        return 0.0

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        where = str(path)
        try:
            with open_csv(path) as opened:
                headers, rows = opened
                mapped, aliases, extra = _classify_headers(headers)
                if aliases:
                    shown = ", ".join(aliases[:8])
                    self.warn(f"column aliases accepted: {shown}")
                if extra:
                    shown = ", ".join(extra[:8])
                    self.warn(f"ignored extra columns: {shown}")
                if "start_time" not in mapped.values() or not (
                    {"model", "line_item"} & set(mapped.values())
                ):
                    self.fail(
                        "header is not an OpenAI usage or cost CSV "
                        "(need a model or line item, and a start time)",
                        field=None,
                        where=where,
                        cfg=cfg,
                    )
                    return
                for number, raw in rows:
                    loc = f"{path}:row {number}"
                    canonical = _canonicalize(raw, mapped)
                    try:
                        yield self._call(canonical, where=loc, cfg=cfg)
                    except ValueError as exc:
                        field, message = split_field_error(exc)
                        self.fail(message, field=field, where=loc, cfg=cfg)
        except UnicodeDecodeError:
            self.fail("file is not valid UTF-8", field=None, where=where, cfg=cfg)

    def _call(self, row: dict[str, Any], *, where: str, cfg: ReaderConfig) -> LLMCall:
        if first(row, "amount_cents") is not None and first(row, "cost") is None:
            raise ValueError(
                "amount_cents: amount_cents is not a documented dollar column; "
                "export cost in USD instead"
            )
        currency = first(row, "currency")
        if currency is not None and str(currency).strip().lower() not in {"usd", "us$", "$"}:
            raise ValueError(f"currency: currency {currency!r} is not USD")
        model = _model(row)
        if not model:
            raise ValueError("model: model is required")
        start = self._time(first(row, "start_time"))
        input_tokens, output_tokens, cached, reasoning, tokens_known = self._tokens(row)
        reported = parse_decimal(first(row, "cost"), field="reported_cost_usd")
        if input_tokens == 0 and output_tokens == 0 and reported is None and cached is None:
            raise ValueError("input_tokens: row has neither token counts nor a cost")
        blank_cost = input_tokens == 0 and output_tokens == 0 and reported is not None
        if blank_cost and not self._tokens_blank:
            self._tokens_blank = True
            self.warn(
                "a cost row had no token counts; token counts were left unknown "
                "and were not estimated"
            )
        requests = first(row, "request_count")
        if requests is None:
            request_count = 1
            if not self._missing_requests:
                self._missing_requests = True
                self.warn("request_count was missing; recorded as 1")
        else:
            request_count = parse_whole_int(requests, field="request_count", minimum=1)
        audio_in = _optional_token(first(row, "input_audio_tokens"), field="input_audio_tokens")
        audio_out = _optional_token(first(row, "output_audio_tokens"), field="output_audio_tokens")
        if (audio_in or audio_out) and not self._audio:
            self._audio = True
            self.warn(
                "audio tokens are present and were not priced; "
                "only the text token columns were used"
            )
        batch = parse_bool(first(row, "batch"))
        tier = str(first(row, "service_tier") or "").strip().lower()
        if batch is None and tier in {"batch", "batched"}:
            batch = True
        return LLMCall.model_construct(
            source="openai_csv",
            record_id=where,
            trace_id=None,
            parent_id=None,
            session_id=None,
            user_id=_text(first(row, "user_id")),
            endpoint=_endpoint(row, cfg),
            provider="openai",
            model=model,
            operation=None,
            start_time=start,
            duration_ms=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
            request_count=request_count,
            status=Status.OK,
            http_status=None,
            error_type=None,
            attempt=None,
            is_batch=batch,
            service_tier=tier or None,
            base_url=None,
            tokens_known=tokens_known,
            interactive=None,
            prefix_hashes=None,
            content_hash=None,
            tool_name=None,
            args_hash=None,
            reported_cost_usd=reported,
            attrs=_attrs(row),
        )

    def _tokens(self, row: dict[str, Any]) -> tuple[int, int, int | None, int | None, bool]:
        incoming = _optional_token(first(row, "input_tokens"), field="input_tokens")
        outgoing = _optional_token(first(row, "output_tokens"), field="output_tokens")
        saw_input = incoming is not None
        saw_output = outgoing is not None
        cached = _optional_token(first(row, "cached_input_tokens"), field="cached_input_tokens")
        uncached = _optional_token(
            first(row, "input_uncached_tokens"),
            field="input_uncached_tokens",
        )
        reasoning = _optional_token(first(row, "reasoning_tokens"), field="reasoning_tokens")
        if incoming is None and uncached is not None:
            incoming = uncached + (cached or 0)
            saw_input = True
            if not self._cache_adjusted:
                self._cache_adjusted = True
                self.warn("input_tokens was missing; it was built from uncached plus cached tokens")
        if incoming is None:
            incoming = 0
        if outgoing is None:
            outgoing = 0
        if cached is not None and cached > incoming:
            incoming = incoming + cached
            if not self._cache_adjusted:
                self._cache_adjusted = True
                self.warn(
                    "cached tokens exceeded input tokens; input was treated as the "
                    "uncached portion and the cached count was added"
                )
        if reasoning is not None and reasoning > outgoing and not self._reasoning_gap:
            self._reasoning_gap = True
            self.warn(
                "reasoning tokens exceed output tokens; they were kept separate and "
                "were not added on top of output tokens"
            )
        return incoming, outgoing, cached, reasoning, saw_input and saw_output

    def _time(self, value: object) -> datetime:
        loose = (
            isinstance(value, str)
            and bool(value.strip())
            and "T" not in value
            and not value.strip().isdigit()
        )
        parsed = parse_time(value, field="start_time")
        if loose and not self._loose_time:
            self._loose_time = True
            self.warn(
                "a non-ISO timestamp was accepted; naive values are read as UTC. "
                "Prefer YYYY-MM-DDTHH:MM:SSZ"
            )
        return parsed


def _classify_headers(headers: list[str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Map raw header → canonical field, plus alias and extra names."""
    mapped: dict[str, str] = {}
    aliases: list[str] = []
    extra: list[str] = []
    seen: set[str] = set()
    for header in headers:
        stripped = header.strip()
        if not stripped:
            continue
        key = normalize_key(stripped)
        canonical = _COLUMN_MAP.get(key)
        if canonical is None:
            extra.append(stripped)
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        mapped[stripped] = canonical
        if stripped != canonical:
            aliases.append(f"{stripped} -> {canonical}")
    return mapped, aliases, extra


def _canonicalize(raw: dict[str, str], mapped: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for header, canonical in mapped.items():
        if header in raw and raw[header] != "":
            row[canonical] = raw[header]
    return row


def _model(row: dict[str, Any]) -> str | None:
    direct = _text(first(row, "model"))
    if direct:
        return direct
    line = _text(first(row, "line_item"))
    if not line:
        return None
    head = line.split(",", 1)[0].strip()
    if not head or " " in head:
        return None
    return head


def _endpoint(row: dict[str, Any], cfg: ReaderConfig) -> str:
    if cfg.endpoint_attr:
        key = normalize_key(cfg.endpoint_attr)
        chosen = first(row, key)
        if chosen is not None and str(chosen).strip():
            return str(chosen).strip()
    for key in ("project_name", "project", "project_id"):
        chosen = first(row, key)
        if chosen is not None and str(chosen).strip():
            return str(chosen).strip()
    return "(usage)"


def _attrs(row: dict[str, Any]) -> dict[str, str]:
    project = first(row, "project_id", "project", "project_name")
    if project is None:
        return {}
    return {"project": str(project)}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_token(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return parse_whole_int(value, field=field)
