# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from tests.support.synthetic import calls, jsonl_text, otel_document
from tracetally.analysis.summary import summarize
from tracetally.config import ReaderConfig
from tracetally.pricing.table import load_bundled
from tracetally.readers.base import load_paths


def _write(tmp_path: Path) -> tuple[Path, Path]:
    jsonl = tmp_path / "synthetic.jsonl"
    otel = tmp_path / "synthetic_otel.json"
    jsonl.write_text(jsonl_text(), encoding="utf-8")
    otel.write_text(json.dumps(otel_document()), encoding="utf-8")
    return jsonl, otel


def test_summary_cost_latency_and_error_rates(tmp_path: Path) -> None:
    jsonl, otel = _write(tmp_path)
    table = load_bundled()
    from_jsonl = summarize(load_paths([jsonl], ReaderConfig()), table)
    from_otel = summarize(load_paths([otel], ReaderConfig()), table)

    assert len(calls()) == 31
    mini = Decimal("0.000195") * 20
    claude = Decimal("0.0135") * 4 + Decimal("0.0135") * Decimal("0.5")
    gemini = Decimal("0.0055") * 4
    expected = mini + claude + gemini
    assert expected == Decimal("0.08665")

    for result, spans in ((from_jsonl, 0), (from_otel, 1)):
        assert result.calls == 31
        assert result.requests == 31
        assert result.spans == spans
        assert result.priced_calls == 29
        assert result.unpriced_calls == 2
        assert result.cost_usd == expected
        assert result.n_429 == 1
        assert result.n_5xx == 1
        assert result.n_error == 2
        assert result.reported_cost_usd == Decimal("0.02")
        assert result.reasoning_tokens == 400
        by_model = {(row.provider, row.model): row for row in result.by_model}
        reply = by_model[("openai", "gpt-4o-mini")]
        assert reply.latency().p50 == pytest_approx(195)
        assert reply.latency().p95 == pytest_approx(280.5)
        assert reply.latency().p99 == pytest_approx(288.1)
        assert reply.n_429 == 1
        assert reply.n_5xx == 1
        draft = by_model[("anthropic", "claude-sonnet-4-5")]
        assert draft.latency().hidden
        assert draft.cost_usd == claude
        private = by_model[("openai", "acme-private-llm")]
        assert private.cost_usd is None
        assert private.reported_cost_usd == Decimal("0.02")
        assert any("acme-private-llm" in warning for warning in result.warnings)
        assert any("gemini-2.5-flash" in warning for warning in result.warnings)
        text = result.to_text()
        assert "gpt-4o-mini" in text
        assert "support.reply" in text
        assert "n<20" in text
        assert "0.08665" in text
        assert "acme-private-llm" in text
        payload = result.to_dict()
        assert payload["summary_schema_version"] == 1
        assert payload["totals"]["cost_usd"] == "0.08665"
        assert payload["error_rates"]["n_429"] == 1
        assert payload["error_rates"]["n_5xx"] == 1
        assert payload["mode"] == "trace"
        assert payload["skipped_checks"] == []
        assert payload["error_rates"]["measured"] is True


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value)
