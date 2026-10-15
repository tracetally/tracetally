# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tracetally"

BANNED_PREFIXES = (
    "socket",
    "http.client",
    "urllib.request",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "subprocess",
)


def test_source_does_not_import_network_or_subprocess() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for name in _imported_names(node):
                if _banned(name):
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert offenders == []


def test_runtime_dependency_tree_has_no_http_client() -> None:
    script = ROOT / "scripts" / "check_no_http_clients.py"
    spec = importlib.util.spec_from_file_location("check_no_http_clients", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0
    assert module.banned_present({"typer", "requests"}) == ["requests"]


def _imported_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        names = [node.module]
        names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        return names
    return []


def _banned(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in BANNED_PREFIXES)
