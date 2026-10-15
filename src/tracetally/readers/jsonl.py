# SPDX-License-Identifier: Apache-2.0
"""Generic tracetally JSONL. One LLM call per line. Unknown fields are ignored.

Required on each object: ``model``, ``start_time``, ``input_tokens``, ``output_tokens``.
``provider`` defaults to ``unknown`` (and then stays unpriced). ``endpoint`` defaults
to ``(unknown)``. ``record_id`` defaults to ``<file>:<line>``. Prompt fields are
hashed and dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from tracetally.config import ReaderConfig
from tracetally.core.hashing import hash_content_parts
from tracetally.core.schema import Capability, LLMCall, Span
from tracetally.readers.base import BaseReader
from tracetally.readers.jsonutil import loads
from tracetally.readers.parse import (
    allowlisted_attrs,
    content_key,
    parse_bool,
    parse_decimal,
    parse_float,
    parse_int,
    parse_optional_int,
    parse_status,
    parse_time,
    text_fragments,
)

_SIGNAL_KEYS = frozenset({"model", "input_tokens", "output_tokens", "record_id", "provider"})


# A single pretty-printed JSON document above this size is loaded whole, with a warning.
_LARGE_DOCUMENT_BYTES = 500 * 1024 * 1024


class JsonlReader(BaseReader):
    name: ClassVar[str] = "jsonl"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(Capability)

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        if b"resourceSpans" in head or b"resource_spans" in head:
            return 0.0
        first = _first_nonempty_line(head)
        if not first:
            return 0.0
        try:
            parsed = json.loads(first)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and (_SIGNAL_KEYS & parsed.keys()):
            return 0.92
        text = head.decode("utf-8", errors="ignore")
        if '"model"' in text and ('"input_tokens"' in text or '"output_tokens"' in text):
            return 0.75
        if isinstance(parsed, dict):
            return 0.55
        if path.suffix.lower() == ".jsonl":
            return 0.6
        return 0.0

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        if _is_line_oriented(path):
            yield from self._read_lines(path, cfg)
            return
        size = path.stat().st_size
        if size > _LARGE_DOCUMENT_BYTES:
            self.warn(
                f"{path}: JSON document is {size} bytes (over 500 MB) and is loaded whole. "
                "One JSON object per line streams."
            )
        yield from self._read_document(path, cfg)

    def _read_lines(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, start=1):
                where = f"{path}:{line_no}"
                if not raw.strip():
                    continue
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
                yield from self._yield_record(parsed, where=where, cfg=cfg)

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
        records: list[tuple[str, object]]
        if isinstance(parsed, dict):
            records = [(where, parsed)]
        elif isinstance(parsed, list):
            records = [(f"{where}[{index}]", item) for index, item in enumerate(parsed)]
        else:
            self.fail("JSON is not an object or array", field=None, where=where, cfg=cfg)
            return
        for loc, item in records:
            if not isinstance(item, dict):
                self.fail("record is not a JSON object", field=None, where=loc, cfg=cfg)
                continue
            yield from self._yield_record(item, where=loc, cfg=cfg)

    def _yield_record(
        self, parsed: dict[str, Any], *, where: str, cfg: ReaderConfig
    ) -> Iterator[LLMCall | Span]:
        try:
            yield _call_from_object(parsed, where=where, cfg=cfg)
        except ValueError as exc:
            field, message = _split_error(exc)
            self.fail(message, field=field, where=where, cfg=cfg)


def _call_from_object(obj: dict[str, Any], *, where: str, cfg: ReaderConfig) -> LLMCall:
    content_parts: list[str] = []
    for key in list(obj):
        if content_key(str(key)):
            content_parts.extend(text_fragments(obj.pop(key)))
    digest, prefixes = hash_content_parts(content_parts)

    model = _required_str(obj, "model")
    start = parse_time(obj.get("start_time"))
    input_tokens = parse_int(obj.get("input_tokens"), field="input_tokens")
    output_tokens = parse_int(obj.get("output_tokens"), field="output_tokens")
    cached = parse_optional_int(obj.get("cached_input_tokens"), field="cached_input_tokens")
    reasoning = parse_optional_int(obj.get("reasoning_tokens"), field="reasoning_tokens")
    if cached is not None and cached > input_tokens:
        raise ValueError("cached_input_tokens: cached_input_tokens exceeds input_tokens")

    http_status = parse_optional_int(obj.get("http_status"), field="http_status")
    error_type = _optional_str(obj.get("error_type"))
    status = parse_status(obj.get("status"), http_status=http_status, error_type=error_type)
    endpoint = _endpoint(obj, cfg)
    provider = (_optional_str(obj.get("provider")) or "unknown").strip() or "unknown"
    record_id = _optional_str(obj.get("record_id")) or where
    request_count = parse_int(obj.get("request_count", 1), field="request_count", minimum=1)
    duration = parse_float(obj.get("duration_ms"), field="duration_ms")
    reported = parse_decimal(obj.get("reported_cost_usd"), field="reported_cost_usd")
    is_batch = parse_bool(obj.get("is_batch"))
    interactive = parse_bool(obj.get("interactive"))
    attempt = parse_optional_int(obj.get("attempt"), field="attempt")

    return LLMCall.model_construct(
        source="jsonl",
        record_id=record_id,
        trace_id=_optional_str(obj.get("trace_id")),
        parent_id=_optional_str(obj.get("parent_id")),
        session_id=_optional_str(obj.get("session_id")),
        user_id=_optional_str(obj.get("user_id")),
        endpoint=endpoint,
        provider=provider,
        model=model,
        operation=_optional_str(obj.get("operation")),
        start_time=start,
        duration_ms=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        request_count=request_count,
        status=status,
        http_status=http_status,
        error_type=error_type,
        attempt=attempt,
        is_batch=is_batch,
        interactive=interactive,
        prefix_hashes=prefixes or None,
        content_hash=digest,
        tool_name=_optional_str(obj.get("tool_name")),
        args_hash=_optional_str(obj.get("args_hash")),
        reported_cost_usd=reported,
        attrs=allowlisted_attrs(obj.get("attrs")),
    )


def _endpoint(obj: dict[str, Any], cfg: ReaderConfig) -> str:
    if cfg.endpoint_attr:
        raw = obj.get(cfg.endpoint_attr)
        if raw is None and isinstance(obj.get("attrs"), dict):
            raw = obj["attrs"].get(cfg.endpoint_attr)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    raw_endpoint = obj.get("endpoint")
    if raw_endpoint is not None and str(raw_endpoint).strip():
        return str(raw_endpoint).strip()
    return "(unknown)"


def _required_str(obj: dict[str, Any], field: str) -> str:
    value = obj.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{field}: {field} is required")
    if not isinstance(value, str):
        raise ValueError(f"{field}: {field} must be a string")
    return value.strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_error(exc: ValueError) -> tuple[str | None, str]:
    text = str(exc)
    if ": " in text:
        field, message = text.split(": ", 1)
        if field.isidentifier() or "." in field:
            return field, message
    # ``parse_int`` messages look like "input_tokens is not an integer".
    head = text.split(" ", 1)[0]
    if head.isidentifier():
        return head, text
    return None, text


def _is_line_oriented(path: Path) -> bool:
    """True when each record is one JSON object on its own line.

    A pretty-printed object or a JSON array is not line-oriented: the first
    line does not itself parse as an object.
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
    if not first.startswith(b"{") or not first.endswith(b"}"):
        return False
    try:
        parsed = loads(first)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(parsed, dict)


def _first_nonempty_line(head: bytes) -> str:
    text = head.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
