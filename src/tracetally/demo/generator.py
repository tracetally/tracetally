# SPDX-License-Identifier: Apache-2.0
"""Synthetic Acme Support Bot calls shared by the demo, the examples, and the tests.

The numbers are chosen so costs divide cleanly against the bundled price table.
Planted endpoints each trigger one check. ``support.control`` is clean.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """Checkout root when this file sits in the source tree. Tests use it."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "examples").is_dir():
            return parent
    return here.parents[3]


ROOT = _repo_root()

CANARY = "CANARY_PROMPT_DO_NOT_LEAK"
# 3000 token-equivalents at the chars/4 estimate used only to bucket prefixes.
PREFIX_3K = (CANARY + ("P" * 12000))[:12000]
CONTROL_ENDPOINT = "support.control"

# Observed-window factor is 1 (1 Oct 00:00 through 31 Oct 00:00). Dollars below
# are the check formulas applied to the planted rows, not a second implementation
# of the checks.
PLANTED: dict[str, dict[str, Any]] = {
    "token_bloat": {
        "endpoint": "support.bloat",
        "low": "0.005625",
        "mid": "0.01125",
        "high": "0.016875",
        "what_if": False,
    },
    "cache_opportunity": {
        "endpoint": "support.cache",
        "low": "0.0045",
        "mid": "0.00675",
        "high": "0.0081",
        "what_if": False,
    },
    "batch_candidates": {
        "endpoint": "support.nightly",
        "low": "0.00105",
        "mid": "0.001575",
        "high": "0.0021",
        "what_if": False,
    },
    "retry_waste": {
        "endpoint": "support.retry",
        "low": "0.003",
        "mid": "0.003",
        "high": "0.003",
        "what_if": False,
    },
    "agent_loops": {
        "endpoint": "support.agent",
        "low": "0.001215",
        "mid": "0.001215",
        "high": "0.001215",
        "what_if": False,
    },
    "model_swap": {
        "endpoint": "support.swap",
        "low": "0.0846",
        "mid": "0.0846",
        "high": "0.0846",
        "what_if": True,
    },
}


def calls() -> list[dict[str, Any]]:
    return _base_calls() + _planted_calls()


def spans() -> list[dict[str, Any]]:
    return _loop_spans()


def _base_calls() -> list[dict[str, Any]]:
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


