# SPDX-License-Identifier: Apache-2.0
"""OTLP/JSON reader for GenAI spans.

Accepts an ``ExportTraceServiceRequest`` (``resourceSpans`` → ``scopeSpans`` →
``spans``), including pretty-printed documents and one-request-per-line files
from the collector file exporter. Current GenAI semantic-convention attributes
are read first, then legacy ``gen_ai.*`` names and OpenLLMetry / OpenInference
fallbacks.

A single JSON document is loaded whole. Files larger than 500 MB produce a
warning pointing at the JSONL exporter. JSONL input is streamed line by line.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from tracetally.config import ReaderConfig
from tracetally.core.cache import cache_accounting
from tracetally.core.hashing import content_hash, hash_content_parts, retained_content
from tracetally.core.schema import Capability, LLMCall, Span, SpanKind, Status
from tracetally.readers.base import BaseReader
from tracetally.readers.jsonutil import loads
from tracetally.readers.parse import (
    content_key,
    parse_bool,
    parse_decimal,
    parse_float,
    parse_int,
    parse_optional_int,
    parse_time,
    text_fragments,
)

LARGE_DOCUMENT_BYTES = 500 * 1024 * 1024

_INPUT_KEYS = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.prompt_tokens",
    "llm.token_count.prompt",
    "llm.usage.prompt_tokens",
)
_OUTPUT_KEYS = (
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.completion_tokens",
    "llm.token_count.completion",
    "llm.usage.completion_tokens",
)
_CACHED_KEYS = (
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cached_tokens",
    "llm.token_count.prompt_details.cache_read",
)
_CACHE_WRITE_KEYS = (
    "gen_ai.usage.cache_creation.input_tokens",
    "llm.token_count.prompt_details.cache_write",
)
_REASONING_KEYS = (
    "gen_ai.usage.reasoning.output_tokens",
    "llm.token_count.completion_details.reasoning",
)
_MODEL_RESPONSE_KEYS = ("gen_ai.response.model",)
_MODEL_REQUEST_KEYS = ("gen_ai.request.model", "llm.model_name", "llm.request.model")
_PROVIDER_KEYS = ("gen_ai.provider.name", "gen_ai.system", "llm.provider", "llm.system")
_OPERATION_KEYS = ("gen_ai.operation.name", "llm.request.type")
_HTTP_KEYS = ("http.response.status_code", "http.status_code")
_ERROR_KEYS = ("error.type", "exception.type")
_SESSION_KEYS = ("session.id", "gen_ai.conversation.id", "tracetally.session_id")
_USER_KEYS = ("user.id", "enduser.id", "tracetally.user_id")
_TOOL_KEYS = ("gen_ai.tool.name", "tool.name")
_ENDPOINT_DEFAULT_KEYS = ("tracetally.endpoint",)


class OtelReader(BaseReader):
    name: ClassVar[str] = "otel"
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

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        if b"resourceSpans" in head or b"resource_spans" in head:
            return 0.97
        return 0.0

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        if _is_jsonl_file(path):
            yield from self._read_jsonl(path, cfg)
            return
        size = path.stat().st_size
        if size > LARGE_DOCUMENT_BYTES:
            self.warn(
                f"{path}: single OTLP JSON document is {size} bytes "
                "(over 500 MB). It is loaded whole; a JSONL file exporter streams"
            )
        yield from self._read_document(path, cfg)

    def _read_jsonl(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
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
                yield from self._yield_document(parsed, where=where, cfg=cfg)

    def _read_document(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
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
        if not isinstance(parsed, dict):
            self.fail("OTLP document is not a JSON object", field=None, where=where, cfg=cfg)
            return
        yield from self._yield_document(parsed, where=where, cfg=cfg)

    def _yield_document(
        self,
        document: dict[str, Any],
        *,
        where: str,
        cfg: ReaderConfig,
    ) -> Iterator[LLMCall | Span]:
        resources = _list(document, "resourceSpans", "resource_spans")
        if not resources:
            self.fail(
                "OTLP document has no resourceSpans",
                field="resourceSpans",
                where=where,
                cfg=cfg,
            )
            return
        for resource_index, resource in enumerate(resources):
            if not isinstance(resource, dict):
                self.fail(
                    "resource span is not an object",
                    field="resourceSpans",
                    where=f"{where} resourceSpans[{resource_index}]",
                    cfg=cfg,
                )
                continue
            resource_attrs = _attrs(resource.get("resource"))
            scopes = _list(resource, "scopeSpans", "scope_spans") or _list(
                resource, "instrumentationLibrarySpans", "instrumentation_library_spans"
            )
            for scope_index, scope in enumerate(scopes):
                if not isinstance(scope, dict):
                    continue
                spans = scope.get("spans") or []
                if not isinstance(spans, list):
                    self.fail(
                        "spans is not a list",
                        field="spans",
                        where=f"{where} scopeSpans[{scope_index}]",
                        cfg=cfg,
                    )
                    continue
                for span_index, span in enumerate(spans):
                    loc = f"{where} spans[{span_index}]"
                    if not isinstance(span, dict):
                        self.fail("span is not an object", field=None, where=loc, cfg=cfg)
                        continue
                    try:
                        item = _span_from_otlp(span, resource_attrs, where=loc, cfg=cfg)
                    except ValueError as exc:
                        field, message = _split_error(exc)
                        self.fail(message, field=field, where=loc, cfg=cfg)
                        continue
                    if item is not None:
                        yield item


def _span_from_otlp(
    span: dict[str, Any],
    resource_attrs: dict[str, Any],
    *,
    where: str,
    cfg: ReaderConfig,
) -> LLMCall | Span | None:
    attrs = {**resource_attrs, **_attrs(span)}
    kind = _kind(attrs)
    name = str(span.get("name") or "").strip() or "(unknown)"
    trace_id = _id(span.get("traceId", span.get("trace_id")))
    span_id = _id(span.get("spanId", span.get("span_id"))) or where
    parent_id = _id(span.get("parentSpanId", span.get("parent_span_id")))
    start_raw = _span_time(span, "startTimeUnixNano", "start_time_unix_nano")
    end_raw = _span_time(span, "endTimeUnixNano", "end_time_unix_nano")
    duration = _duration_ms(start_raw, end_raw, attrs)
    status = _status(span, attrs)
    http_status = _optional_int_attr(attrs, _HTTP_KEYS, field="http_status")
    error_type = _first_str(attrs, _ERROR_KEYS)
    if status is None:
        if (http_status is not None and http_status >= 400) or error_type:
            status = Status.ERROR
        else:
            status = Status.OK

    if kind != "llm":
        start_time = _require_time(start_raw)
        return Span.model_construct(
            source="otel",
            trace_id=trace_id,
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            kind=SpanKind(kind),
            start_time=start_time,
            duration_ms=duration,
            status=status,
            args_hash=_args_hash(attrs),
        )

    model = _first_str(attrs, _MODEL_RESPONSE_KEYS) or _first_str(attrs, _MODEL_REQUEST_KEYS)
    if not model:
        raise ValueError("model: model is required")
    start_time = _require_time(start_raw)
    if not _has_any(attrs, _INPUT_KEYS):
        raise ValueError("gen_ai.usage.input_tokens: gen_ai.usage.input_tokens is required")
    if not _has_any(attrs, _OUTPUT_KEYS):
        raise ValueError("gen_ai.usage.output_tokens: gen_ai.usage.output_tokens is required")
    input_tokens = _required_int_attr(attrs, _INPUT_KEYS, field="gen_ai.usage.input_tokens")
    output_tokens = _required_int_attr(attrs, _OUTPUT_KEYS, field="gen_ai.usage.output_tokens")
    cached = _optional_int_attr(attrs, _CACHED_KEYS, field="gen_ai.usage.cache_read.input_tokens")
    created = _optional_int_attr(
        attrs, _CACHE_WRITE_KEYS, field="gen_ai.usage.cache_creation.input_tokens"
    )
    reasoning = _optional_int_attr(
        attrs, _REASONING_KEYS, field="gen_ai.usage.reasoning.output_tokens"
    )
    provider = _first_str(attrs, _PROVIDER_KEYS) or "unknown"
    accounting = cache_accounting(
        provider,
        model,
        has_cache=cached is not None or created is not None,
        source="otel",
    )

    parts = _content_parts(attrs)
    digest, prefixes = hash_content_parts(parts)
    content = retained_content(parts) if cfg.include_content else None
    endpoint = _endpoint(attrs, name, cfg)
    is_batch = parse_bool(attrs.get("tracetally.batch"))
    service_tier = _first_str(
        attrs,
        ("gen_ai.request.service_tier", "llm.service_tier", "tracetally.service_tier"),
    )
    base_url = _first_str(
        attrs,
        (
            "gen_ai.request.base_url",
            "llm.base_url",
            "server.address",
            "http.url",
        ),
    )
    interactive_raw = attrs.get("tracetally.interactive")
    interactive = parse_bool(interactive_raw) if interactive_raw is not None else None

    return LLMCall.model_construct(
        source="otel",
        record_id=span_id,
        trace_id=trace_id,
        parent_id=parent_id,
        session_id=_first_str(attrs, _SESSION_KEYS),
        user_id=_first_str(attrs, _USER_KEYS),
        endpoint=endpoint,
        provider=provider,
        model=model,
        operation=_first_str(attrs, _OPERATION_KEYS),
        start_time=start_time,
        duration_ms=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        cache_creation_tokens=created,
        cache_accounting=accounting,
        reasoning_tokens=reasoning,
        request_count=1,
        status=status,
        http_status=http_status,
        error_type=error_type,
        attempt=_optional_int_attr(attrs, ("tracetally.attempt",), field="attempt"),
        is_batch=is_batch,
        service_tier=service_tier.casefold() if service_tier else None,
        base_url=base_url,
        tokens_known=True,
        interactive=interactive,
        prefix_hashes=prefixes or None,
        content_hash=digest,
        content=content,
        tool_name=_first_str(attrs, _TOOL_KEYS),
        args_hash=_args_hash(attrs),
        reported_cost_usd=_reported_cost(attrs),
        attrs=_allow_from_flat(attrs),
    )


def _kind(attrs: dict[str, Any]) -> str:
    openinference = str(attrs.get("openinference.span.kind") or "").upper()
    if openinference in {"LLM", "EMBEDDING"}:
        return "llm"
    if openinference == "TOOL":
        return "tool"
    if openinference == "RETRIEVER":
        return "retriever"
    if openinference in {"AGENT", "CHAIN"}:
        return "agent"
    traceloop = str(attrs.get("traceloop.span.kind") or "").lower()
    if traceloop == "llm":
        return "llm"
    if traceloop == "tool":
        return "tool"
    if _has_any(attrs, _MODEL_RESPONSE_KEYS + _MODEL_REQUEST_KEYS + _INPUT_KEYS):
        return "llm"
    if attrs.get("gen_ai.operation.name") or attrs.get("gen_ai.provider.name"):
        return "llm"
    return "other"


def _endpoint(attrs: dict[str, Any], span_name: str, cfg: ReaderConfig) -> str:
    if cfg.endpoint_attr:
        value = attrs.get(cfg.endpoint_attr)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key in _ENDPOINT_DEFAULT_KEYS:
        value = attrs.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return span_name or "(unknown)"


def _content_parts(attrs: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key, value in attrs.items():
        if content_key(str(key)):
            parts.extend(text_fragments(value))
    return parts


_REPORTED_COST_KEYS = (
    "tracetally.reported_cost_usd",
    "gen_ai.usage.cost",
    "llm.cost",
)


def _reported_cost(attrs: dict[str, Any]) -> Decimal | None:
    for key in _REPORTED_COST_KEYS:
        if key in attrs and attrs[key] not in (None, ""):
            return parse_decimal(attrs[key], field="reported_cost_usd")
    return None


def _args_hash(attrs: dict[str, Any]) -> str | None:
    for key in ("gen_ai.tool.call.arguments", "tool.arguments", "llm.tools"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return content_hash(value)
    return None


def _allow_from_flat(attrs: dict[str, Any]) -> dict[str, str]:
    kept: dict[str, str] = {}
    for key in ("env", "project", "feature", "service.name"):
        value = attrs.get(key)
        if value is not None and not content_key(key):
            kept[key] = str(value)
    return kept


def _attrs(node: object) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    raw = node.get("attributes", node if _looks_flat(node) else None)
    if raw is None:
        return {}
    if isinstance(raw, dict) and "key" not in raw:
        return {str(key): _unwrap(value) for key, value in raw.items()}
    if isinstance(raw, list):
        flat: dict[str, Any] = {}
        for item in raw:
            if not isinstance(item, dict) or "key" not in item:
                continue
            flat[str(item["key"])] = _unwrap(item.get("value"))
        return flat
    return {}


def _looks_flat(node: dict[str, Any]) -> bool:
    return any(str(key).startswith(("gen_ai.", "llm.", "http.")) for key in node)


def _unwrap(value: object) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "string_value"):
        if key in value:
            return value[key]
    for key in ("intValue", "int_value"):
        if key in value:
            return value[key]
    for key in ("doubleValue", "double_value"):
        if key in value:
            return value[key]
    for key in ("boolValue", "bool_value"):
        if key in value:
            return value[key]
    array_value = value.get("arrayValue", value.get("array_value"))
    if isinstance(array_value, dict):
        return [_unwrap(item) for item in array_value.get("values", [])]
    kv_value = value.get("kvlistValue", value.get("kvlist_value"))
    if isinstance(kv_value, dict):
        return _attrs({"attributes": kv_value.get("values", [])})
    return value


def _list(node: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = node.get(key)
        if isinstance(value, list):
            return value
    return []


def _id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _span_time(span: dict[str, Any], *keys: str) -> object:
    for key in keys:
        if key in span and span[key] not in (None, ""):
            return span[key]
    return None


def _require_time(value: object) -> datetime:
    if value is None:
        raise ValueError("start_time: start_time is required")
    try:
        return parse_time(value)
    except ValueError as exc:
        raise ValueError(f"start_time: {exc}") from exc


def _duration_ms(start: object, end: object, attrs: dict[str, Any]) -> float | None:
    explicit = attrs.get("tracetally.duration_ms")
    if explicit is not None:
        return parse_float(explicit, field="duration_ms")
    if start is None or end is None:
        return None
    try:
        start_ns = int(str(start))
        end_ns = int(str(end))
    except ValueError:
        return None
    if end_ns < start_ns:
        return None
    return (end_ns - start_ns) / 1_000_000


def _status(span: dict[str, Any], attrs: dict[str, Any]) -> Status | None:
    raw = span.get("status")
    code: object = None
    if isinstance(raw, dict):
        code = raw.get("code")
    elif raw is not None:
        code = raw
    if code is None:
        code = attrs.get("otel.status_code")
    if code is None:
        return None
    if isinstance(code, int) or (isinstance(code, str) and str(code).strip().isdigit()):
        number = int(code)
        if number == 2:
            return Status.ERROR
        return Status.OK
    text = str(code).strip().upper()
    if "ERROR" in text:
        return Status.ERROR
    return Status.OK


def _first_str(attrs: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = attrs.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _has_any(attrs: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(key in attrs and attrs[key] not in (None, "") for key in keys)


def _required_int_attr(attrs: dict[str, Any], keys: tuple[str, ...], *, field: str) -> int:
    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return parse_int(attrs[key], field=field)
    raise ValueError(f"{field}: {field} is required")


def _optional_int_attr(attrs: dict[str, Any], keys: tuple[str, ...], *, field: str) -> int | None:
    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return parse_optional_int(attrs[key], field=field)
    return None


def _split_error(exc: ValueError) -> tuple[str | None, str]:
    text = str(exc)
    if ": " in text:
        field, message = text.split(": ", 1)
        return field, message
    return None, text


def _is_jsonl_file(path: Path) -> bool:
    """True when the file is one JSON object per line, not one pretty-printed document.

    The first line is parsed on its own. A pretty-printed document starts with
    ``{`` and does not end the line with ``}``, so it is loaded as one document.
    """
    with path.open("rb") as handle:
        first = b""
        while True:
            line = handle.readline()
            if not line:
                return False
            if line.strip():
                first = line.strip()
                break
        extra = handle.read(1)
    if not extra:
        return False
    if not first.startswith(b"{") or not first.endswith(b"}"):
        return False
    try:
        parsed = loads(first)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(parsed, dict)
