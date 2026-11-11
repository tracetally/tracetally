# SPDX-License-Identifier: Apache-2.0
"""Make user-supplied names safe to print on a terminal."""

from __future__ import annotations


def terminal_safe(value: object) -> str:
    """Fold ESC, C0, and C1 controls so a name cannot rewrite the terminal.

    Newlines and the other controls become spaces. The characters that carry
    the name stay. This is the helper ``tracetally run`` uses.
    """
    text = "" if value is None else str(value)
    safe: list[str] = []
    for char in text:
        code = ord(char)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F or char in {"\u2028", "\u2029"}:
            safe.append(" ")
        else:
            safe.append(char)
    return "".join(safe)
