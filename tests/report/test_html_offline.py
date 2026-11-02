# SPDX-License-Identifier: Apache-2.0
"""The HTML report is one file. It must not load anything external."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from tracetally.report.links import ALLOWED_HREFS, SVG_NAMESPACE

_RESOURCE_TAGS = frozenset(
    {"script", "img", "iframe", "object", "embed", "audio", "video", "source", "track", "link"}
)
_URL_ATTRS = frozenset({"src", "srcset", "poster", "data", "action", "formaction", "xlink:href"})


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_data(self, data: str) -> None:
        if not self._in_style:
            return
        lowered = data.lower()
        if (
            "url(" in lowered
            or "@import" in lowered
            or "http://" in lowered
            or "https://" in lowered
        ):
            self.errors.append("stylesheet refers to an external resource")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name == "style":
            self._in_style = True
        if name in _RESOURCE_TAGS:
            self.errors.append(f"<{name}>")
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        if "http-equiv" in attr_map:
            self.errors.append("http-equiv")
        for key, value in attr_map.items():
            lowered = value.strip().lower()
            if key.startswith("on"):
                self.errors.append(f"event handler {key}")
            if "javascript:" in lowered:
                self.errors.append("javascript URL")
            if key in _URL_ATTRS:
                self.errors.append(f"{key}={value}")
            if key == "style" and ("url(" in lowered or "@import" in lowered):
                self.errors.append("inline style url")
            if key == "href" and not _href_ok(name, value):
                self.errors.append(f"href={value}")
            if key == "xmlns" and value != SVG_NAMESPACE:
                self.errors.append(f"xmlns={value}")


def assert_no_external_loads(html: str) -> None:
    """Fail when the report would fetch a script, image, font, or stylesheet."""
    if re.search(r"<\s*script", html, flags=re.IGNORECASE):
        raise AssertionError("report contains a script tag")
    parser = _Collector()
    parser.feed(html)
    parser.close()
    if parser.errors:
        raise AssertionError("external resource loads: " + "; ".join(parser.errors))


def _href_ok(tag: str, value: str) -> bool:
    cleaned = value.strip()
    if tag == "a" and cleaned in ALLOWED_HREFS:
        return True
    return cleaned.startswith("#") and "://" not in cleaned
