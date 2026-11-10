# SPDX-License-Identifier: Apache-2.0
"""One static dashboard file. The script is inline and hashed into the CSP.

No remote asset. The payload lives in a JSON script. The page script builds
the DOM with ``textContent`` only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

_STYLE = """\
:root { color-scheme: light; }
body {
  margin: 0;
  font: 16px/1.45 ui-sans-serif, system-ui, sans-serif;
  color: #1c1915;
  background: #f4f0e8;
}
main { max-width: 44rem; margin: 0 auto; padding: 2rem 1.25rem 3rem; }
h1 { font-size: 1.6rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.05rem; margin: 0 0 0.35rem; }
p, li { margin: 0.2rem 0; }
.muted { color: #5c564c; }
section {
  margin: 0.85rem 0;
  padding: 0.75rem 0.9rem;
  background: #fffdf8;
  border: 1px solid #d9d2c5;
}
footer { margin-top: 1.5rem; }
a { color: #8c3a2f; }
"""

# The executable script. Keep this free of HTML parsers' end-tag sequence.
_SCRIPT = """\
(function () {
  var dataNode = document.getElementById("tracetally-data");
  var raw = dataNode ? dataNode.textContent : "";
  var data = {};
  try {
    data = JSON.parse(raw || "{}");
  } catch (err) {
    data = { findings: [], warnings: ["dashboard data could not be read"] };
  }
  var app = document.getElementById("app");
  if (!app) {
    return;
  }
  function add(parent, tag, value) {
    var node = document.createElement(tag);
    node.textContent = value == null ? "" : String(value);
    parent.appendChild(node);
    return node;
  }
  add(app, "h1", "tracetally");
  var summary = data.summary || {};
  var totals = data.totals || {};
  var calls = totals.calls == null ? "" : totals.calls;
  add(app, "p", "Calls " + calls);
  var meta = document.createElement("p");
  meta.className = "muted";
  meta.textContent = [
    "Prices " + (data.price_source || ""),
    data.price_url || "",
    "retrieved " + (data.price_table_retrieved_on || ""),
    "sha256 " + (data.price_sha256 || ""),
    "currency " + (data.currency || ""),
    "fx " + (data.fx_rate || "")
  ].join(" · ");
  app.appendChild(meta);
  var findings = data.findings || [];
  if (!findings.length) {
    add(app, "p", "No findings.");
  }
  findings.forEach(function (item, index) {
    var card = document.createElement("section");
    var title = (index + 1) + ". " + (item.title || "");
    if (item.what_if) {
      title += " (what-if)";
    }
    add(card, "h2", title);
    add(card, "p", item.cost_label || "");
    add(card, "p", item.recommendation || "");
    var evidence = item.evidence || [];
    evidence.forEach(function (row) {
      add(card, "p", (row.endpoint || "") + " " + (row.detail || ""));
    });
    app.appendChild(card);
  });
  var models = (summary.by_model || []);
  models.forEach(function (row) {
    var cost = row.cost_usd == null ? "" : row.cost_usd;
    var label = (row.provider || "") + " / " + (row.model || "");
    add(app, "p", label + " " + cost);
  });
  var warnings = data.warnings || [];
  warnings.forEach(function (warning) {
    add(app, "p", warning);
  });
  var footer = document.createElement("footer");
  var link = document.createElement("a");
  link.textContent = data.cta_text || "";
  var href = String(data.cta_url || "");
  if (href.indexOf("https://") === 0 || href.indexOf("http://") === 0) {
    link.setAttribute("href", href);
  }
  footer.appendChild(link);
  app.appendChild(footer);
})();
"""


def embed_json(document: dict[str, Any]) -> str:
    """JSON for a ``<script type="application/json">`` element.

    ``<``, ``>``, ``&``, U+2028, and U+2029 become ``\\u`` escapes so a value
    cannot close the script element or introduce an HTML tag.
    """
    text = json.dumps(document, ensure_ascii=False, indent=2)
    return (
        text.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def csp_source_hash(content: str) -> str:
    """CSP sha256 source hash of an inline style or script body."""
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def render_dashboard(document: dict[str, Any]) -> str:
    """Return one HTML file. The CSP allows the inline script by hash only."""
    lowered = _STYLE.lower()
    banned = ("</style", "url(", "@import", "http://", "https://")
    if any(token in lowered for token in banned):
        raise ValueError("dashboard stylesheet must not load external resources")
    if "</script" in _SCRIPT.lower():
        raise ValueError("dashboard script must not close the script element")
    if "innerHTML" in _SCRIPT or "insertAdjacentHTML" in _SCRIPT:
        raise ValueError("dashboard script must build the page with textContent")
    payload = embed_json(document)
    script_hash = csp_source_hash(_SCRIPT)
    style_hash = csp_source_hash(_STYLE)
    policy = (
        "default-src 'none'; "
        f"script-src 'sha256-{script_hash}'; "
        f"style-src 'sha256-{style_hash}'; "
        "base-uri 'none'; form-action 'none'"
    )
    if "unsafe-inline" in policy:
        raise ValueError("dashboard CSP must not allow unsafe-inline")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n'
        "<title>tracetally</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        '<main id="app"></main>\n'
        '<script type="application/json" id="tracetally-data">'
        f"{payload}</script>\n"
        f"<script>{_SCRIPT}</script>\n"
        "</body>\n"
        "</html>\n"
    )
