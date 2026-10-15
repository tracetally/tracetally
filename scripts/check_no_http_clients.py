# SPDX-License-Identifier: Apache-2.0
"""Fail if tracetally's runtime dependency tree contains an HTTP client.

Dev extras are ignored. Run this in an environment where tracetally itself
is installed (``pip install -e .`` is enough).
"""

from __future__ import annotations

import importlib.metadata
import sys

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
    print(f"ok runtime dependencies ({len(names)}): " + ", ".join(sorted(names)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
