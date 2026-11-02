# SPDX-License-Identifier: Apache-2.0
"""Hash identifiers before they are written into a report.

The salt stays in memory for the run. It is not written next to the tokens,
so a later report of the same file uses different tokens.
"""

from __future__ import annotations

from dataclasses import replace

from tracetally.analysis.engine import Analysis
from tracetally.analysis.summary import GroupSummary, Summary
from tracetally.core.hashing import blake2b16
from tracetally.core.schema import Finding, SkippedCheck
from tracetally.core.store import TraceStore


class Replacer:
    """Map known identifiers to stable ``redacted:`` tokens for one salt."""

    def __init__(self, salt: str) -> None:
        self._salt = salt
        self._tokens: dict[str, str] = {}
        self._needles: list[tuple[str, str]] = []

    def add(self, value: str | None, *, kind: str) -> None:
        if not value or not _needle_ok(value, kind):
            return
        self._needles.append((value, self.token(value)))

    def token(self, value: str) -> str:
        found = self._tokens.get(value)
        if found is None:
            digest = blake2b16(f"{self._salt}\0{value}".encode())
            found = f"redacted:{digest[:12]}"
            self._tokens[value] = found
        return found

    def finish(self) -> None:
        unique = dict(self._needles)
        self._needles = sorted(unique.items(), key=lambda item: (-len(item[0]), item[0]))

    def text(self, value: str) -> str:
        updated = value
        for ident, token in self._needles:
            if ident in updated:
                updated = updated.replace(ident, token)
        return updated

    def finding(self, item: Finding) -> Finding:
        evidence = []
        for row in item.evidence:
            evidence.append(
                row.model_copy(
                    update={
                        "trace_ids": [
                            self.token(trace_id) if trace_id else "" for trace_id in row.trace_ids
                        ],
                        "endpoint": None if row.endpoint is None else self.text(row.endpoint),
                        "detail": None if row.detail is None else self.text(row.detail),
                    }
                )
            )
        return item.model_copy(
            update={
                "id": self.text(item.id),
                "title": self.text(item.title),
                "recommendation": self.text(item.recommendation),
                "caveats": [self.text(caveat) for caveat in item.caveats],
                "evidence": evidence,
            }
        )

    def summary(self, summary: Summary) -> Summary:
        inputs: list[dict[str, object]] = []
        for item in summary.inputs:
            copied = dict(item)
            path = copied.get("path")
            if isinstance(path, str) and path:
                copied["path"] = self.token(path)
            inputs.append(copied)
        return replace(
            summary,
            by_model=[self._group(group) for group in summary.by_model],
            by_endpoint_model=[self._group(group) for group in summary.by_endpoint_model],
            inputs=inputs,
            warnings=[self.text(warning) for warning in summary.warnings],
        )

    def _group(self, group: GroupSummary) -> GroupSummary:
        endpoint = group.endpoint
        if endpoint is not None:
            endpoint = self.text(endpoint)
        return replace(
            group,
            endpoint=endpoint,
            provider=self.text(group.provider),
            model=self.text(group.model),
        )


def redact_analysis(analysis: Analysis, store: TraceStore, salt: str) -> Analysis:
    """Return a copy whose evidence ids and input paths are hashed."""
    replacer = Replacer(salt)
    for index in range(len(store)):
        replacer.add(store.trace_id[index], kind="id")
        replacer.add(store.user_id[index], kind="id")
        replacer.add(store.session_id[index], kind="id")
    for span in store.spans:
        replacer.add(span.trace_id, kind="id")
    for item in store.inputs:
        replacer.add(item.path, kind="path")
    for finding in analysis.findings:
        for row in finding.evidence:
            for trace_id in row.trace_ids:
                replacer.add(trace_id, kind="id")
    replacer.finish()
    skipped = [
        SkippedCheck(check=item.check, reason=replacer.text(item.reason))
        for item in analysis.skipped
    ]
    return replace(
        analysis,
        summary=replacer.summary(analysis.summary),
        findings=[replacer.finding(item) for item in analysis.findings],
        skipped=skipped,
        monthly_caveats=[replacer.text(item) for item in analysis.monthly_caveats],
    )


def _needle_ok(value: str, kind: str) -> bool:
    """Skip tiny strings so redaction cannot rewrite ordinary numbers."""
    if kind == "path":
        return len(value) >= 4 and ("/" in value or "\\" in value or "." in value)
    return len(value) >= 8
