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

# Call names that open an HTTP connection even when the import was disguised.
_HTTP_CALL_NAMES = frozenset(
    {
        "urlopen",
        "urlretrieve",
        "HTTPConnection",
        "HTTPSConnection",
    }
)


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
    """Network imports and HTTP calls outside ``pricing/fetch.py``.

    ``pricing/fetch.py`` may import ``urllib.request`` and call it. Every
    other module fails on a banned import, on ``urllib.request`` reached
    through an attribute, on ``importlib.import_module`` / ``__import__``,
    and on ``urlopen`` / ``urlretrieve`` / ``HTTP(S)Connection``.
    """
    base = root or Path(__file__).resolve().parents[1]
    src = base / "src" / "tracetally"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        relative = path.relative_to(base).as_posix()
        allowed = SOURCE_ALLOW.get(relative, ())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(f"{relative} {use}" for use in _network_uses(tree, allowed))
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
        print("error: network use outside pricing/fetch.py:", file=sys.stderr)
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


def _network_uses(tree: ast.AST, allowed: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if _source_banned(name) and not _source_allowed(name, allowed):
                found.append(f"imports {name}")
        chain = _attribute_chain(node)
        if chain and _source_banned(chain) and not _source_allowed(chain, allowed):
            found.append(f"references {chain}")
        dynamic = _dynamic_import_name(node)
        if dynamic == "<non-literal>":
            found.append("imports a non-literal module")
        elif dynamic and _source_banned(dynamic) and not _source_allowed(dynamic, allowed):
            found.append(f"imports {dynamic}")
        # The allowlisted module may call urllib. Everywhere else, a direct
        # HTTP entry point is a use even when the import was hidden.
        if not allowed:
            call = _http_call_name(node)
            if call:
                found.append(f"calls {call}")
    return _drop_prefixes(found)


def _attribute_chain(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _dynamic_import_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    is_import = (isinstance(func, ast.Name) and func.id == "__import__") or (
        isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}
    )
    if not is_import:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return "<non-literal>"


def _http_call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id in _HTTP_CALL_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _HTTP_CALL_NAMES:
        return func.attr
    return None


def _drop_prefixes(items: list[str]) -> list[str]:
    unique = list(dict.fromkeys(items))
    return [
        item
        for item in unique
        if not any(other != item and other.startswith(item) for other in unique)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
