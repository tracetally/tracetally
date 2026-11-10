# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from tracetally.cli import app

runner = CliRunner()


def test_report_markdown_keeps_blank_lines_after_block_tags(tmp_path: Path) -> None:
    from tests.support.synthetic import write_examples

    write_examples(tmp_path)
    prices = tmp_path / "tracetally.toml"
    prices.write_text('currency = "USD"\nfx_rate = "1.1"\n', encoding="utf-8")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "report",
            str(tmp_path / "synthetic.jsonl"),
            "--tier-map",
            str(tmp_path / "tiers.example.yaml"),
            "--pricing-config",
            str(prices),
            "--out",
            str(out),
            "--offline",
        ],
    )
    assert result.exit_code == 0, result.output
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "SYNTHETIC DATA" not in markdown
    assert "was not fetched.\n\n## Executive summary\n" in markdown
    assert re.search(r"\(what-if\)\n\n`", markdown)
