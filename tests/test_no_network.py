# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_does_not_import_network_or_subprocess() -> None:
    script = ROOT / "scripts" / "check_no_http_clients.py"
    spec = importlib.util.spec_from_file_location("check_no_http_clients", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.source_network_offenders(ROOT) == []
    allowed = module._source_allowed("urllib.request.urlopen", ("urllib.request",))
    assert allowed is True
    assert module._source_allowed("socket.socket", ("urllib.request",)) is False


def test_runtime_dependency_tree_has_no_http_client() -> None:
    script = ROOT / "scripts" / "check_no_http_clients.py"
    spec = importlib.util.spec_from_file_location("check_no_http_clients", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0
    assert module.banned_present({"typer", "requests"}) == ["requests"]
