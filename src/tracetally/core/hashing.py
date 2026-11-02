# SPDX-License-Identifier: Apache-2.0
"""Content hashing. Raw prompt text is discarded by readers after this runs."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

# Token-equivalent boundaries. Length is estimated as chars/4 only to choose
# the cut point. Dollar math uses reported token counts, never this estimate.
PREFIX_TOKEN_BOUNDARIES: tuple[int, ...] = (256, 512, 1024, 2048, 4096)
_CHARS_PER_TOKEN = 4


def blake2b16(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def content_hash(text: str) -> str:
    return blake2b16(text.encode("utf-8"))


def prefix_hashes(text: str) -> tuple[tuple[int, str], ...]:
    """Hashes of the leading text at each boundary the content actually reaches."""
    encoded_len = len(text)
    found: list[tuple[int, str]] = []
    for tokens in PREFIX_TOKEN_BOUNDARIES:
        nchars = tokens * _CHARS_PER_TOKEN
        if encoded_len < nchars:
            break
        found.append((tokens, content_hash(text[:nchars])))
    return tuple(found)


_SKIP_KEYS = frozenset({"role", "type", "name"})


def _collect_text(node: object, found: list[str]) -> None:
    """Flatten prompt-shaped values. Role labels are not part of the content."""
    if isinstance(node, str):
        if node:
            found.append(node)
        return
    if isinstance(node, dict):
        for key, child in node.items():
            if str(key) not in _SKIP_KEYS:
                _collect_text(child, found)
        return
    if isinstance(node, (list, tuple)):
        for child in node:
            _collect_text(child, found)


# Kept only when a run asks for raw text. The hash still uses the full string.
CONTENT_RETAIN_CHARS = 4000


def retained_content(parts: Sequence[object]) -> str | None:
    """Join prompt fragments for a report. Empty when there is no text.

    The result is capped so one large prompt cannot dominate the output.
    """
    texts: list[str] = []
    for part in parts:
        _collect_text(part, texts)
    if not texts:
        return None
    joined = "\n".join(texts)
    if len(joined) <= CONTENT_RETAIN_CHARS:
        return joined
    return joined[:CONTENT_RETAIN_CHARS]


def hash_content_parts(parts: Sequence[object]) -> tuple[str | None, tuple[tuple[int, str], ...]]:
    """Hash prompt fragments, including message dicts. Other types are skipped.

    Returns ``(content_hash, prefix_hashes)``. Both are empty when nothing
    textual was supplied.
    """
    texts: list[str] = []
    for part in parts:
        _collect_text(part, texts)
    if not texts:
        return None, ()
    joined = "\n".join(texts)
    return content_hash(joined), prefix_hashes(joined)
