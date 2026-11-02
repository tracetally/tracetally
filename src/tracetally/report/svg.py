# SPDX-License-Identifier: Apache-2.0
"""Hand-built SVG charts. No scripts, no images, no links."""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape

from tracetally.core.money import format_usd

_WIDTH = 720
_LABEL_W = 248
_PLOT_W = 340
_MAX_BARS = 12
_FONT = "ui-sans-serif, sans-serif"
_INK = "#1c1915"
_MUTED = "#5c564c"
_LINE = "#d9d2c5"
_PAPER = "#f7f4ee"
_BAR = "#8c3a2f"
_P50 = "#1f6b4a"
_P95 = "#8a5a12"
_P99 = "#8c3a2f"


def bar_chart(title: str, series: list[tuple[str, Decimal]]) -> str:
    """Horizontal bars. ``series`` is largest-first. Extra rows are counted, not drawn."""
    priced = [(label, amount) for label, amount in series if amount > 0]
    if not priced:
        return empty_chart(title, "No priced cost to chart.")
    shown = priced[:_MAX_BARS]
    omitted = len(priced) - len(shown)
    peak = max(amount for _, amount in shown)
    height = 40 + len(shown) * 26 + (18 if omitted else 8)
    lines = [_open(title, height)]
    lines.append(_text(16, 22, title, size=14, fill=_INK, weight="600"))
    for index, (label, amount) in enumerate(shown):
        y = 40 + index * 26
        width = _bar_px(amount, peak, _PLOT_W)
        lines.append(_text(16, y + 12, _short(label), size=12, fill=_MUTED))
        lines.append(f'<rect x="{_LABEL_W}" y="{y}" width="{_PLOT_W}" height="14" fill="#efeae2"/>')
        lines.append(
            f'<rect x="{_LABEL_W}" y="{y}" width="{width}" height="14" fill="{_BAR}">'
            f"<title>{_xml(_short(label, 80))} {_xml(format_usd(amount))}</title></rect>"
        )
        lines.append(_text(_LABEL_W + _PLOT_W + 8, y + 12, format_usd(amount), size=12, fill=_INK))
    if omitted:
        lines.append(
            _text(16, height - 8, f"{omitted} further rows are in the table.", size=11, fill=_MUTED)
        )
    lines.append("</svg>")
    return "".join(lines)


def latency_chart(rows: list[tuple[str, float, float, float]]) -> str:
    """Dot plot of p50, p95, and p99 in milliseconds. One row per endpoint × model."""
    title = "Latency p50 / p95 / p99"
    if not rows:
        return empty_chart(title, "No group has 20 or more durations.")
    shown = sorted(rows, key=lambda item: (-item[3], item[0]))[:_MAX_BARS]
    omitted = len(rows) - len(shown)
    xmax = max(item[3] for item in shown)
    if xmax <= 0:
        return empty_chart(title, "Latency samples are zero.")
    xmax *= 1.08
    height = 64 + len(shown) * 26 + (18 if omitted else 8)
    left = _LABEL_W
    lines = [_open(title, height)]
    lines.append(_text(16, 20, title, size=14, fill=_INK, weight="600"))
    lines.append(_legend(left))
    for index, (label, p50, p95, p99) in enumerate(shown):
        y = 58 + index * 26
        lines.append(_text(16, y + 4, _short(label), size=12, fill=_MUTED))
        lines.append(
            f'<line x1="{left}" y1="{y}" x2="{left + _PLOT_W}" y2="{y}" stroke="{_LINE}"/>'
        )
        lines.append(_dot(left, _PLOT_W, xmax, p50, y, _P50, "p50"))
        lines.append(_dot(left, _PLOT_W, xmax, p95, y, _P95, "p95"))
        lines.append(_dot(left, _PLOT_W, xmax, p99, y, _P99, "p99"))
    if omitted:
        lines.append(
            _text(
                16, height - 6, f"{omitted} further groups are in the table.", size=11, fill=_MUTED
            )
        )
    lines.append("</svg>")
    return "".join(lines)


def empty_chart(title: str, message: str) -> str:
    lines = [
        _open(title, 72),
        f'<rect width="{_WIDTH}" height="72" fill="{_PAPER}" stroke="{_LINE}"/>',
        _text(16, 30, title, size=14, fill=_INK, weight="600"),
        _text(16, 52, message, size=12, fill=_MUTED),
        "</svg>",
    ]
    return "".join(lines)


def _open(title: str, height: int) -> str:
    label = _xml(title)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{height}" '
        f'viewBox="0 0 {_WIDTH} {height}" role="img" aria-label="{label}">'
        f"<title>{label}</title>"
    )


def _text(x: int, y: int, value: str, *, size: int, fill: str, weight: str = "400") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{_FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{_xml(value)}</text>'
    )


def _legend(left: int) -> str:
    return (
        f'<circle cx="{left}" cy="36" r="4" fill="{_P50}"/>'
        f"{_text(left + 8, 40, 'p50', size=11, fill=_MUTED)}"
        f'<circle cx="{left + 48}" cy="36" r="4" fill="{_P95}"/>'
        f"{_text(left + 56, 40, 'p95', size=11, fill=_MUTED)}"
        f'<circle cx="{left + 96}" cy="36" r="4" fill="{_P99}"/>'
        f"{_text(left + 104, 40, 'p99', size=11, fill=_MUTED)}"
        f"{_text(left + _PLOT_W - 24, 40, 'ms', size=11, fill=_MUTED)}"
    )


def _dot(left: int, width: int, xmax: float, value: float, y: int, color: str, name: str) -> str:
    x = left + int(round(value / xmax * width))
    return (
        f'<circle cx="{x}" cy="{y}" r="4" fill="{color}">'
        f"<title>{_xml(name)} {_xml(f'{value:.3f}')} ms</title></circle>"
    )


def _bar_px(amount: Decimal, peak: Decimal, width: int) -> int:
    pixels = int((amount / peak) * Decimal(width))
    return max(pixels, 1)


def _short(value: str, limit: int = 36) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _xml(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})
