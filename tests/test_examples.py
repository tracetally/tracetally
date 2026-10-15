# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from tests.support.synthetic import ROOT, jsonl_text, otel_document


def test_checked_in_examples_match_the_generator() -> None:
    examples = ROOT / "examples"
    assert (examples / "synthetic.jsonl").read_text(encoding="utf-8") == jsonl_text()
    document = json.loads((examples / "synthetic_otel.json").read_text(encoding="utf-8"))
    assert document == otel_document()
