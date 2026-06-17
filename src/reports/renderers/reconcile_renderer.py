"""HTML report generator for reconciliation verdicts (S23-3, #445).

This is a *thin* renderer for the field-level reconcile verdict produced by
:func:`src.services.reconcile_service.reconcile_mapping_service` and the
aggregate produced by :func:`src.services.reconcile_all_service.reconcile_all_service`.

It deliberately does **not** build a new HTML engine: it reuses the established
report look (the same self-apple-system font stack, ``#f5f7fa`` page, gradient
header, card / section, and severity-badge styling) shared by
:class:`~src.reports.renderers.suite_renderer.SuiteReporter` and
:class:`~src.reports.renderers.validation_renderer.ValidationReporter`, factored
here into a small shared shell so the reconcile report visually matches the rest
of Valdo's reports without duplicating the whole validation CSS.

Two public entry points, mirroring the two services:

* :meth:`ReconcileReporter.generate` — a single-mapping verdict (header with
  status + summary counts, then sections for errors, type mismatches,
  advisories, and unmapped-required fields).
* :meth:`ReconcileReporter.generate_all` — a ``reconcile-all`` aggregate
  (header with totals, a per-mapping results table, and — when present — the
  baseline drift block: added / removed / changed mappings).

PII handling matches the other renderers: ``suppress_pii=True`` (default)
redacts embedded field values from message strings before they reach the HTML.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

__all__ = ["ReconcileReporter"]


# Status -> (badge background, badge text colour). Reuses the suite report's
# green/amber/red palette so reconcile verdicts read consistently with the
# rest of Valdo's HTML reports.
_STATUS_STYLES: Dict[str, str] = {
    "clean": "background:#27ae60; color:#fff;",
    "advisories": "background:#e67e22; color:#fff;",
    "mismatch": "background:#c0392b; color:#fff;",
    "error": "background:#922b21; color:#fff;",
}

# PII-value patterns mirrored from ValidationReporter so embedded raw values in
# engine messages (e.g. ``value 'XYZ' ...``) are redacted consistently.
_PII_VALUE_PATTERNS = [
    re.compile(r"(value\s+)['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"(got\s+)['\"]([^'\"]+)['\"]", re.IGNORECASE),
]


class ReconcileReporter:
    """Render reconcile / reconcile-all verdicts to a self-contained HTML file."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        verdict: Dict[str, Any],
        output_path: str,
        *,
        suppress_pii: bool = True,
    ) -> str:
        """Render a single-mapping reconcile verdict to HTML.

        Args:
            verdict: The verdict dict from
                :func:`src.services.reconcile_service.reconcile_mapping_service`
                (keys: ``status``, ``mapping_name``, ``table``, ``summary``,
                ``errors``, ``mismatches``, ``advisories``, ``unmapped_required``).
            output_path: Filesystem path to write the HTML report to.
            suppress_pii: When True (default) redact embedded field values from
                error / mismatch / advisory messages.

        Returns:
            The ``output_path`` the report was written to.
        """
        self._suppress_pii = suppress_pii
        html = self._render_single(verdict)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path

    def generate_all(
        self,
        aggregate: Dict[str, Any],
        output_path: str,
        *,
        suppress_pii: bool = True,
    ) -> str:
        """Render a ``reconcile-all`` aggregate (+ optional drift) to HTML.

        Args:
            aggregate: The aggregate dict from
                :func:`src.services.reconcile_all_service.reconcile_all_service`
                (keys: ``total_mappings``, ``valid_mappings``,
                ``invalid_mappings``, ``total_errors``, ``total_warnings``,
                ``results``, and optionally ``drift``).
            output_path: Filesystem path to write the HTML report to.
            suppress_pii: When True (default) redact embedded field values from
                per-mapping error / warning messages.

        Returns:
            The ``output_path`` the report was written to.
        """
        self._suppress_pii = suppress_pii
        html = self._render_aggregate(aggregate)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path

    # ------------------------------------------------------------------
    # Shared HTML shell (reused look, not a new engine)
    # ------------------------------------------------------------------

    def _shell(self, title: str, body: str) -> str:
        """Wrap *body* in the shared report HTML shell (head + container).

        The CSS here is the established Valdo report look (same font stack,
        ``#f5f7fa`` page, gradient header, white cards, severity badges) shared
        with the suite / validation renderers — kept minimal and scoped to what
        the reconcile report needs.

        Args:
            title: The document title and ``<h1>`` text.
            body: Pre-rendered inner HTML (header + sections).

        Returns:
            A complete, self-contained HTML document string.
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self._escape(title)}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #f5f7fa;
            color: #2d3748;
            line-height: 1.6;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px; }}
        .header {{
            background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
            color: white; padding: 32px 40px; border-radius: 12px; margin-bottom: 24px;
        }}
        .header h1 {{ font-size: 1.8em; font-weight: 700; margin-bottom: 8px; }}
        .header .meta {{ font-size: 0.9em; opacity: 0.85; }}
        .header .meta span {{ margin-right: 24px; }}
        .badge-section {{ display: flex; align-items: center; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
        .status-badge {{
            display: inline-block; padding: 12px 32px; border-radius: 8px;
            font-size: 1.4em; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase;
        }}
        .counts-bar {{
            font-size: 1.05em; background: #fff; border: 1px solid #e2e8f0;
            border-radius: 8px; padding: 12px 20px;
        }}
        .counts-bar span {{ margin-right: 18px; }}
        .card {{
            background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
            margin-bottom: 24px; overflow: hidden;
        }}
        .card-title {{
            background: #edf2f7; padding: 12px 20px; font-weight: 700;
            font-size: 1em; color: #2d3748; border-bottom: 1px solid #e2e8f0;
        }}
        .card-body {{ padding: 16px 20px; }}
        ul.issues {{ list-style: none; }}
        ul.issues li {{
            padding: 10px 14px; margin: 8px 0; border-left: 4px solid;
            border-radius: 6px; font-size: 0.95em;
        }}
        li.sev-error {{ background: #fff5f5; border-color: #c53030; }}
        li.sev-mismatch {{ background: #fffaf0; border-color: #c05621; }}
        li.sev-advisory {{ background: #ebf8ff; border-color: #2b6cb0; }}
        li.sev-unmapped {{ background: #faf5ff; border-color: #6b46c1; }}
        code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead th {{
            background: #2c3e50; color: #fff; padding: 10px 12px; text-align: left;
            font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.04em;
        }}
        tbody tr:nth-child(even) {{ background: #f9fafb; }}
        tbody td {{ border-bottom: 1px solid #e2e8f0; font-size: 0.92em; padding: 8px 12px; }}
        .ok {{ color: #1e8449; font-weight: 700; }}
        .bad {{ color: #922b21; font-weight: 700; }}
        .empty {{ color: #7f8c8d; padding: 8px 0; }}
        .footer {{
            text-align: center; color: #a0aec0; font-size: 0.82em;
            margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0;
        }}
    </style>
</head>
<body>
    <div class="container">
        {body}
        <div class="footer">Generated by Valdo &mdash; Reconciliation Report</div>
    </div>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Single-mapping verdict
    # ------------------------------------------------------------------

    def _render_single(self, verdict: Dict[str, Any]) -> str:
        """Render the inner body for a single-mapping verdict."""
        status = str(verdict.get("status", "error"))
        mapping_name = self._escape(verdict.get("mapping_name", "Unknown mapping"))
        table = self._escape(verdict.get("table") or "N/A")
        adapter = self._escape(verdict.get("db_adapter") or "N/A")
        summary = verdict.get("summary", {}) or {}
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        badge_style = _STATUS_STYLES.get(status, _STATUS_STYLES["error"])

        counts_bar = (
            f'<span><strong class="bad">{int(summary.get("error_count", 0))}</strong> ERRORS</span>'
            f'<span><strong>{int(summary.get("mismatch_count", 0))}</strong> MISMATCHES</span>'
            f'<span><strong>{int(summary.get("advisory_count", 0))}</strong> ADVISORIES</span>'
            f'<span><strong>{int(summary.get("mapped_columns", 0))}</strong> MAPPED</span>'
            f'<span><strong>{int(summary.get("database_columns", 0))}</strong> DB COLUMNS</span>'
        )

        header = f"""
        <div class="header">
            <h1>Reconciliation Report: {mapping_name}</h1>
            <div class="meta">
                <span><strong>Table:</strong> {table}</span>
                <span><strong>Adapter:</strong> {adapter}</span>
                <span><strong>Generated:</strong> {timestamp}</span>
            </div>
        </div>
        <div class="badge-section">
            <div class="status-badge" style="{badge_style}">{self._escape(status)}</div>
            <div class="counts-bar">{counts_bar}</div>
        </div>
        """

        sections = (
            self._issue_card("Errors", verdict.get("errors"), "sev-error")
            + self._issue_card("Type Mismatches", verdict.get("mismatches"), "sev-mismatch")
            + self._issue_card("Advisories", verdict.get("advisories"), "sev-advisory")
            + self._issue_card(
                "Unmapped Required Fields",
                verdict.get("unmapped_required"),
                "sev-unmapped",
                redact=False,
            )
        )

        return self._shell(f"Reconciliation Report: {verdict.get('mapping_name', '')}", header + sections)

    def _issue_card(
        self,
        title: str,
        items: Optional[List[Any]],
        severity_class: str,
        *,
        redact: bool = True,
    ) -> str:
        """Render one labelled card listing message strings.

        Args:
            title: The card title (rendered with the item count).
            items: The list of message strings (may be None / empty).
            severity_class: CSS class controlling the left-border colour.
            redact: When True (default), apply PII redaction to each message.

        Returns:
            HTML for one ``.card`` block.
        """
        items = list(items or [])
        if not items:
            inner = '<div class="empty">None.</div>'
        else:
            lis = "".join(
                f'<li class="{severity_class}">'
                f"{self._escape(self._redact(str(item)) if redact else str(item))}</li>"
                for item in items
            )
            inner = f'<ul class="issues">{lis}</ul>'
        return f"""
        <div class="card">
            <div class="card-title">{self._escape(title)} ({len(items)})</div>
            <div class="card-body">{inner}</div>
        </div>
        """

    # ------------------------------------------------------------------
    # reconcile-all aggregate
    # ------------------------------------------------------------------

    def _render_aggregate(self, aggregate: Dict[str, Any]) -> str:
        """Render the inner body for a reconcile-all aggregate (+ drift)."""
        total = int(aggregate.get("total_mappings", 0))
        valid = int(aggregate.get("valid_mappings", 0))
        invalid = int(aggregate.get("invalid_mappings", 0))
        total_errors = int(aggregate.get("total_errors", 0))
        total_warnings = int(aggregate.get("total_warnings", 0))
        results = aggregate.get("results", []) or []
        drift = aggregate.get("drift")
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        overall = "clean" if invalid == 0 else "error"
        badge_style = _STATUS_STYLES.get(overall, _STATUS_STYLES["error"])
        badge_text = "ALL VALID" if invalid == 0 else f"{invalid} INVALID"

        counts_bar = (
            f'<span><strong>{total}</strong> MAPPINGS</span>'
            f'<span><strong class="ok">{valid}</strong> VALID</span>'
            f'<span><strong class="bad">{invalid}</strong> INVALID</span>'
            f'<span><strong>{total_errors}</strong> ERRORS</span>'
            f'<span><strong>{total_warnings}</strong> WARNINGS</span>'
        )

        header = f"""
        <div class="header">
            <h1>Reconcile-All Report</h1>
            <div class="meta">
                <span><strong>Generated:</strong> {timestamp}</span>
            </div>
        </div>
        <div class="badge-section">
            <div class="status-badge" style="{badge_style}">{self._escape(badge_text)}</div>
            <div class="counts-bar">{counts_bar}</div>
        </div>
        """

        return self._shell(
            "Reconcile-All Report",
            header + self._results_table(results) + self._drift_card(drift),
        )

    def _results_table(self, results: List[Dict[str, Any]]) -> str:
        """Render the per-mapping aggregate results table."""
        if not results:
            rows = '<tr><td colspan="4" class="empty">No mapping files processed.</td></tr>'
        else:
            rows_html = []
            for r in results:
                name = self._escape(r.get("mapping_name") or r.get("mapping_file") or "—")
                valid = bool(r.get("valid", False))
                status_cell = (
                    '<span class="ok">VALID</span>' if valid else '<span class="bad">INVALID</span>'
                )
                errors = int(r.get("error_count", len(r.get("errors", []) or [])))
                warnings = int(r.get("warning_count", len(r.get("warnings", []) or [])))
                rows_html.append(
                    f"<tr><td>{name}</td><td>{status_cell}</td>"
                    f"<td>{errors}</td><td>{warnings}</td></tr>"
                )
            rows = "".join(rows_html)

        return f"""
        <div class="card">
            <div class="card-title">Per-Mapping Results</div>
            <table>
                <thead>
                    <tr><th>Mapping</th><th>Status</th><th>Errors</th><th>Warnings</th></tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        """

    def _drift_card(self, drift: Optional[Dict[str, Any]]) -> str:
        """Render the baseline drift block, or empty string when no baseline."""
        if not drift:
            return ""

        added = drift.get("added_files", []) or []
        removed = drift.get("removed_files", []) or []
        changed = drift.get("changed", []) or []
        new_errors = int(drift.get("new_errors", 0))
        new_warnings = int(drift.get("new_warnings", 0))
        baseline = self._escape(drift.get("baseline") or "")

        if changed:
            changed_rows = "".join(
                f"<tr><td>{self._escape(c.get('mapping_file', '—'))}</td>"
                f"<td>{int(c.get('old_errors', 0))} &rarr; {int(c.get('new_errors', 0))} "
                f"({self._signed(c.get('delta_errors', 0))})</td>"
                f"<td>{int(c.get('old_warnings', 0))} &rarr; {int(c.get('new_warnings', 0))} "
                f"({self._signed(c.get('delta_warnings', 0))})</td></tr>"
                for c in changed
            )
            changed_table = f"""
            <table>
                <thead><tr><th>Mapping</th><th>Errors</th><th>Warnings</th></tr></thead>
                <tbody>{changed_rows}</tbody>
            </table>
            """
        else:
            changed_table = '<div class="empty">No per-mapping count changes.</div>'

        summary_bar = (
            f'<span><strong class="ok">{len(added)}</strong> ADDED</span>'
            f'<span><strong>{len(removed)}</strong> REMOVED</span>'
            f'<span><strong>{len(changed)}</strong> CHANGED</span>'
            f'<span><strong class="bad">{new_errors}</strong> NEW ERRORS</span>'
            f'<span><strong>{new_warnings}</strong> NEW WARNINGS</span>'
        )

        return f"""
        <div class="card">
            <div class="card-title">Baseline Drift &mdash; vs {baseline}</div>
            <div class="card-body">
                <div class="counts-bar" style="margin-bottom:14px;">{summary_bar}</div>
                {changed_table}
            </div>
        </div>
        """

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _signed(self, value: Any) -> str:
        """Format a delta with an explicit sign (e.g. ``+2``, ``-1``, ``0``)."""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return "0"
        return f"+{n}" if n > 0 else str(n)

    def _redact(self, message: str) -> str:
        """Redact PII-like embedded values from a message when enabled.

        Args:
            message: The original engine message string.

        Returns:
            The message with embedded values replaced by ``[REDACTED]`` when
            ``self._suppress_pii`` is True; otherwise unchanged.
        """
        if not getattr(self, "_suppress_pii", True):
            return message
        text = message
        for pattern in _PII_VALUE_PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
        return text

    def _escape(self, text: Any) -> str:
        """Minimal HTML escaping (mirrors SuiteReporter._escape)."""
        if text is None:
            return ""
        s = str(text)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        return s
