# SPDX-License-Identifier: Apache-2.0
"""Synthetic Acme Support Bot calls shared by the examples and the tests.

The numbers are chosen so costs divide cleanly against the bundled price table.
This is not the full seeded issue-planter (that lands with the checks).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def calls() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(20):
        status = "ok"
        http_status: int | None = None
        error_type: str | None = None
        if index == 0:
            status = "error"
            http_status = 429
            error_type = "rate_limit_exceeded"
        elif index == 1:
            status = "error"
            http_status = 503
            error_type = "server_error"
        rows.append(
            {
                "record_id": f"mini-{index}",
                "trace_id": f"trace-mini-{index}",
                "endpoint": "support.reply",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "start_time": f"2026-10-01T{index:02d}:00:00Z",
                "duration_ms": 100 + index * 10,
                "input_tokens": 1000,
                "cached_input_tokens": 200,
                "output_tokens": 100,
                "status": status,
                "http_status": http_status,
                "error_type": error_type,
                "operation": "chat",
            }
        )
    for index in range(5):
        rows.append(
            {
                "record_id": f"draft-{index}",
                "trace_id": f"trace-draft-{index}",
                "endpoint": "support.draft",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "start_time": f"2026-10-02T0{index}:00:00Z",
                "duration_ms": 400,
                "input_tokens": 2000,
                "output_tokens": 500,
                "status": "ok",
                "is_batch": index == 0,
                "operation": "chat",
            }
        )
    for index in range(4):
        rows.append(
            {
                "record_id": f"classify-{index}",
                "trace_id": f"trace-classify-{index}",
                "endpoint": "support.classify",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "start_time": f"2026-10-03T0{index}:15:00Z",
                "duration_ms": 80,
                "input_tokens": 10_000,
                "output_tokens": 1000,
                "reasoning_tokens": 100,
                "status": "ok",
                "operation": "generate_content",
            }
        )
    for index in range(2):
        rows.append(
            {
                "record_id": f"private-{index}",
                "trace_id": f"trace-private-{index}",
                "endpoint": "support.internal",
                "provider": "openai",
                "model": "acme-private-llm",
                "start_time": f"2026-10-04T0{index}:00:00Z",
                "duration_ms": 50,
                "input_tokens": 100,
                "output_tokens": 10,
                "status": "ok",
                "reported_cost_usd": "0.01",
            }
        )
    return rows


def jsonl_text() -> str:
    return "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in calls())


def otel_document() -> dict[str, Any]:
    spans: list[dict[str, Any]] = [_tool_span()]
    for row in calls():
        spans.append(_llm_span(row))
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _kv("service.name", "acme-support-bot"),
                    ]
                },
                "scopeSpans": [{"scope": {"name": "tracetally.synthetic"}, "spans": spans}],
            }
        ]
    }


def write_examples(root: Path | None = None) -> None:
    target = root or (ROOT / "examples")
    target.mkdir(parents=True, exist_ok=True)
    (target / "synthetic.jsonl").write_text(jsonl_text(), encoding="utf-8")
    (target / "synthetic_otel.json").write_text(
        json.dumps(otel_document(), indent=2) + "\n",
        encoding="utf-8",
    )


def _llm_span(row: dict[str, Any]) -> dict[str, Any]:
    start_ns, end_ns = _window(str(row["start_time"]), int(row["duration_ms"]))
    attributes: list[dict[str, Any]] = [
        _kv("gen_ai.provider.name", row["provider"]),
        _kv("gen_ai.response.model", row["model"]),
        _kv("gen_ai.request.model", row["model"]),
        _kv("gen_ai.operation.name", row.get("operation") or "chat"),
        _kv("gen_ai.usage.input_tokens", int(row["input_tokens"])),
        _kv("gen_ai.usage.output_tokens", int(row["output_tokens"])),
        _kv("tracetally.endpoint", row["endpoint"]),
    ]
    if row.get("cached_input_tokens"):
        attributes.append(
            _kv("gen_ai.usage.cache_read.input_tokens", int(row["cached_input_tokens"]))
        )
    if row.get("reasoning_tokens"):
        attributes.append(_kv("gen_ai.usage.reasoning.output_tokens", int(row["reasoning_tokens"])))
    if row.get("error_type"):
        attributes.append(_kv("error.type", row["error_type"]))
    if row.get("http_status") is not None:
        attributes.append(_kv("http.response.status_code", int(row["http_status"])))
    if row.get("is_batch"):
        attributes.append(_kv("tracetally.batch", True))
    if row.get("reported_cost_usd") is not None:
        attributes.append(_kv("tracetally.reported_cost_usd", row["reported_cost_usd"]))
    status = "STATUS_CODE_ERROR" if row["status"] == "error" else "STATUS_CODE_OK"
    return {
        "traceId": row["trace_id"],
        "spanId": row["record_id"],
        "name": row["endpoint"],
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes,
        "status": {"code": status},
    }


def _tool_span() -> dict[str, Any]:
    start_ns, end_ns = _window("2026-10-01T00:00:30Z", 20)
    return {
        "traceId": "trace-mini-0",
        "spanId": "tool-search",
        "parentSpanId": "mini-0",
        "name": "search_kb",
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            _kv("openinference.span.kind", "TOOL"),
            _kv("gen_ai.tool.name", "search_kb"),
        ],
        "status": {"code": "STATUS_CODE_OK"},
    }


def _window(start: str, duration_ms: int) -> tuple[int, int]:
    parsed = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_ns = int(parsed.timestamp()) * 1_000_000_000
    return start_ns, start_ns + duration_ms * 1_000_000


def _kv(key: str, value: object) -> dict[str, Any]:
    if isinstance(value, bool):
        wrapped: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


if __name__ == "__main__":
    write_examples()