def _planted_calls() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(30):
        rows.append(
            _row(
                record_id=f"bloat-low-{index}",
                trace_id=f"trace-bloat-low-{index}",
                endpoint="support.bloat",
                start_time=f"2026-10-06T{index % 24:02d}:{index % 60:02d}:00Z",
                input_tokens=5000,
                output_tokens=100,
                user_id=f"bloat-user-{index}",
                session_id=f"bloat-session-{index}",
            )
        )
    for index in range(10):
        rows.append(
            _row(
                record_id=f"bloat-high-{index}",
                trace_id=f"trace-bloat-high-{index}",
                endpoint="support.bloat",
                start_time=f"2026-10-07T{index:02d}:10:00Z",
                input_tokens=20000,
                output_tokens=100,
                user_id=f"bloat-tail-{index}",
                session_id=f"bloat-tail-session-{index}",
            )
        )
    for index in range(40):
        rows.append(
            _row(
                record_id=f"cache-{index}",
                trace_id=f"trace-cache-{index}",
                endpoint="support.cache",
                start_time=f"2026-10-11T{index % 24:02d}:{index % 60:02d}:00Z",
                input_tokens=3000,
                output_tokens=50,
                prompt=PREFIX_3K,
                user_id=f"cache-user-{index}",
                session_id=f"cache-session-{index}",
            )
        )
    for index in range(20):
        day = 1 + index // 2
        minute = 5 * (index % 2)
        rows.append(
            _row(
                record_id=f"nightly-{index}",
                trace_id=f"trace-nightly-{index}",
                endpoint="support.nightly",
                start_time=f"2026-10-{day:02d}T02:{minute:02d}:00Z",
                input_tokens=1000,
                output_tokens=100,
                interactive=False,
            )
        )
    for index in range(10):
        prompt = f"retry-pair-{index}"
        rows.append(
            _row(
                record_id=f"retry-fail-{index}",
                trace_id=f"trace-retry-{index}",
                endpoint="support.retry",
                start_time=f"2026-10-15T{index:02d}:00:00Z",
                input_tokens=2000,
                output_tokens=0,
                status="error",
                http_status=429,
                error_type="rate_limit_exceeded",
                attempt=1,
                prompt=prompt,
                user_id=f"retry-user-{index}",
                session_id=f"retry-session-{index}",
            )
        )
        rows.append(
            _row(
                record_id=f"retry-ok-{index}",
                trace_id=f"trace-retry-{index}",
                endpoint="support.retry",
                start_time=f"2026-10-15T{index:02d}:00:30Z",
                input_tokens=2000,
                output_tokens=50,
                attempt=2,
                prompt=prompt,
                user_id=f"retry-user-{index}",
                session_id=f"retry-session-{index}",
            )
        )
    for index in range(30):
        rows.append(
            _row(
                record_id=f"agent-{index}",
                trace_id=f"trace-agent-{index}",
                endpoint="support.agent",
                start_time=f"2026-10-18T{index % 24:02d}:20:00Z",
                input_tokens=500,
                output_tokens=100,
                prompt=f"agent-step-{index}",
                user_id=f"agent-user-{index}",
                session_id=f"agent-session-{index}",
            )
        )
    for index in range(10):
        rows.append(
            _row(
                record_id=f"agent-loop-{index}",
                trace_id="trace-agent-loop",
                endpoint="support.agent",
                start_time=f"2026-10-18T12:30:{index:02d}Z",
                input_tokens=500,
                output_tokens=100,
                prompt=f"agent-loop-step-{index}",
                user_id="agent-loop-user",
                session_id="agent-loop-session",
            )
        )
    for index in range(20):
        rows.append(
            _row(
                record_id=f"swap-{index}",
                trace_id=f"trace-swap-{index}",
                endpoint="support.swap",
                provider="openai",
                model="gpt-4o",
                start_time=f"2026-10-21T{index:02d}:00:00Z",
                input_tokens=1000,
                output_tokens=200,
                prompt=f"swap-{index}",
                user_id=f"swap-user-{index}",
                session_id=f"swap-session-{index}",
            )
        )
    for index in range(24):
        if index == 23:
            start = "2026-10-31T00:00:00Z"
        else:
            start = f"2026-10-{23 + index // 8:02d}T{(index * 3) % 24:02d}:40:00Z"
        rows.append(
            _row(
                record_id=f"control-{index}",
                trace_id=f"trace-control-{index}",
                endpoint=CONTROL_ENDPOINT,
                start_time=start,
                duration_ms=150,
                input_tokens=800,
                output_tokens=200,
                prompt=f"control-{index}",
                user_id=f"control-user-{index}",
                session_id=f"control-session-{index}",
                interactive=True,
            )
        )
    return rows


def _loop_spans() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(6):
        rows.append(
            {
                "kind": "tool",
                "span_id": f"tool-loop-{index}",
                "trace_id": "trace-agent-loop",
                "name": "search_kb",
                "args_hash": "lookup:kb:v1",
                "start_time": f"2026-10-18T12:31:{index:02d}Z",
                "status": "ok",
                "duration_ms": 15,
            }
        )
    return rows


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "duration_ms": 120,
        "status": "ok",
        "operation": "chat",
        "interactive": True,
    }
    row.update(overrides)
    return row


def jsonl_text() -> str:
    lines = [json.dumps(row, separators=(",", ":")) for row in calls()]
    lines.extend(json.dumps(row, separators=(",", ":")) for row in spans())
    return "".join(line + "\n" for line in lines)


