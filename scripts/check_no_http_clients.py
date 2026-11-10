# SPDX-License-Identifier: Apache-2.0
"""Fail if tracetally's runtime dependency tree contains an HTTP client.

Dev extras are ignored. Run this in an environment where tracetally itself
is installed (``pip install -e .`` is enough).
"""

from __future__ import annotations

import ast
import importlib.metadata
import sys
from pathlib import Path

BANNED = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "httpcore",
    "httplib2",
    "niquests",
    "treq",
}

# Stdlib HTTP imports are banned everywhere except the single price fetch.
SOURCE_BANNED_PREFIXES = (
    "socket",
    "http.client",
    "urllib.request",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "subprocess",
)
SOURCE_ALLOW = {
    "src/tracetally/pricing/fetch.py": ("urllib.request",),
}


def runtime_dependency_names() -> set[str]:
    """Names of tracetally's runtime dependencies, including transitive ones."""
    root = importlib.metadata.distribution("tracetally")
    names: set[str] = set()
    queue: list[str] = []
    for requirement in root.requires or []:
        if "extra ==" in requirement:
            continue
        queue.append(_name(requirement))
    while queue:
        name = queue.pop()
        key = name.lower()
        if key in names:
            continue
        names.add(key)
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        for requirement in dist.requires or []:
            if "extra ==" in requirement:
                continue
            queue.append(_name(requirement))
    return names


def banned_present(names: set[str]) -> list[str]:
    return sorted(names & BANNED)


def _name(requirement: str) -> str:
    return requirement.split(";", 1)[0].split("[", 1)[0].strip()


def source_network_offenders(root: Path | None = None) -> list[str]:
    """Imports of network APIs outside ``pricing/fetch.py``."""
    base = root or Path(__file__).resolve().parents[1]
    src = base / "src" / "tracetally"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        relative = path.relative_to(base).as_posix()
        allowed = SOURCE_ALLOW.get(relative, ())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for name in _imported_names(node):
                if _source_banned(name) and not _source_allowed(name, allowed):
                    offenders.append(f"{relative} imports {name}")
    return offenders


def main() -> int:
    try:
        names = runtime_dependency_names()
    except importlib.metadata.PackageNotFoundError:
        print("error: tracetally is not installed", file=sys.stderr)
        return 1
    found = banned_present(names)
    if found:
        print(
            "error: HTTP client in the runtime dependency tree: " + ", ".join(found),
            file=sys.stderr,
        )
        return 1
    offenders = source_network_offenders()
    if offenders:
        print("error: network import outside pricing/fetch.py:", file=sys.stderr)
        for offender in offenders:
            print(f"error: {offender}", file=sys.stderr)
        return 1
    print(f"ok runtime dependencies ({len(names)}): " + ", ".join(sorted(names)))
    print("ok network imports limited to src/tracetally/pricing/fetch.py")
    return 0


def _imported_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        names = [node.module]
        names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        return names
    return []


def _source_banned(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in SOURCE_BANNED_PREFIXES)


def _source_allowed(name: str, allowed: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in allowed)


if __name__ == "__main__":
    raise SystemExit(main())
