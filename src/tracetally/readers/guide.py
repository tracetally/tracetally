# SPDX-License-Identifier: Apache-2.0
"""Export guide printed when a scan finds no usage file.

The body is the PM copy. The closing line uses the CTA constants.
"""

from __future__ import annotations

from tracetally._cta import AUDIT_CTA_TEXT, AUDIT_CTA_URL

_GUIDE_LINES: tuple[str, ...] = (
    "No LLM usage file found",
    "",
    "tracetally looks under the current folder for JSONL, OpenTelemetry, Langfuse,",
    "or OpenAI usage CSV. You can also pass a path:  tracetally run ./path",
    "",
    "OpenAI usage CSV",
    "  Sign in at platform.openai.com, open Usage, set the date range, Export",
    "  Activity or Cost data as CSV, then Download.",
    "  https://help.openai.com/en/articles/"
    "20001072-exporting-monthly-usage-details-from-the-api-usage-dashboard",
    "Langfuse",
    "  On Traces or Observations, Export as CSV or JSON; download from",
    "  Project Settings > Exports when Completed.",
    "  https://langfuse.com/docs/api-and-data-platform/features/export-from-ui",
    "OpenTelemetry / OpenLLMetry",
    "  Write OTLP JSON traces to a file (JSON lines of TracesData). Collector",
    "  file exporter: format: json, no compression.",
    "  https://opentelemetry.io/docs/specs/otel/protocol/file-exporter/",
    "  https://github.com/open-telemetry/opentelemetry-collector-contrib/"
    "blob/main/exporter/fileexporter/README.md",
    "  OpenLLMetry emits OTLP HTTP. Set TRACELOOP_BASE_URL to a collector, then",
    "  write traces to a file as above.",
    "  https://www.traceloop.com/docs/openllmetry/integrations/otel-collector",
    "JSONL",
    "  Native format: one JSON object per line with model, start_time,",
    "  input_tokens, and output_tokens. See the README for fields.",
)


def no_export_text() -> str:
    """Guide plus the audit link. Exit 0. No report directory is written."""
    body = "\n".join(_GUIDE_LINES)
    return f"{body}\n\n{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}"