def otel_document() -> dict[str, Any]:
    span_rows: list[dict[str, Any]] = [_tool_span()]
    for row in calls():
        span_rows.append(_llm_span(row))
    for row in spans():
        span_rows.append(_tool_from_row(row))
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _kv("service.name", "acme-support-bot"),
                    ]
                },
                "scopeSpans": [{"scope": {"name": "tracetally.synthetic"}, "spans": span_rows}],
            }
        ]
    }


def tier_map_text() -> str:
    return "endpoints:\n  support.swap:\n    provider: openai\n    model: gpt-4o-mini\n"


def ground_truth_document() -> dict[str, Any]:
    return {
        "control_endpoint": CONTROL_ENDPOINT,
        "planted": PLANTED,
        "notes": (
            "Dollars assume a 30-day window (monthly factor 1) and the bundled "
            "2026-10 price table. model_swap is a what-if."
        ),
    }


def write_demo_files(directory: Path, *, seed: int) -> dict[str, Path]:
    """Write the planted Acme Support Bot files used by ``tracetally demo``.

    ``seed`` is recorded on ``ground_truth.json``. It does not redraw the rows,
    so the planted dollar amounts stay the ones the tests check.
    """
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "jsonl": directory / "synthetic.jsonl",
        "otel": directory / "synthetic_otel.json",
        "tiers": directory / "tiers.yaml",
        "ground_truth": directory / "ground_truth.json",
    }
    paths["jsonl"].write_text(jsonl_text(), encoding="utf-8", newline="\n")
    paths["otel"].write_text(
        json.dumps(otel_document(), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    paths["tiers"].write_text(tier_map_text(), encoding="utf-8", newline="\n")
    truth = ground_truth_document()
    truth["seed"] = seed
    truth["notes"] = (
        str(truth["notes"]) + " The seed labels the demo run. It does not redraw the planted rows."
    )
    paths["ground_truth"].write_text(
        json.dumps(truth, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return paths


def write_examples(root: Path | None = None) -> None:
    target = root or (ROOT / "examples")
    target.mkdir(parents=True, exist_ok=True)
    (target / "synthetic.jsonl").write_text(jsonl_text(), encoding="utf-8")
    (target / "synthetic_otel.json").write_text(
        json.dumps(otel_document(), indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "tiers.example.yaml").write_text(tier_map_text(), encoding="utf-8")
    (target / "ground_truth.json").write_text(
        json.dumps(ground_truth_document(), indent=2) + "\n",
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
    if row.get("prompt"):
        attributes.append(_kv("gen_ai.input.messages", row["prompt"]))
    if row.get("user_id"):
        attributes.append(_kv("user.id", row["user_id"]))
    if row.get("session_id"):
        attributes.append(_kv("session.id", row["session_id"]))
    if row.get("interactive") is not None:
        attributes.append(_kv("tracetally.interactive", bool(row["interactive"])))
    if row.get("attempt") is not None:
        attributes.append(_kv("tracetally.attempt", int(row["attempt"])))
    if row.get("is_batch"):
        attributes.append(_kv("tracetally.batch", True))
    if row.get("reported_cost_usd") is not None:
        attributes.append(_kv("tracetally.reported_cost_usd", row["reported_cost_usd"]))
    status = "STATUS_CODE_ERROR" if row["status"] == "error" else "STATUS_CODE_OK"
    span: dict[str, Any] = {
        "traceId": row["trace_id"],
        "spanId": row["record_id"],
        "name": row["endpoint"],
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes,
        "status": {"code": status},
    }
    if row.get("parent_id"):
        span["parentSpanId"] = row["parent_id"]
    return span


def _tool_from_row(row: dict[str, Any]) -> dict[str, Any]:
    start_ns, end_ns = _window(str(row["start_time"]), int(row["duration_ms"]))
    return {
        "traceId": row["trace_id"],
        "spanId": row["span_id"],
        "name": row["name"],
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            _kv("openinference.span.kind", "TOOL"),
            _kv("gen_ai.tool.name", row["name"]),
            _kv("gen_ai.tool.call.arguments", row["args_hash"]),
        ],
        "status": {"code": "STATUS_CODE_OK"},
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
