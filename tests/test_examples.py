# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from tests.support.synthetic import (
    ROOT,
    ground_truth_document,
    jsonl_text,
    otel_document,
    tier_map_text,
)


def test_checked_in_examples_match_the_generator() -> None:
    examples = ROOT / "examples"
    assert (examples / "synthetic.jsonl").read_text(encoding="utf-8") == jsonl_text()
    document = json.loads((examples / "synthetic_otel.json").read_text(encoding="utf-8"))
    assert document == otel_document()
    assert (examples / "tiers.example.yaml").read_text(encoding="utf-8") == tier_map_text()
    truth = json.loads((examples / "ground_truth.json").read_text(encoding="utf-8"))
    assert truth == ground_truth_document()
