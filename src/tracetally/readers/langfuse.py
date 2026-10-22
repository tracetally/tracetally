# SPDX-License-Identifier: Apache-2.0
"""Langfuse UI export reader (JSON and CSV, traces and observations).

The public export docs name CSV and JSON and say every column is included.
They do not publish one schema. This reader accepts the observation and trace
columns from the Langfuse table definitions, the public observation payload
(``usage`` / ``usageDetails`` / ``costDetails``), and the blob-export names
(``trace_id``, ``usage_details``, ``provided_model_name``). Display-name
headers (``Trace ID``, ``Latency (s)``) are aliases.

Real exports were not available, so unknown columns are ignored, missing
optional fields are left empty, and a bad record is skipped unless ``--strict``.
Prompt text is hashed and dropped.

``GENERATION`` and ``EMBEDDING`` become ``LLMCall``. Other observation types
become ``Span``. A trace is expanded to its observations. A trace with no
observations and no model is skipped, because a model is not invented.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from tracetally.config import ReaderConfig
from tracetally.core.hashing import content_hash, hash_content_parts
from tracetally.core.schema import Capability, LLMCall, Span, SpanKind, Status
from tracetally.readers.base import BaseReader
from tracetally.readers.csvutil import normalize_key, open_csv, split_header
from tracetally.readers.jsonutil import loads
from tracetally.readers.mapping import (
    as_object,
    first,
    index_fields,
    infer_provider,
    split_field_error,
    status_bits,
)
from tracetally.readers.parse import (
    parse_bool,
    parse_decimal,
    parse_time,
    parse_whole_int,
    text_fragments,
)

_LARGE_DOCUMENT_BYTES = 500 * 1024 * 1024

_GENERATION_TYPES = frozenset({"GENERATION", "EMBEDDING"})
_SPAN_KINDS = {
    "SPAN": SpanKind.OTHER,
    "EVENT": SpanKind.OTHER,
    "AGENT": SpanKind.AGENT,
    "TOOL": SpanKind.TOOL,
    "CHAIN": SpanKind.AGENT,
    "RETRIEVER": SpanKind.RETRIEVER,
    "EVALUATOR": SpanKind.OTHER,
    "GUARDRAIL": SpanKind.OTHER,
}
_INPUT_KEYS = ("input", "prompt", "input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output", "completion", "output_tokens", "completion_tokens")
_CACHED_KEYS = (
    "input_cache_read",
    "cache_read_input_tokens",
    "cached_tokens",
    "input_cached_tokens",
    "cache_read",
)
_REASONING_KEYS = (
    "output_reasoning",
    "reasoning_tokens",
    "reasoning",
    "output_reasoning_tokens",
)
_PREFERRED_HEADERS = frozenset(
    {
        "id",
        "traceId",
        "trace_id",
        "type",
        "name",
        "startTime",
        "start_time",
        "endTime",
        "end_time",
        "model",
        "provided_model_name",
        "providedModelName",
        "input",
        "output",
        "usage",
        "usageDetails",
        "usage_details",
        "costDetails",
        "cost_details",
        "metadata",
        "level",
        "statusMessage",
        "status_message",
        "parentObservationId",
        "parent_observation_id",
        "totalCost",
        "total_cost",
        "calculatedTotalCost",
        "calculated_total_cost",
        "latency",
        "userId",
        "user_id",
        "sessionId",
        "session_id",
        "inputTokens",
        "input_tokens",
        "outputTokens",
        "output_tokens",
        "totalTokens",
        "total_tokens",
        "environment",
        "traceName",
        "trace_name",
        "projectId",
        "project_id",
        "timestamp",
        "tags",
        "version",
        "release",
    }
)


class LangfuseReader(BaseReader):
    name: ClassVar[str] = "langfuse"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.PER_CALL,
            Capability.LATENCY,
            Capability.TRACE_TREE,
            Capability.PROMPT_HASHES,
            Capability.ERRORS,
            Capability.CACHED_TOKENS,
            Capability.TOOL_SPANS,
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self._loose_time = False
        self._cache_adjusted = False
        self._derived_tokens = False
        self._reasoning_gap = False
        self._latency_seconds = False

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        if b"resourceSpans" in head or b"resource_spans" in head:
            return 0.0
        text = head.decode("utf-8", errors="ignore").lstrip("\ufeff").lstrip()
        if not text:
            return 0.0
        if text[0] in "{[":
            # ``trace_id`` is also a canonical JSONL field, so it is not a marker.
            # CamelCase ``traceId`` and Langfuse usage/cost/type fields are.
            lowered = text.lower()
            marked = any(
                token in lowered
                for token in (
                    "traceid",
                    "parentobservationid",
                    "parent_observation_id",
                    "usagedetails",
                    "usage_details",
                    "costdetails",
                    "cost_details",
                    "provided_model_name",
                    "providedmodelname",
                    '"observations"',
                    '"generation"',
                    '"embedding"',
                )
            )
            if marked:
                return 0.96
            return 0.0
        header = text.splitlines()[0]
        try:
            _, cells = split_header(header)
        except csv.Error:
            return 0.0
        keys = {normalize_key(cell) for cell in cells if cell.strip()}
        identity = bool({"trace_id", "parent_observation_id"} & keys)
        shape = bool({"type", "start_time", "usage_details", "latency_s", "latency"} & keys)
        if identity and shape:
            return 0.94
        if "type" in keys and "model" in keys and "start_time" in keys:
            return 0.9
        return 0.0

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        if _is_csv(path):
            yield from self._read_csv(path, cfg)
            return
        yield from self._read_json(path, cfg)

    def _read_csv(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        try:
            with open_csv(path) as opened:
                headers, rows = opened
                self._warn_headers(headers)
                if not headers:
                    self.fail("CSV has no header", field=None, where=str(path), cfg=cfg)
                    return
                indexed_headers = {normalize_key(header) for header in headers}
                if not (
                    {"trace_id", "parent_observation_id", "id", "model", "timestamp"}
                    & indexed_headers
                ):
                    self.fail(
                        "header is not a Langfuse trace or observation export",
                        field=None,
                        where=str(path),
                        cfg=cfg,
                    )
                    return
                for number, raw in rows:
                    where = f"{path}:row {number}"
                    yield from self._yield_raw(raw, where=where, cfg=cfg, inherited=None)
        except UnicodeDecodeError:
            self.fail("file is not valid UTF-8", field=None, where=str(path), cfg=cfg)

    def _read_json(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        if _is_line_oriented(path):
            yield from self._read_lines(path, cfg)
            return
        size = path.stat().st_size
        if size > _LARGE_DOCUMENT_BYTES:
            self.warn(
                f"{path}: JSON document is {size} bytes (over 500 MB) and is loaded whole. "
                "One JSON object per line streams."
            )
        where = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.fail("file is not valid UTF-8", field=None, where=where, cfg=cfg)
            return
        try:
            parsed = loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            message = exc.msg if isinstance(exc, json.JSONDecodeError) else "malformed JSON"
            self.fail(f"malformed JSON ({message})", field=None, where=where, cfg=cfg)
            return
        yield from self._yield_document(parsed, where=where, cfg=cfg)

    def _read_lines(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                where = f"{path}:{line_no}"
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self.fail("line is not valid UTF-8", field=None, where=where, cfg=cfg)
                    continue
                try:
                    parsed = loads(text)
                except (json.JSONDecodeError, ValueError) as exc:
                    message = exc.msg if isinstance(exc, json.JSONDecodeError) else "malformed JSON"
                    self.fail(f"malformed JSON ({message})", field=None, where=where, cfg=cfg)
                    continue
                if not isinstance(parsed, dict):
                    self.fail("line is not a JSON object", field=None, where=where, cfg=cfg)
                    continue
                yield from self._yield_raw(parsed, where=where, cfg=cfg, inherited=None)

    def _yield_document(
        self, parsed: object, *, where: str, cfg: ReaderConfig
    ) -> Iterator[LLMCall | Span]:
        records: list[tuple[str, object]]
        if isinstance(parsed, list):
            records = [(f"{where}[{index}]", item) for index, item in enumerate(parsed)]
        elif isinstance(parsed, dict):
            records = []
            wrapped = False
            for key in ("data", "observations", "traces"):
                body = parsed.get(key)
                if isinstance(body, list):
                    wrapped = True
                    records.extend(
                        (f"{where} {key}[{index}]", item) for index, item in enumerate(body)
                    )
            if not wrapped:
                records = [(where, parsed)]
        else:
            self.fail("JSON is not an object or array", field=None, where=where, cfg=cfg)
            return
        for loc, item in records:
            if not isinstance(item, dict):
                self.fail("record is not a JSON object", field=None, where=loc, cfg=cfg)
                continue
            yield from self._yield_raw(item, where=loc, cfg=cfg, inherited=None)

    def _yield_raw(
        self,
        raw: dict[str, Any],
        *,
        where: str,
        cfg: ReaderConfig,
        inherited: dict[str, Any] | None,
    ) -> Iterator[LLMCall | Span]:
        row = index_fields(raw)
        observations = raw.get("observations")
        if not isinstance(observations, list):
            observations = raw.get("Observations")
        if isinstance(observations, list):
            parent = _trace_context(row)
            produced = False
            for index, child in enumerate(observations):
                loc = f"{where}.observations[{index}]"
                if not isinstance(child, dict):
                    self.fail("observation is not an object", field=None, where=loc, cfg=cfg)
                    continue
                produced = True
                yield from self._yield_raw(child, where=loc, cfg=cfg, inherited=parent)
            if produced:
                return
        try:
            item = self._convert(row, where=where, cfg=cfg, inherited=inherited)
        except ValueError as exc:
            field, message = split_field_error(exc)
            self.fail(message, field=field, where=where, cfg=cfg)
            return
        if item is not None:
            yield item

    def _convert(
        self,
        row: dict[str, Any],
        *,
        where: str,
        cfg: ReaderConfig,
        inherited: dict[str, Any] | None,
    ) -> LLMCall | Span | None:
        kind = str(first(row, "type") or "").strip().upper()
        if kind in _GENERATION_TYPES or (not kind and _looks_like_generation(row)):
            return self._call(
                row, where=where, cfg=cfg, inherited=inherited, kind=kind or "GENERATION"
            )
        if kind and kind not in _SPAN_KINDS and _looks_like_generation(row):
            self.warn(
                f"{where}: unknown observation type {kind!r}; recorded as a generation "
                "because it has a model and token counts"
            )
            return self._call(row, where=where, cfg=cfg, inherited=inherited, kind="GENERATION")
        if kind in _SPAN_KINDS or kind:
            if kind and kind not in _SPAN_KINDS:
                self.warn(f"{where}: unknown observation type {kind!r}; recorded as a span")
            return self._span(row, where=where, cfg=cfg, inherited=inherited, kind=kind or "SPAN")
        if _looks_like_trace(row):
            model = _model(row)
            if not model:
                raise ValueError("model: trace has no model and no observations")
            return self._call(row, where=where, cfg=cfg, inherited=inherited, kind="GENERATION")
        raise ValueError("type: record is not a Langfuse trace or observation")

    def _call(
        self,
        row: dict[str, Any],
        *,
        where: str,
        cfg: ReaderConfig,
        inherited: dict[str, Any] | None,
        kind: str,
    ) -> LLMCall:
        model = _model(row)
        if not model:
            raise ValueError("model: model is required")
        start = self._time(first(row, "start_time", "timestamp"), field="start_time")
        usage = as_object(first(row, "usage_details")) or as_object(first(row, "usage")) or {}
        self._reject_character_usage(row)
        input_tokens, output_tokens, cached, reasoning = self._tokens(usage, row)
        end_raw = first(row, "end_time")
        end = self._time(end_raw, field="end_time") if end_raw is not None else None
        duration = _duration_ms(start, end, row, self)
        http_status, error_type = status_bits(first(row, "status_message"))
        level = str(first(row, "level") or "").strip().upper()
        failed = level == "ERROR" or (http_status is not None and http_status >= 400)
        status = Status.ERROR if failed else Status.OK
        meta = as_object(first(row, "metadata")) or {}
        context = inherited or {}
        digest, prefixes = hash_content_parts(_content_parts(first(row, "input")))
        reported = _reported_cost(row)
        batch = _flag(first(meta, "is_batch", "batch"))
        tag_values = first(row, "tags")
        if tag_values is None and context:
            tag_values = context.get("tags")
        if batch is None and "batch" in _tags(tag_values):
            batch = True
        provider = infer_provider(
            model,
            first(meta, "provider", "gen_ai_provider_name", "gen_ai_system", "model_provider")
            or first(row, "provider"),
        )
        return LLMCall.model_construct(
            source="langfuse",
            record_id=str(first(row, "id") or where),
            trace_id=_text(first(row, "trace_id") or context.get("trace_id")),
            parent_id=_text(first(row, "parent_observation_id")),
            session_id=_text(first(row, "session_id") or context.get("session_id")),
            user_id=_text(first(row, "user_id") or context.get("user_id")),
            endpoint=_endpoint(row, meta, cfg, context),
            provider=provider,
            model=model,
            operation="embeddings" if kind == "EMBEDDING" else "chat",
            start_time=start,
            duration_ms=duration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
            request_count=1,
            status=status,
            http_status=http_status,
            error_type=error_type if status is Status.ERROR else None,
            attempt=None,
            is_batch=batch,
            interactive=_flag(first(meta, "interactive")),
            prefix_hashes=prefixes or None,
            content_hash=digest,
            tool_name=None,
            args_hash=None,
            reported_cost_usd=reported,
            attrs=_attrs(row, meta),
        )

    def _span(
        self,
        row: dict[str, Any],
        *,
        where: str,
        cfg: ReaderConfig,
        inherited: dict[str, Any] | None,
        kind: str,
    ) -> Span:
        del cfg
        start_raw = first(row, "start_time", "timestamp")
        if start_raw is None:
            raise ValueError("start_time: start_time is missing")
        start = self._time(start_raw, field="start_time")
        end_raw = first(row, "end_time")
        end = self._time(end_raw, field="end_time") if end_raw is not None else None
        level = str(first(row, "level") or "").strip().upper()
        context = inherited or {}
        fragments = _content_parts(first(row, "input"))
        args = content_hash("\n".join(fragments)) if fragments else None
        name = _text(first(row, "name")) or kind.lower() or "(span)"
        return Span.model_construct(
            source="langfuse",
            trace_id=_text(first(row, "trace_id") or context.get("trace_id")),
            span_id=str(first(row, "id") or where),
            parent_id=_text(first(row, "parent_observation_id")),
            name=name,
            kind=_SPAN_KINDS.get(kind, SpanKind.OTHER),
            start_time=start,
            duration_ms=_duration_ms(start, end, row, self),
            status=Status.ERROR if level == "ERROR" else Status.OK,
            args_hash=args,
        )

    def _tokens(
        self, usage: dict[str, Any], row: dict[str, Any]
    ) -> tuple[int, int, int | None, int | None]:
        incoming = _token(usage, _INPUT_KEYS, "input_tokens")
        if incoming is None:
            incoming = _token(row, ("input_tokens", "prompt_tokens"), "input_tokens")
        outgoing = _token(usage, _OUTPUT_KEYS, "output_tokens")
        if outgoing is None:
            outgoing = _token(row, ("output_tokens", "completion_tokens"), "output_tokens")
        total = _token(usage, ("total", "total_tokens"), "total_tokens")
        if total is None:
            total = _token(row, ("total_tokens",), "total_tokens")
        if incoming is None and outgoing is not None and total is not None:
            incoming = total - outgoing
            self._note_derived()
        elif outgoing is None and incoming is not None and total is not None:
            outgoing = total - incoming
            self._note_derived()
        if incoming is None:
            raise ValueError("input_tokens: input_tokens is missing")
        if outgoing is None:
            raise ValueError("output_tokens: output_tokens is missing")
        if incoming < 0 or outgoing < 0:
            raise ValueError("input_tokens: token counts must be >= 0")
        cached = _token(usage, _CACHED_KEYS, "cached_input_tokens")
        if cached is None:
            cached = _token(
                row,
                ("cached_input_tokens", "input_cached_tokens"),
                "cached_input_tokens",
            )
        if cached is not None and cached > incoming:
            incoming = incoming + cached
            if not self._cache_adjusted:
                self._cache_adjusted = True
                self.warn(
                    "cached tokens exceeded input tokens; input was treated as the "
                    "uncached portion and the cached count was added"
                )
        reasoning = _token(usage, _REASONING_KEYS, "reasoning_tokens")
        if reasoning is None:
            reasoning = _token(
                row,
                ("reasoning_tokens", "output_reasoning_tokens"),
                "reasoning_tokens",
            )
        if reasoning is not None and reasoning > outgoing and not self._reasoning_gap:
            self._reasoning_gap = True
            self.warn(
                "reasoning tokens exceed output tokens; they were kept separate and "
                "were not added on top of output tokens"
            )
        return incoming, outgoing, cached, reasoning

    def _note_derived(self) -> None:
        if not self._derived_tokens:
            self._derived_tokens = True
            self.warn("a missing input or output count was derived from the total")

    def _reject_character_usage(self, row: dict[str, Any]) -> None:
        usage = as_object(first(row, "usage")) or {}
        unit = str(first(usage, "unit") or "").strip().upper()
        if unit and unit not in {"TOKENS", "TOKEN"}:
            raise ValueError(f"usage: usage unit is {unit}, not tokens")

    def _time(self, value: object, *, field: str) -> datetime:
        parsed = parse_time(value, field=field)
        if isinstance(value, str) and value.strip() and "T" not in value and not self._loose_time:
            self._loose_time = True
            self.warn(
                "a non-ISO timestamp was accepted; naive values are read as UTC. "
                "Prefer YYYY-MM-DDTHH:MM:SSZ"
            )
        return parsed

    def _warn_headers(self, headers: list[str]) -> None:
        aliases: list[str] = []
        extra: list[str] = []
        for header in headers:
            stripped = header.strip()
            if not stripped:
                continue
            if stripped in _PREFERRED_HEADERS:
                continue
            key = normalize_key(stripped)
            if key in _KNOWN_NORMALIZED:
                aliases.append(stripped)
            elif not key.startswith("extra_"):
                extra.append(stripped)
        if aliases:
            shown = ", ".join(aliases[:8])
            self.warn(f"column aliases accepted: {shown}")
        if extra:
            shown = ", ".join(extra[:8])
            self.warn(f"ignored extra columns: {shown}")


_KNOWN_NORMALIZED = frozenset(normalize_key(name) for name in _PREFERRED_HEADERS) | frozenset(
    {
        "input_cost",
        "output_cost",
        "latency_s",
        "time_to_first_token_s",
        "completion_start_time",
        "model_parameters",
        "prompt_name",
        "prompt_version",
        "parent_observation_id",
        "calculated_input_cost",
        "calculated_output_cost",
    }
)


def _model(row: dict[str, Any]) -> str | None:
    value = first(row, "model", "provided_model_name")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _token(source: dict[str, Any], keys: tuple[str, ...], field: str) -> int | None:
    value = first(source, *keys)
    if value is None:
        return None
    return parse_whole_int(value, field=field)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _flag(value: object) -> bool | None:
    if value is None:
        return None
    return parse_bool(value)


def _content_parts(value: object) -> list[str]:
    if value is None or isinstance(value, (int, float, bool)):
        return []
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                return text_fragments(loads(text))
            except (json.JSONDecodeError, ValueError):
                return [value] if value else []
        return [value] if value else []
    return text_fragments(value)


def _reported_cost(row: dict[str, Any]) -> Decimal | None:
    details = as_object(first(row, "cost_details"))
    if details:
        total = first(details, "total", "total_cost")
        if total is not None:
            return parse_decimal(total, field="reported_cost_usd")
        left = first(details, "input", "input_cost")
        right = first(details, "output", "output_cost")
        if left is not None or right is not None:
            incoming = parse_decimal(left if left is not None else 0, field="reported_cost_usd")
            outgoing = parse_decimal(right if right is not None else 0, field="reported_cost_usd")
            return (incoming or Decimal(0)) + (outgoing or Decimal(0))
    flat = first(row, "total_cost", "calculated_total_cost", "cost", "cost_usd")
    if flat is None:
        return None
    return parse_decimal(flat, field="reported_cost_usd")


def _endpoint(
    row: dict[str, Any],
    meta: dict[str, Any],
    cfg: ReaderConfig,
    context: dict[str, Any],
) -> str:
    if cfg.endpoint_attr:
        key = normalize_key(cfg.endpoint_attr)
        chosen = first(row, key)
        if chosen is None:
            chosen = first(meta, key)
        if chosen is not None and str(chosen).strip():
            return str(chosen).strip()
    for candidate in (
        first(row, "name"),
        first(row, "trace_name"),
        context.get("name"),
    ):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return "(unknown)"


def _attrs(row: dict[str, Any], meta: dict[str, Any]) -> dict[str, str]:
    kept: dict[str, str] = {}
    env = first(row, "environment") or first(meta, "env", "environment")
    project = first(row, "project_id", "project") or first(meta, "project")
    feature = first(meta, "feature") or first(row, "feature")
    if env is not None:
        kept["env"] = str(env)
    if project is not None:
        kept["project"] = str(project)
    if feature is not None:
        kept["feature"] = str(feature)
    return kept


def _tags(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text[0] == "[":
            try:
                parsed = loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return {str(item).strip().lower() for item in parsed if str(item).strip()}
        return {part.strip().lower() for part in text.split(",") if part.strip()}
    return set()


def _trace_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace_id": _text(first(row, "id", "trace_id")),
        "name": _text(first(row, "name", "trace_name")),
        "user_id": _text(first(row, "user_id")),
        "session_id": _text(first(row, "session_id")),
        "tags": first(row, "tags"),
    }


def _looks_like_generation(row: dict[str, Any]) -> bool:
    return _model(row) is not None and (
        first(row, "input_tokens", "output_tokens", "usage", "usage_details") is not None
    )


def _looks_like_trace(row: dict[str, Any]) -> bool:
    return first(row, "timestamp") is not None and first(row, "start_time") is None


def _duration_ms(
    start: datetime,
    end: datetime | None,
    row: dict[str, Any],
    reader: LangfuseReader,
) -> float | None:
    if end is not None:
        delta = (end - start).total_seconds() * 1000
        if delta < 0:
            raise ValueError("end_time: end_time is before start_time")
        return delta
    latency = first(row, "latency_s", "latency", "duration_ms", "duration")
    if latency is None:
        return None
    number = parse_decimal(latency, field="latency")
    if number is None:
        return None
    if first(row, "duration_ms") is not None and first(row, "latency", "latency_s") is None:
        return float(number)
    if not reader._latency_seconds:
        reader._latency_seconds = True
        reader.warn(
            "latency is interpreted as seconds, which is how Langfuse documents that column"
        )
    return float(number * 1000)


def _is_csv(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(4096)
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    return bool(text) and text[0:1] not in (b"{", b"[")


def _is_line_oriented(path: Path) -> bool:
    with path.open("rb") as handle:
        first = b""
        while True:
            line = handle.readline()
            if not line:
                return False
            if line.strip():
                first = line.strip()
                break
    if not first.startswith(b"{") or not first.endswith(b"}"):
        return False
    try:
        parsed = loads(first)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(parsed, dict)
