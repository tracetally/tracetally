# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    script = ROOT / "scripts" / "check_no_http_clients.py"
    spec = importlib.util.spec_from_file_location("check_no_http_clients", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_does_not_import_network_or_subprocess() -> None:
    module = _checker()
    assert module.source_network_offenders(ROOT) == []
    allowed = module._source_allowed("urllib.request.urlopen", ("urllib.request",))
    assert allowed is True
    assert module._source_allowed("socket.socket", ("urllib.request",)) is False


def test_runtime_dependency_tree_has_no_http_client() -> None:
    module = _checker()
    assert module.main() == 0
    assert module.banned_present({"typer", "requests"}) == ["requests"]


def test_only_fetch_may_make_an_http_call(tmp_path: Path) -> None:
    module = _checker()
    package = tmp_path / "src" / "tracetally"
    pricing = package / "pricing"
    pricing.mkdir(parents=True)
    (package / "sneak.py").write_text(
        "import urllib\n"
        "import importlib\n"
        "urllib.request.urlopen('https://example.invalid')\n"
        "importlib.import_module('http.client')\n"
        "importlib.import_module(name)\n",
        encoding="utf-8",
    )
    (package / "parse_only.py").write_text(
        "from urllib.parse import urlparse\nurlparse('http://127.0.0.1')\n",
        encoding="utf-8",
    )
    (pricing / "fetch.py").write_text(
        "import urllib.request\nurllib.request.urlopen('https://example.invalid')\n",
        encoding="utf-8",
    )
    offenders = module.source_network_offenders(tmp_path)
    blob = "\n".join(offenders)
    assert "pricing/fetch.py" not in blob
    assert "parse_only.py" not in blob
    assert "urlopen" in blob
    assert "http.client" in blob
    assert "non-literal" in blob

    (pricing / "fetch.py").write_text(
        "import socket\nimport urllib.request\n",
        encoding="utf-8",
    )
    fetch_offenders = module.source_network_offenders(tmp_path)
    assert any("fetch.py" in item and "socket" in item for item in fetch_offenders)
