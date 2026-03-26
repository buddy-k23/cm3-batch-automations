"""Suite-level HTML summary report generator."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


class SuiteReporter:
    """Generates a consolidated HTML summary report for a test suite run."""

    def generate(
        self,
        suite_name: str,
        results: list[dict],
        output_path: str,
        run_id: str | None = None,
        environment: str = "dev",
    ) -> str:
        """Generate HTML summary report.

        Args:
            suite_name: Human-readable name of the test suite.
            results: List of result dicts as returned by run_tests_command.
            output_path: Filesystem path where the HTML file will be written.
            run_id: Optional unique identifier for this run.
            environment: Environment name (e.g. "dev", "prod").

        Returns:
            The output_path where the file was written.
        """
        html = self._render(suite_name, results, run_id, environment)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_overall_status(self, results: list[dict]) -> str:
        """Compute the overall suite status badge.

        Rules:
        - PASS  : all tests are PASS or SKIPPED
        - FAIL  : any test is ERROR, OR any test is FAIL and at least one is also ERROR
        - PARTIAL: mix of PASS and FAIL with no ERRORs
        """
        if not results:
            return "PASS"

        statuses = {r.get("status", "ERROR") for r in results}
        has_fail = "FAIL" in statuses
        has_error = "ERROR" in statuses
        has_pass = "PASS" in statuses

        if has_error:
            return "FAIL"
        if has_fail and has_pass:
            return "PARTIAL"
        if has_fail:
            return "FAIL"
        return "PASS"

    def _count_statuses(self, results: list[dict]) -> dict[str, int]:
        counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIPPED": 0}
        for r in results:
            s = r.get("status", "ERROR")
            if s in counts:
                counts[s] += 1
            else:
                counts["ERROR"] += 1
        return counts

    def _status_badge_style(self, status: str) -> str:
        colors = {
            "PASS": ("background:#27ae60; color:#fff;",),
            "FAIL": ("background:#c0392b; color:#fff;",),
            "PARTIAL": ("background:#e67e22; color:#fff;",),
            "ERROR": ("background:#922b21; color:#fff;",),
            "SKIPPED": ("background:#7f8c8d; color:#fff;",),
        }
        return colors.get(status, colors["ERROR"])[0]

    def _status_cell_style(self, status: str) -> str:
        bg = {
            "PASS": "#d5f5e3",
            "FAIL": "#fadbd8",
            "ERROR": "#922b21",
            "SKIPPED": "#eaecee",
        }
        color = {
            "PASS": "#1e8449",
            "FAIL": "#922b21",
            "ERROR": "#fff",
            "SKIPPED": "#5d6d7e",
        }
        b = bg.get(status, "#eee")
        c = color.get(status, "#333")
        return f"background:{b}; color:{c}; font-weight:700; text-align:center;"

    def _escape(self, text: Any) -> str:
        """Minimal HTML escaping."""
        if text is None:
            return ""
        s = str(text)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        return s

    def _render_rows(self, results: list[dict]) -> str:
        if not results:
            return (
                '<tr><td colspan="6" style="text-align:center; color:#7f8c8d; padding:24px;">'
                "No tests were run.</td></tr>"
            )
        rows = []
        for r in results:
            status = r.get("status", "ERROR")
            name = self._escape(r.get("name", ""))
            test_type = self._escape(r.get("type", ""))
            duration = r.get("duration_seconds")
            dur_str = f"{duration:.2f}s" if duration is not None else "&mdash;"
            message = self._escape(r.get("message") or r.get("detail") or "")
            report_url = r.get("report_url") or r.get("report_path")
            if report_url:
                report_cell = (
                    f'<a href="{self._escape(report_url)}" '
                    f'style="color:#2980b9;">View</a>'
                )
            else:
                report_cell = "&mdash;"
            cell_style = self._status_cell_style(status)
            rows.append(
                f"<tr>"
                f'<td style="padding:8px 12px;">{name}</td>'
                f'<td style="padding:8px 12px; text-align:center;">{test_type}</td>'
                f'<td style="{cell_style} padding:8px 12px;">{self._escape(status)}</td>'
                f'<td style="padding:8px 12px; text-align:center;">{dur_str}</td>'
                f'<td style="padding:8px 12px;">{message}</td>'
                f'<td style="padding:8px 12px; text-align:center;">{report_cell}</td>'
                f"</tr>"
            )
        return "\n".join(rows)

    def _render(
        self,
        suite_name: str,
        results: list[dict],
        run_id: str | None,
        environment: str,
    ) -> str:
        overall = self._compute_overall_status(results)
        counts = self._count_statuses(results)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        badge_style = self._status_badge_style(overall)
        rows_html = self._render_rows(results)

        run_id_display = self._escape(run_id) if run_id else "&mdash;"
        suite_name_display = self._escape(suite_name)
        env_display = self._escape(environment)

        counts_bar = (
            f'<span style="margin-right:18px;"><strong style="color:#27ae60;">'
            f'{counts["PASS"]}</strong> PASSED</span>'
            f'<span style="margin-right:18px;"><strong style="color:#c0392b;">'
            f'{counts["FAIL"]}</strong> FAILED</span>'
            f'<span style="margin-right:18px;"><strong style="color:#922b21;">'
            f'{counts["ERROR"]}</strong> ERRORS</span>'
            f'<span><strong style="color:#7f8c8d;">'
            f'{counts["SKIPPED"]}</strong> SKIPPED</span>'
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Suite Report: {suite_name_display}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #f5f7fa;
            color: #2d3748;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px 16px;
        }}
        .header {{
            background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
            color: white;
            padding: 32px 40px;
            border-radius: 12px;
            margin-bottom: 24px;
        }}
        .header h1 {{
            font-size: 1.8em;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        .header .meta {{
            font-size: 0.9em;
            opacity: 0.85;
        }}
        .header .meta span {{
            margin-right: 24px;
        }}
        .badge-section {{
            display: flex;
            align-items: center;
            gap: 20px;
            margin-bottom: 20px;
        }}
        .overall-badge {{
            display: inline-block;
            padding: 14px 36px;
            border-radius: 8px;
            font-size: 2em;
            font-weight: 800;
            letter-spacing: 0.08em;
            {badge_style}
        }}
        .counts-bar {{
            font-size: 1.05em;
            background: #fff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px 20px;
        }}
        .card {{
            background: #fff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 0;
            margin-bottom: 24px;
            overflow: hidden;
        }}
        .card-title {{
            background: #edf2f7;
            padding: 12px 20px;
            font-weight: 700;
            font-size: 1em;
            color: #2d3748;
            border-bottom: 1px solid #e2e8f0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        thead th {{
            background: #2c3e50;
            color: #fff;
            padding: 10px 12px;
            text-align: left;
            font-size: 0.88em;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        tbody tr:nth-child(even) {{ background: #f9fafb; }}
        tbody tr:hover {{ background: #edf2f7; }}
        tbody td {{ border-bottom: 1px solid #e2e8f0; font-size: 0.92em; }}
        .footer {{
            text-align: center;
            color: #a0aec0;
            font-size: 0.82em;
            margin-top: 32px;
            padding-top: 16px;
            border-top: 1px solid #e2e8f0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Suite Report: {suite_name_display}</h1>
            <div class="meta">
                <span><strong>Environment:</strong> {env_display}</span>
                <span><strong>Run ID:</strong> {run_id_display}</span>
                <span><strong>Generated:</strong> {timestamp}</span>
            </div>
        </div>

        <div class="badge-section">
            <div class="overall-badge">{self._escape(overall)}</div>
            <div class="counts-bar">{counts_bar}</div>
        </div>

        <div class="card">
            <div class="card-title">Test Results</div>
            <table>
                <thead>
                    <tr>
                        <th>Test Name</th>
                        <th style="text-align:center;">Type</th>
                        <th style="text-align:center;">Status</th>
                        <th style="text-align:center;">Duration</th>
                        <th>Message</th>
                        <th style="text-align:center;">Report</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="footer">Generated by Valdo</div>
    </div>
</body>
</html>"""
