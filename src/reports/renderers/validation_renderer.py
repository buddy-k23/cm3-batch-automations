"""Validation report generator for creating HTML validation reports."""

import csv
import json
import re
from typing import Dict, Any
from datetime import datetime
from pathlib import Path

_CHART_JS_PATH = Path(__file__).parent.parent / "static" / "chart.umd.min.js"
if not _CHART_JS_PATH.exists():
    raise FileNotFoundError(
        f"Chart.js bundle not found: {_CHART_JS_PATH}. "
        "If developing locally, pull the latest commits. "
        "If running from an installed package, re-install from the latest release."
    )
_CHART_JS_INLINE = _CHART_JS_PATH.read_text(encoding="utf-8")


class ValidationReporter:
    """Generates HTML validation reports with charts and detailed analysis."""

    ERROR_DISPLAY_LIMIT = 10
    WARNING_DISPLAY_LIMIT = 100

    # Regex patterns for detecting PII-like values embedded in error messages.
    # Matches patterns like: value 'SOMEVALUE' failed, value "SOMEVALUE" is invalid, etc.
    _PII_VALUE_PATTERNS = [
        re.compile(r"(value\s+)['\"]([^'\"]+)['\"]", re.IGNORECASE),
        re.compile(r"(value\s+)(\S+)(\s+(?:failed|is invalid|does not match|not in|violates|expected))", re.IGNORECASE),
        re.compile(r"(got\s+)['\"]([^'\"]+)['\"]", re.IGNORECASE),
        re.compile(r"(got\s+)(\S+)(\s)", re.IGNORECASE),
    ]

    def generate(
        self,
        validation_results: Dict[str, Any],
        output_path: str,
        suppress_pii: bool = True,
    ) -> None:
        """Generate HTML validation report.

        Args:
            validation_results: Validation results from EnhancedFileValidator.
            output_path: Path to save HTML report.
            suppress_pii: When True (default), redact raw field values from
                error messages and affected-row details in the HTML report
                and CSV sidecars. Row numbers and error codes are preserved.
        """
        self._suppress_pii = suppress_pii
        html = self._generate_html(validation_results)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # Export full issue lists to CSV sidecars to keep HTML report concise.
        self._write_errors_csv(validation_results, output_path)
        self._write_warnings_csv(validation_results, output_path)

    # ------------------------------------------------------------------
    # PII redaction helpers
    # ------------------------------------------------------------------

    def _redact_message(self, message: str) -> str:
        """Redact PII-like values from an error/warning message string.

        Replaces embedded field values (e.g. ``value '123456789' failed regex``)
        with ``[REDACTED]`` while preserving the surrounding context so the
        error type remains understandable.

        Args:
            message: The original error or warning message.

        Returns:
            The message with values replaced by ``[REDACTED]`` when
            ``self._suppress_pii`` is True; otherwise the original message.
        """
        if not self._suppress_pii:
            return message
        text = message
        for pattern in self._PII_VALUE_PATTERNS:
            text = pattern.sub(
                lambda m: m.group(1) + "[REDACTED]" + (m.group(3) if m.lastindex >= 3 else ""),
                text,
            )
        return text

    def _redact_issue_text(self, issue_text: str) -> str:
        """Redact a plain-text issue string used in affected-row listings.

        Args:
            issue_text: A single issue description string.

        Returns:
            Redacted string when ``self._suppress_pii`` is True.
        """
        if not self._suppress_pii:
            return issue_text
        return self._redact_message(issue_text)

    def _sort_issues(self, issues):
        """Sort issues by row (numeric), then field, then code/severity."""
        def _key(item):
            if not isinstance(item, dict):
                return (1, float('inf'), '', '', '')
            row = item.get('row')
            row_missing = 1 if row is None else 0
            row_num = int(row) if isinstance(row, int) or (isinstance(row, str) and str(row).isdigit()) else float('inf')
            field = str(item.get('field') or '')
            code = str(item.get('code') or '')
            severity = str(item.get('severity') or '')
            return (row_missing, row_num, field, code, severity)

        return sorted(issues, key=_key)

    def _write_errors_csv(self, validation_results: Dict[str, Any], output_path: str) -> None:
        """Write all errors to a CSV sidecar next to the HTML report.

        The sidecar includes a ``source_row`` column so users can trace each
        error back to the physical line number in the source batch file.

        Args:
            validation_results: Validation results dict from EnhancedFileValidator.
            output_path: Path to the main HTML report (used to derive the CSV path).
        """
        errors = self._sort_issues(validation_results.get('errors', []))
        output = Path(output_path)
        csv_path = output.with_name(f"{output.stem}_errors.csv")

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['source_row', 'index', 'severity', 'message'])
            writer.writeheader()
            for idx, error in enumerate(errors, start=1):
                if isinstance(error, dict):
                    writer.writerow({
                        'source_row': error.get('source_row', error.get('row', '')),
                        'index': idx,
                        'severity': error.get('severity', 'error'),
                        'message': self._redact_message(error.get('message', '')),
                    })
                else:
                    writer.writerow({
                        'source_row': '',
                        'index': idx,
                        'severity': 'error',
                        'message': self._redact_message(str(error)),
                    })

    def _write_warnings_csv(self, validation_results: Dict[str, Any], output_path: str) -> None:
        """Write all warnings to a CSV sidecar next to the HTML report.

        The sidecar includes a ``source_row`` column so users can trace each
        warning back to the physical line number in the source batch file.

        Args:
            validation_results: Validation results dict from EnhancedFileValidator.
            output_path: Path to the main HTML report (used to derive the CSV path).
        """
        warnings = self._sort_issues(validation_results.get('warnings', []))
        output = Path(output_path)
        csv_path = output.with_name(f"{output.stem}_warnings.csv")

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['source_row', 'index', 'severity', 'message'])
            writer.writeheader()
            for idx, warning in enumerate(warnings, start=1):
                if isinstance(warning, dict):
                    writer.writerow({
                        'source_row': warning.get('source_row', warning.get('row', '')),
                        'index': idx,
                        'severity': warning.get('severity', 'warning'),
                        'message': self._redact_message(warning.get('message', '')),
                    })
                else:
                    writer.writerow({
                        'source_row': '',
                        'index': idx,
                        'severity': 'warning',
                        'message': self._redact_message(str(warning)),
                    })

    def _generate_html(self, results: Dict[str, Any]) -> str:
        """Generate complete HTML report."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>File Validation Report</title>
    <script>{_CHART_JS_INLINE}</script>
    <style>
        {self._get_css()}
    </style>
</head>
<body>
    <div class="container">
        {self._generate_header(results)}
        {self._generate_dashboard_bar(results)}
        {self._generate_full_metrics_details(results)}
        {self._generate_issues(results)}
        {self._generate_required_fields(results)}
        {self._wrap_collapsible_section('Field-Level Analysis', self._generate_field_analysis(results))}
        {self._wrap_collapsible_section('Date Field Analysis', self._generate_date_analysis(results))}
        {self._wrap_collapsible_section('Duplicate Analysis', self._generate_duplicate_analysis(results))}
        {self._wrap_collapsible_section('Business Rules', self._generate_business_rules(results))}
        {self._wrap_collapsible_section('Appendix', self._generate_appendix(results))}
        {self._generate_footer()}
    </div>
    <script>
        {self._generate_charts_js(results)}
    </script>
</body>
</html>"""

    def _get_css(self) -> str:
        """Get CSS styles for the report."""
        return """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #f5f7fa;
            color: #2d3748;
            line-height: 1.6;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 14px;
        }

        .dashboard-bar {
            position: sticky;
            top: 0;
            z-index: 20;
            background: rgba(255,255,255,0.96);
            backdrop-filter: blur(4px);
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 8px 10px;
            margin-bottom: 12px;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 8px;
        }

        .dashboard-kpi {
            background: #f8fafc;
            border-radius: 8px;
            padding: 8px 10px;
        }

        .dashboard-kpi .k {
            font-size: 0.72em;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        .dashboard-kpi .v {
            font-size: 1.1em;
            font-weight: 700;
            line-height: 1.2;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            border-radius: 12px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .header .subtitle {
            font-size: 1.1em;
            opacity: 0.9;
        }
        
        .status-badge {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.9em;
            margin-top: 15px;
        }
        
        .status-pass {
            background: #48bb78;
            color: white;
        }
        
        .status-fail {
            background: #f56565;
            color: white;
        }
        
        .section {
            background: white;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 25px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }

        .compact-section {
            padding: 22px;
            margin-bottom: 18px;
        }

        .compact-section .section-title {
            font-size: 1.35em;
            margin-bottom: 14px;
            padding-bottom: 8px;
        }

        .compact-section .metrics-grid {
            gap: 14px;
            margin-top: 14px;
        }

        .compact-section .metric-card {
            padding: 18px;
        }

        .compact-section .metric-value {
            font-size: 1.65em;
        }

        .compact-section .quality-score {
            padding: 28px;
        }

        .compact-section .quality-score-value {
            font-size: 3em;
        }

        .compact-section .quality-score-label {
            font-size: 1em;
        }
        
        .section-title {
            font-size: 1.8em;
            margin-bottom: 20px;
            color: #1a202c;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        
        .metric-card {
            background: linear-gradient(135deg, #f6f8fb 0%, #ffffff 100%);
            padding: 25px;
            border-radius: 10px;
            border-left: 4px solid #667eea;
        }
        
        .metric-label {
            font-size: 0.9em;
            color: #718096;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        
        .metric-value {
            font-size: 2.2em;
            font-weight: 700;
            color: #2d3748;
        }
        
        .metric-unit {
            font-size: 0.5em;
            color: #a0aec0;
            margin-left: 5px;
        }
        
        .quality-score {
            text-align: center;
            padding: 40px;
        }
        
        .quality-score-value {
            font-size: 4em;
            font-weight: 700;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .quality-score-label {
            font-size: 1.2em;
            color: #718096;
            margin-top: 10px;
        }
        
        .chart-container {
            position: relative;
            height: 400px;
            margin: 30px 0;
        }
        
        .chart-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
            margin: 30px 0;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        
        th {
            background: #f7fafc;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            color: #2d3748;
            border-bottom: 2px solid #e2e8f0;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #e2e8f0;
        }
        
        tr:hover {
            background: #f7fafc;
        }
        
        .issue {
            padding: 15px;
            margin: 10px 0;
            border-radius: 8px;
            border-left: 4px solid;
        }
        
        .issue-critical {
            background: #fff5f5;
            border-color: #f56565;
        }
        
        .issue-error {
            background: #fff5f5;
            border-color: #fc8181;
        }
        
        .issue-warning {
            background: #fffaf0;
            border-color: #f6ad55;
        }
        
        .issue-info {
            background: #ebf8ff;
            border-color: #63b3ed;
        }
        
        .issue-title {
            font-weight: 600;
            margin-bottom: 5px;
        }
        
        .issue-message {
            color: #4a5568;
        }
        
        .footer {
            text-align: center;
            padding: 30px;
            color: #a0aec0;
            font-size: 0.9em;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        .badge-numeric {
            background: #bee3f8;
            color: #2c5282;
        }
        
        .badge-string {
            background: #c6f6d5;
            color: #22543d;
        }
        
        .badge-datetime {
            background: #fed7d7;
            color: #742a2a;
        }
        
        .search-box {
            width: 100%;
            padding: 12px 20px;
            margin-bottom: 20px;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            font-size: 1em;
            transition: border-color 0.3s;
        }
        
        .search-box:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .table-controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .pagination {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .pagination button {
            padding: 8px 16px;
            border: 1px solid #e2e8f0;
            background: white;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }
        
        .pagination button:hover:not(:disabled) {
            background: #667eea;
            color: white;
            border-color: #667eea;
        }
        
        .pagination button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .pagination .page-info {
            color: #718096;
            font-weight: 600;
        }
        
        th.sortable {
            cursor: pointer;
            user-select: none;
            position: relative;
            padding-right: 30px;
        }
        
        th.sortable:hover {
            background: #edf2f7;
        }
        
        th.sortable::after {
            content: '⇅';
            position: absolute;
            right: 10px;
            opacity: 0.3;
        }
        
        th.sortable.asc::after {
            content: '↑';
            opacity: 1;
        }
        
        th.sortable.desc::after {
            content: '↓';
            opacity: 1;
        }
        
        .no-results {
            text-align: center;
            padding: 40px;
            color: #a0aec0;
            font-size: 1.1em;
        }
        
        details {
            margin: 20px 0;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 15px;
        }
        
        summary {
            cursor: pointer;
            font-weight: 600;
            color: #667eea;
            padding: 10px;
            user-select: none;
        }
        
        summary:hover {
            background: #f7fafc;
            border-radius: 6px;
        }
        
        .config-table {
            width: 100%;
            margin: 15px 0;
        }
        
        .config-table td:first-child {
            font-weight: 600;
            color: #4a5568;
            width: 30%;
        }
        
        .config-table td:last-child {
            color: #2d3748;
        }
        
        .violations-table {
            width: 100%;
            margin-top: 15px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            overflow: hidden;
        }
        
        .violations-table th {
            background: #f8fafc;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #718096;
            padding: 12px 15px;
        }
        
        .violations-table td {
            padding: 12px 15px;
            vertical-align: top;
        }

        .severity-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75em;
            font-weight: 700;
            text-transform: uppercase;
        }
        
        .severity-error {
            background: #fff5f5;
            color: #c53030;
            border: 1px solid #fed7d7;
        }
        
        .severity-warning {
            background: #fffaf0;
            color: #c05621;
            border: 1px solid #feebc8;
        }
        
        .severity-info {
            background: #ebf8ff;
            color: #2b6cb0;
            border: 1px solid #bee3f8;
        }
        """

    def _generate_header(self, results: Dict[str, Any]) -> str:
        """Generate report header."""
        status = "PASS" if results['valid'] else "FAIL"
        status_class = "status-pass" if results['valid'] else "status-fail"
        file_name = results['file_metadata'].get('file_name', 'Unknown')
        timestamp = results.get('timestamp', datetime.now().isoformat())
        
        return f"""
        <div class="header">
            <h1>📊 File Validation Report</h1>
            <div class="subtitle">{file_name}</div>
            <div class="subtitle">Generated: {timestamp}</div>
            <span class="status-badge {status_class}">✓ {status}</span>
        </div>
        """

    def _generate_dashboard_bar(self, results: Dict[str, Any]) -> str:
        """Generate compact, single-screen KPI bar."""
        metrics = results.get('quality_metrics', {})
        appendix = results.get('appendix', {})
        affected = appendix.get('affected_rows', {}) if isinstance(appendix, dict) else {}
        dup = results.get('duplicate_analysis', {})

        total_rows = int(metrics.get('total_rows', 0) or 0)
        errored_rows = int(affected.get('total_affected_rows', 0) or 0)
        if total_rows > 0:
            errored_rows = min(max(errored_rows, 0), total_rows)
        good_rows = max(total_rows - errored_rows, 0)

        kpis = [
            ("Quality", f"{metrics.get('quality_score', 0)}%", "Overall score combining completeness, uniqueness, and validation outcomes."),
            ("Rows", f"{total_rows:,}", "Total records processed in this validation run."),
            ("Errors", f"{results.get('error_count', 0):,}", "Count of validation errors."),
            ("Warnings", f"{results.get('warning_count', 0):,}", "Count of validation warnings."),
            ("Good Rows", f"{good_rows:,}", "Rows without reported validation issues."),
            ("Errored Rows", f"{errored_rows:,}", "Rows with one or more reported validation issues."),
            ("Completeness", f"{metrics.get('completeness_pct', 0)}%", "Percent of non-null/non-empty cells across the dataset."),
            ("Uniqueness", f"{metrics.get('uniqueness_pct', 0)}%", "Percent of rows that are unique (non-duplicate)."),
            ("Duplicates", f"{dup.get('duplicate_rows', 0):,}", "Number of detected duplicate rows."),
            ("Columns", f"{metrics.get('total_columns', 0):,}", "Total columns in the parsed dataset."),
        ]

        blocks = ''.join(
            f'<div class="dashboard-kpi" title="{tip}"><div class="k">{k}</div><div class="v">{v}</div></div>'
            for k, v, tip in kpis
        )

        return f"""
        <div class="dashboard-bar">
            <div class="dashboard-grid">{blocks}</div>
        </div>
        """

    def _wrap_collapsible_section(self, title: str, section_html: str) -> str:
        """Wrap heavy sections in collapsed containers for single-screen overview."""
        if not section_html:
            return ""
        return f"""
        <details>
            <summary>Show {title}</summary>
            {section_html}
        </details>
        """

    def _generate_full_metrics_details(self, results: Dict[str, Any]) -> str:
        """Keep legacy metric cards available, but collapsed to avoid redundancy."""
        parts = [
            self._generate_summary(results),
            self._generate_file_metadata(results),
            self._generate_quality_metrics(results),
        ]
        content = ''.join(p for p in parts if p)
        if not content:
            return ''
        return f"""
        <details>
            <summary>Show Full Metrics Details</summary>
            {content}
        </details>
        """

    def _generate_summary(self, results: Dict[str, Any]) -> str:
        """Generate executive summary."""
        quality_metrics = results.get('quality_metrics', {})
        quality_score = quality_metrics.get('quality_score', 0)
        total_rows = int(quality_metrics.get('total_rows', 0) or 0)
        error_count = results.get('error_count', 0)
        warning_count = results.get('warning_count', 0)
        appendix = results.get('appendix', {})
        affected_rows = appendix.get('affected_rows', {}) if isinstance(appendix, dict) else {}
        errored_rows = int(affected_rows.get('total_affected_rows', 0) or 0)
        if total_rows > 0:
            errored_rows = min(max(errored_rows, 0), total_rows)
        good_rows = max(total_rows - errored_rows, 0)

        return f"""
        <div class="section compact-section">
            <h2 class="section-title">Executive Summary</h2>
            <div class="quality-score">
                <div class="quality-score-value">{quality_score}%</div>
                <div class="quality-score-label">Overall Data Quality Score</div>
            </div>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Errors</div>
                    <div class="metric-value" style="color: #f56565;">{error_count}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Warnings</div>
                    <div class="metric-value" style="color: #f6ad55;">{warning_count}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Errored Rows</div>
                    <div class="metric-value" style="color: #f56565;">{errored_rows:,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Good Rows</div>
                    <div class="metric-value" style="color: #48bb78;">{good_rows:,}</div>
                </div>
            </div>
        </div>
        """

    def _generate_file_metadata(self, results: Dict[str, Any]) -> str:
        """Generate file metadata section."""
        metadata = results.get('file_metadata', {})
        
        return f"""
        <div class="section compact-section">
            <h2 class="section-title">File Metadata</h2>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">File Size</div>
                    <div class="metric-value">{metadata.get('size_mb', 0):.2f}<span class="metric-unit">MB</span></div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Format</div>
                    <div class="metric-value" style="font-size: 1.5em;">{metadata.get('format', 'Unknown').upper()}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Last Modified</div>
                    <div class="metric-value" style="font-size: 1em;">{metadata.get('modified_time', 'Unknown')}</div>
                </div>
            </div>
        </div>
        """

    def _generate_quality_metrics(self, results: Dict[str, Any]) -> str:
        """Generate data quality metrics section."""
        metrics = results.get('quality_metrics', {})
        
        if not metrics:
            return ""
        
        return f"""
        <div class="section compact-section">
            <h2 class="section-title">Data Quality Metrics</h2>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Total Rows</div>
                    <div class="metric-value">{metrics.get('total_rows', 0):,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Columns</div>
                    <div class="metric-value">{metrics.get('total_columns', 0):,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Completeness</div>
                    <div class="metric-value">{metrics.get('completeness_pct', 0)}<span class="metric-unit">%</span></div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Uniqueness</div>
                    <div class="metric-value">{metrics.get('uniqueness_pct', 0)}<span class="metric-unit">%</span></div>
                </div>
            </div>
            <div class="chart-row">
                <div class="chart-container">
                    <canvas id="completenessChart"></canvas>
                </div>
                <div class="chart-container">
                    <canvas id="uniquenessChart"></canvas>
                </div>
            </div>
        </div>
        """

    def _generate_issues(self, results: Dict[str, Any]) -> str:
        """Generate issues and warnings section with collapsible groups."""
        errors = self._sort_issues(results.get('errors', []))
        warnings = self._sort_issues(results.get('warnings', []))
        info = self._sort_issues(results.get('info', []))

        if not errors and not warnings and not info:
            return """
            <div class="section">
                <h2 class="section-title">Issues & Warnings</h2>
                <p style="color: #48bb78; font-size: 1.2em;">✓ No issues found!</p>
            </div>
            """

        def _render_items(items, icon):
            html = []
            for item in items:
                severity = item.get('severity', 'info') if isinstance(item, dict) else 'info'
                raw_message = item.get('message', '') if isinstance(item, dict) else str(item)
                message = self._redact_message(raw_message)
                # Surface source_row (physical line number) when available.
                source_row = None
                if isinstance(item, dict):
                    source_row = item.get('source_row', item.get('row'))
                source_row_html = (
                    f'<div class="issue-meta">Source Row: <strong>{source_row}</strong></div>'
                    if source_row is not None else ''
                )
                html.append(f"""
                <div class="issue issue-{severity}">
                    <div class="issue-title">{icon} {severity.upper()}</div>
                    {source_row_html}
                    <div class="issue-message">{message}</div>
                </div>
                """)
            return ''.join(html)

        # Affected rows summary (moved from appendix to this section)
        appendix = results.get('appendix', {})
        affected_rows = appendix.get('affected_rows', {}) if isinstance(appendix, dict) else {}
        total_affected = affected_rows.get('total_affected_rows', 0)
        affected_pct = affected_rows.get('affected_row_pct', 0)
        top_problematic = affected_rows.get('top_problematic_rows', [])

        problematic_rows_html = ""
        if top_problematic:
            problematic_rows = []
            for row_info in top_problematic[:10]:
                row_num = row_info.get('row_number', 0)
                issue_count = row_info.get('issue_count', 0)
                issues = row_info.get('issues', [])

                issues_list = ''.join(f'<li>{self._redact_issue_text(issue)}</li>' for issue in issues[:5])
                if len(issues) > 5:
                    issues_list += f'<li><em>... and {len(issues) - 5} more issues</em></li>'

                problematic_rows.append(f"""
                <tr>
                    <td>{row_num:,}</td>
                    <td>{issue_count}</td>
                    <td><ul style="margin: 0; padding-left: 20px;">{issues_list}</ul></td>
                </tr>
                """)

            problematic_rows_html = f"""
            <details>
                <summary>Top 10 Most Problematic Rows</summary>
                <table style="margin-top: 15px;">
                    <thead>
                        <tr>
                            <th>Row #</th>
                            <th>Issue Count</th>
                            <th>Issues</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(problematic_rows)}
                    </tbody>
                </table>
            </details>
            """

        affected_rows_html = f"""
        <details>
            <summary>Affected Rows Summary</summary>
            <p style="margin: 15px 0;">
                Total rows with issues: <strong>{total_affected:,} ({affected_pct}%)</strong>
            </p>
            {problematic_rows_html}
        </details>
        """

        required_counts: dict[str, int] = {}
        for e in errors:
            if not isinstance(e, dict):
                continue
            field = e.get('field')
            code = str(e.get('code') or '')
            msg = str(e.get('message') or '')
            is_required = code == 'FW_REQ_001' or "required field" in msg.lower()
            if not is_required:
                continue
            if not field:
                m = re.search(r"Required field '([^']+)'", msg, flags=re.IGNORECASE)
                if m:
                    field = m.group(1)
            if not field:
                continue
            name = str(field)
            required_counts[name] = required_counts.get(name, 0) + 1

        required_summary_html = ""
        if required_counts:
            rows = ''.join(
                f"<tr><td><code>{fname}</code></td><td>{cnt:,}</td></tr>"
                for fname, cnt in sorted(required_counts.items(), key=lambda x: (-x[1], x[0]))
            )
            required_summary_html = f"""
            <details>
                <summary>Required Field Error Summary ({sum(required_counts.values()):,} errors across {len(required_counts)} fields)</summary>
                <table style=\"margin-top: 15px;\">
                    <thead><tr><th>Field</th><th>Error Count</th></tr></thead>
                    <tbody>{rows}</tbody>
                </table>
            </details>
            """

        mapping_errors = 0
        data_errors = 0
        for e in errors:
            if not isinstance(e, dict):
                data_errors += 1
                continue
            code = str(e.get('code') or '')
            msg = str(e.get('message') or '').lower()
            if code.startswith('MAP_') or 'invalid mapping' in msg or 'mapping:' in msg:
                mapping_errors += 1
            else:
                data_errors += 1

        error_bucket_html = f"""
        <div class=\"metrics-grid\" style=\"margin: 12px 0 18px 0;\">
            <div class=\"metric-card\">
                <div class=\"metric-label\">Mapping Errors</div>
                <div class=\"metric-value\" style=\"color: #c53030;\">{mapping_errors:,}</div>
            </div>
            <div class=\"metric-card\">
                <div class=\"metric-label\">Data Errors</div>
                <div class=\"metric-value\" style=\"color: #2d3748;\">{data_errors:,}</div>
            </div>
        </div>
        """

        error_items = errors[:self.ERROR_DISPLAY_LIMIT]
        warning_items = warnings[:self.WARNING_DISPLAY_LIMIT]

        error_note = ""
        if len(errors) > self.ERROR_DISPLAY_LIMIT:
            remaining = len(errors) - self.ERROR_DISPLAY_LIMIT
            error_note = f"""
            <div class=\"issue issue-error\">
                <div class=\"issue-title\">🔴 ERROR SUMMARY</div>
                <div class=\"issue-message\">
                    Showing first {self.ERROR_DISPLAY_LIMIT} errors in HTML. {remaining} additional errors were exported to the CSV sidecar file.
                </div>
            </div>
            """

        warning_note = ""
        if len(warnings) > self.WARNING_DISPLAY_LIMIT:
            remaining = len(warnings) - self.WARNING_DISPLAY_LIMIT
            warning_note = f"""
            <div class=\"issue issue-warning\">
                <div class=\"issue-title\">🟡 WARNING SUMMARY</div>
                <div class=\"issue-message\">
                    Showing first {self.WARNING_DISPLAY_LIMIT} warnings in HTML. {remaining} additional warnings were exported to the CSV sidecar file.
                </div>
            </div>
            """

        return f"""
        <div class="section">
            <h2 class="section-title">Issues & Warnings</h2>
            {affected_rows_html}
            {required_summary_html}
            {error_bucket_html}

            <details open>
                <summary>Errors ({len(errors)})</summary>
                {_render_items(error_items, '🔴')}
                {error_note}
            </details>

            <details>
                <summary>Warnings ({len(warnings)})</summary>
                {_render_items(warning_items, '🟡')}
                {warning_note}
            </details>

            <details>
                <summary>Info ({len(info)})</summary>
                {_render_items(info, '🔵')}
            </details>
        </div>
        """

    def _generate_required_fields(self, results: Dict[str, Any]) -> str:
        """Generate required fields section above field-level analysis."""
        appendix = results.get('appendix', {})
        mapping_details = appendix.get('mapping_details', {}) if isinstance(appendix, dict) else {}
        required_fields = mapping_details.get('required_fields', []) if isinstance(mapping_details, dict) else []

        if not required_fields:
            return ""

        required_count = len(required_fields)
        items = ''.join(f'<li><code>{field}</code></li>' for field in required_fields[:100])
        more = f'<li><em>... and {required_count - 100} more</em></li>' if required_count > 100 else ''

        return f"""
        <div class="section">
            <h2 class="section-title">Required Fields</h2>
            <p style="margin-bottom: 10px; color: #4a5568;">Configured required fields: <strong>{required_count}</strong></p>
            <ul style="margin-top: 10px; padding-left: 20px; column-count: 2; column-gap: 30px;">
                {items}
                {more}
            </ul>
        </div>
        """

    def _field_position_order_map(self, results: Dict[str, Any]) -> Dict[str, int]:
        """Build a field-name -> ordinal map based on mapping field positions."""
        appendix = results.get('appendix', {})
        validation_config = appendix.get('validation_config', {}) if isinstance(appendix, dict) else {}
        mapping_file = validation_config.get('mapping_file') if isinstance(validation_config, dict) else None
        if not mapping_file:
            return {}

        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            fields = mapping.get('fields', []) if isinstance(mapping, dict) else []
            ordered = sorted(
                [f for f in fields if isinstance(f, dict) and f.get('name')],
                key=lambda f: int(f.get('position')) if f.get('position') not in (None, '') else 10**9,
            )
            order_map = {}
            for idx, f in enumerate(ordered):
                name = str(f['name'])
                order_map[name] = idx
                order_map[name.upper()] = idx
                order_map[name.lower()] = idx
            return order_map
        except Exception:
            return {}

    def _generate_field_analysis(self, results: Dict[str, Any]) -> str:
        """Generate field-level analysis table with search, sort, and pagination."""
        field_analysis = results.get('field_analysis', {})

        if not field_analysis:
            return ""

        order_map = self._field_position_order_map(results)
        sorted_items = sorted(
            field_analysis.items(),
            key=lambda kv: (
                order_map.get(str(kv[0]), order_map.get(str(kv[0]).upper(), order_map.get(str(kv[0]).lower(), 10**9))),
                str(kv[0]),
            )
        )

        total_fields = len(field_analysis)
        rows = []

        for field_name, analysis in sorted_items:
            inferred_type = analysis.get('inferred_type', 'unknown')
            fill_rate = analysis.get('fill_rate_pct', 0)
            unique_count = analysis.get('unique_count', 0)

            field_name_str = str(field_name)
            field_name_search = field_name_str.lower()
            
            badge_class = {
                'numeric': 'badge-numeric',
                'string': 'badge-string',
                'datetime': 'badge-datetime'
            }.get(inferred_type, '')
            
            rows.append(f"""
            <tr data-field-name="{field_name_search}" data-type="{inferred_type}" data-fill-rate="{fill_rate}" data-unique-count="{unique_count}">
                <td><strong>{field_name_str}</strong></td>
                <td><span class="badge {badge_class}">{inferred_type}</span></td>
                <td>{fill_rate}%</td>
                <td>{unique_count:,}</td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2 class="section-title">Field-Level Analysis</h2>
            
            <div class="table-controls">
                <input type="text" 
                       id="fieldSearch" 
                       class="search-box" 
                       placeholder="🔍 Search fields by name..."
                       onkeyup="filterFields()">
                
                <div class="pagination">
                    <button onclick="previousPage()" id="prevBtn">← Previous</button>
                    <span class="page-info" id="pageInfo">Page 1</span>
                    <button onclick="nextPage()" id="nextBtn">Next →</button>
                </div>
            </div>
            
            <p style="margin-bottom: 15px; color: #718096;">
                Showing <span id="visibleCount">{total_fields}</span> of {total_fields} fields
            </p>
            
            <table id="fieldTable">
                <thead>
                    <tr>
                        <th class="sortable" onclick="sortTable(0)">Field Name</th>
                        <th class="sortable" onclick="sortTable(1)">Data Type</th>
                        <th class="sortable" onclick="sortTable(2)">Fill Rate</th>
                        <th class="sortable" onclick="sortTable(3)">Unique Values</th>
                    </tr>
                </thead>
                <tbody id="fieldTableBody">
                    {''.join(rows)}
                </tbody>
            </table>
            
            <div id="noResults" class="no-results" style="display: none;">
                No fields match your search criteria
            </div>
        </div>
        """

    def _generate_duplicate_analysis(self, results: Dict[str, Any]) -> str:
        """Generate duplicate analysis section."""
        dup_analysis = results.get('duplicate_analysis', {})
        
        if not dup_analysis:
            return ""
        
        return f"""
        <div class="section">
            <h2 class="section-title">Duplicate Analysis</h2>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Unique Rows</div>
                    <div class="metric-value">{dup_analysis.get('unique_rows', 0):,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Duplicate Rows</div>
                    <div class="metric-value" style="color: #f6ad55;">{dup_analysis.get('duplicate_rows', 0):,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Duplicate Percentage</div>
                    <div class="metric-value">{dup_analysis.get('duplicate_pct', 0)}<span class="metric-unit">%</span></div>
                </div>
            </div>
            <div class="chart-container" style="height: 300px;">
                <canvas id="duplicateChart"></canvas>
            </div>
        </div>
        """

    def _generate_date_analysis(self, results: Dict[str, Any]) -> str:
        """Generate date field analysis section."""
        date_analysis = results.get('date_analysis', {})
        
        if not date_analysis:
            return ""
        
        rows = []
        for field_name, analysis in date_analysis.items():
            earliest = analysis.get('earliest_date', 'N/A')
            latest = analysis.get('latest_date', 'N/A')
            date_range_days = analysis.get('date_range_days', 0)
            invalid_count = analysis.get('invalid_date_count', 0)
            invalid_pct = analysis.get('invalid_date_pct', 0)
            future_count = analysis.get('future_date_count', 0)
            future_pct = analysis.get('future_date_pct', 0)
            formats = analysis.get('detected_formats', [])
            
            # Format date range display
            if earliest != 'N/A' and latest != 'N/A':
                date_range = f"{earliest[:10]} to {latest[:10]}<br><small>({date_range_days:,} days)</small>"
            else:
                date_range = "N/A"
            
            # Format detected formats
            format_display = ', '.join(formats) if formats else 'Unknown'
            
            rows.append(f"""
            <tr>
                <td><strong>{field_name}</strong></td>
                <td>{date_range}</td>
                <td>{invalid_count:,} ({invalid_pct}%)</td>
                <td>{future_count:,} ({future_pct}%)</td>
                <td><code>{format_display}</code></td>
            </tr>
            """)
        
        return f"""
        <div class="section">
            <h2 class="section-title">📅 Date Field Analysis</h2>
            <p style="margin-bottom: 20px; color: #718096;">
                Analysis of {len(date_analysis)} date/datetime field(s) detected in the file.
            </p>
            <table>
                <thead>
                    <tr>
                        <th>Field Name</th>
                        <th>Date Range</th>
                        <th>Invalid Dates</th>
                        <th>Future Dates</th>
                        <th>Detected Format</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        </div>
        """

    def _generate_business_rules(self, results: Dict[str, Any]) -> str:
        """Generate business rules validation section."""
        rules_data = results.get('business_rules')
        # Check if rules executed (even if disabled/failed) or just missing
        if not rules_data or not rules_data.get('enabled') and not rules_data.get('error'):
            return ""
            
        stats = rules_data.get('statistics', {})
        violations = rules_data.get('violations', [])
        error_msg = rules_data.get('error')
        
        if error_msg:
             return f"""
            <div class="section">
                <h2 class="section-title">📋 Business Rule Validation</h2>
                <div class="issue issue-error">
                    <div class="issue-title">Execution Error</div>
                    <div class="issue-message">{error_msg}</div>
                </div>
            </div>"""

        # Calculate summary metrics
        total_rules = stats.get('total_rules', 0)
        executed_rules = stats.get('executed_rules', 0)
        total_violations = stats.get('total_violations', 0)
        compliance_rate = stats.get('compliance_rate', 100.0)
        
        # Color for compliance rate
        compliance_color = '#38a169' if compliance_rate >= 95 else '#e53e3e' if compliance_rate < 80 else '#d69e2e'
        
        # Group violations by rule
        violations_by_rule = {}
        for v in violations:
            rule_id = v['rule_id']
            if rule_id not in violations_by_rule:
                violations_by_rule[rule_id] = {
                    'name': v['rule_name'],
                    'severity': v['severity'],
                    'count': 0,
                    'samples': []
                }
            violations_by_rule[rule_id]['count'] += 1
            if len(violations_by_rule[rule_id]['samples']) < 5:
                # Format: "Row 123: Message"
                violations_by_rule[rule_id]['samples'].append(
                    f"Row {v['row_number']}: {self._redact_message(v['message'])}"
                )

        # Build HTML
        html = f"""
        <div class="section">
            <h2 class="section-title">📋 Business Rule Validation</h2>
            
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Rules Executed</div>
                    <div class="metric-value">{executed_rules} <span class="metric-unit">/ {total_rules}</span></div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Violations</div>
                    <div class="metric-value" style="color: {'#e53e3e' if total_violations > 0 else '#38a169'}">{total_violations}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Compliance Rate</div>
                    <div class="metric-value" style="color: {compliance_color}">{compliance_rate}%</div>
                </div>
            </div>
            
            <h3 style="margin: 25px 0 15px 0; font-size: 1.2em; color: #4a5568;">Violations by Rule</h3>
            <table class="violations-table">
                <thead>
                    <tr>
                        <th style="width: 25%">Rule Name</th>
                        <th style="width: 10%">Severity</th>
                        <th style="width: 10%">Count</th>
                        <th style="width: 55%">Sample Issues</th>
                    </tr>
                </thead>
                <tbody>"""
                
        if not violations_by_rule:
             html += """<tr><td colspan="4" style="text-align: center; padding: 30px; color: #718096; font-size: 1.1em;">No business rule violations found. ✅</td></tr>"""
        else:
            # Sort by count desc
            sorted_rules = sorted(violations_by_rule.items(), key=lambda x: x[1]['count'], reverse=True)
            
            for rule_id, info in sorted_rules:
                severity_class = f"severity-{info['severity']}"
                samples_html = '<div style="margin-bottom: 4px;">' + '</div><div style="margin-bottom: 4px;">'.join(info['samples']) + '</div>'
                if info['count'] > 5:
                    samples_html += f'<div style="color: #a0aec0; font-style: italic;">... and {info["count"] - 5} more</div>'
                    
                html += f"""
                    <tr>
                        <td>
                            <strong>{info['name']}</strong><br>
                            <span style="font-size: 0.85em; color: #a0aec0; font-family: monospace;">{rule_id}</span>
                        </td>
                        <td><span class="severity-badge {severity_class}">{info['severity']}</span></td>
                        <td><strong>{info['count']}</strong></td>
                        <td style="font-size: 0.9em; color: #4a5568;">{samples_html}</td>
                    </tr>"""

        html += """
                </tbody>
            </table>
        </div>"""
        
        return html

    def _generate_appendix(self, results: Dict[str, Any]) -> str:
        """Generate appendix section."""
        appendix = results.get('appendix', {})
        
        if not appendix:
            return ""
        
        validation_config = appendix.get('validation_config', {})
        mapping_details = appendix.get('mapping_details')
        # affected_rows summary moved to Issues & Warnings section
        
        mode = validation_config.get('mode')
        chunk_size = validation_config.get('chunk_size')
        elapsed_seconds = validation_config.get('elapsed_seconds')
        rows_per_second = validation_config.get('rows_per_second')

        telemetry_rows = ""
        if mode:
            telemetry_rows += f"""
            <tr>
                <td>Validation Mode</td>
                <td>{mode}</td>
            </tr>
            """
        if chunk_size is not None:
            telemetry_rows += f"""
            <tr>
                <td>Chunk Size</td>
                <td>{chunk_size:,}</td>
            </tr>
            """
        if elapsed_seconds is not None:
            telemetry_rows += f"""
            <tr>
                <td>Elapsed Seconds</td>
                <td>{elapsed_seconds}</td>
            </tr>
            """
        if rows_per_second is not None:
            telemetry_rows += f"""
            <tr>
                <td>Rows / Second</td>
                <td>{rows_per_second}</td>
            </tr>
            """

        # Validation Configuration
        config_html = f"""
        <h3>Validation Configuration</h3>
        <table class="config-table">
            <tr>
                <td>Detailed Mode</td>
                <td>{validation_config.get('detailed_mode', False)}</td>
            </tr>
            {telemetry_rows}
            <tr>
                <td>Mapping File</td>
                <td>{validation_config.get('mapping_file', 'None')}</td>
            </tr>
            <tr>
                <td>Validation Timestamp</td>
                <td>{validation_config.get('validation_timestamp', 'N/A')}</td>
            </tr>
            <tr>
                <td>Validator Version</td>
                <td>{validation_config.get('validator_version', '1.0.0')}</td>
            </tr>
        </table>
        """
        
        # Mapping Details
        mapping_html = ""
        if mapping_details:
            total_fields = mapping_details.get('total_fields', 0)
            required_count = mapping_details.get('required_field_count', 0)
            total_width = mapping_details.get('total_width')
            required_fields = mapping_details.get('required_fields', [])
            
            width_row = f"""
            <tr>
                <td>Total Width</td>
                <td>{total_width:,} characters</td>
            </tr>
            """ if total_width else ""
            
            mapping_html = f"""
            <h3>Mapping File Details</h3>
            <table class="config-table">
                <tr>
                    <td>Total Fields</td>
                    <td>{total_fields}</td>
                </tr>
                {width_row}
                <tr>
                    <td>Required Fields</td>
                    <td>{required_count}</td>
                </tr>
            </table>
            <p style="margin-top: 10px; color: #718096;">Required field list is shown above Field-Level Analysis.</p>
            """
        
        # Affected rows summary moved to Issues & Warnings section.
        
        return f"""
        <div class="section">
            <h2 class="section-title">📋 Appendix</h2>
            {config_html}
            {mapping_html}
        </div>
        """

    def _generate_footer(self) -> str:
        """Generate report footer."""
        return """
        <div class="footer">
            Generated by CM3 Batch Automations - File Validation System
        </div>
        """

    def _generate_charts_js(self, results: Dict[str, Any]) -> str:
        """Generate JavaScript for charts."""
        metrics = results.get('quality_metrics', {})
        dup_analysis = results.get('duplicate_analysis', {})
        
        completeness = metrics.get('completeness_pct', 0)
        uniqueness = metrics.get('uniqueness_pct', 0)
        unique_rows = dup_analysis.get('unique_rows', 0)
        duplicate_rows = dup_analysis.get('duplicate_rows', 0)
        
        return f"""
        // Completeness Chart
        new Chart(document.getElementById('completenessChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Filled', 'Null'],
                datasets: [{{
                    data: [{completeness}, {100 - completeness}],
                    backgroundColor: ['#48bb78', '#e2e8f0']
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Data Completeness',
                        font: {{ size: 16 }}
                    }},
                    legend: {{
                        position: 'bottom'
                    }}
                }}
            }}
        }});
        
        // Uniqueness Chart
        new Chart(document.getElementById('uniquenessChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Unique', 'Duplicate'],
                datasets: [{{
                    data: [{uniqueness}, {100 - uniqueness}],
                    backgroundColor: ['#667eea', '#f6ad55']
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Row Uniqueness',
                        font: {{ size: 16 }}
                    }},
                    legend: {{
                        position: 'bottom'
                    }}
                }}
            }}
        }});
        
        // Duplicate Distribution Chart
        new Chart(document.getElementById('duplicateChart'), {{
            type: 'bar',
            data: {{
                labels: ['Unique Rows', 'Duplicate Rows'],
                datasets: [{{
                    label: 'Row Count',
                    data: [{unique_rows}, {duplicate_rows}],
                    backgroundColor: ['#667eea', '#f6ad55']
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Duplicate Distribution',
                        font: {{ size: 16 }}
                    }},
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});
        
        // Field Analysis Table Interactivity
        let currentPage = 1;
        const rowsPerPage = 50;
        let allRows = [];
        let filteredRows = [];
        let currentSortColumn = -1;
        let currentSortDirection = 'asc';
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {{
            const tbody = document.getElementById('fieldTableBody');
            if (tbody) {{
                allRows = Array.from(tbody.getElementsByTagName('tr'));
                filteredRows = [...allRows];
                updatePagination();
            }}
        }});
        
        // Search/Filter
        function filterFields() {{
            const searchTerm = document.getElementById('fieldSearch').value.toLowerCase();
            const tbody = document.getElementById('fieldTableBody');
            const noResults = document.getElementById('noResults');
            const table = document.getElementById('fieldTable');
            
            filteredRows = allRows.filter(row => {{
                const fieldName = row.getAttribute('data-field-name');
                return fieldName.includes(searchTerm);
            }});
            
            currentPage = 1;
            updatePagination();
            
            // Update visible count
            document.getElementById('visibleCount').textContent = filteredRows.length;
            
            // Show/hide no results message
            if (filteredRows.length === 0) {{
                table.style.display = 'none';
                noResults.style.display = 'block';
            }} else {{
                table.style.display = 'table';
                noResults.style.display = 'none';
            }}
        }}
        
        // Sorting
        function sortTable(columnIndex) {{
            const headers = document.querySelectorAll('#fieldTable th.sortable');
            
            // Toggle sort direction
            if (currentSortColumn === columnIndex) {{
                currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
            }} else {{
                currentSortDirection = 'asc';
                currentSortColumn = columnIndex;
            }}
            
            // Update header classes
            headers.forEach((header, index) => {{
                header.classList.remove('asc', 'desc');
                if (index === columnIndex) {{
                    header.classList.add(currentSortDirection);
                }}
            }});
            
            // Sort rows
            filteredRows.sort((a, b) => {{
                let aVal, bVal;
                
                switch(columnIndex) {{
                    case 0: // Field Name
                        aVal = a.getAttribute('data-field-name');
                        bVal = b.getAttribute('data-field-name');
                        break;
                    case 1: // Data Type
                        aVal = a.getAttribute('data-type');
                        bVal = b.getAttribute('data-type');
                        break;
                    case 2: // Fill Rate
                        aVal = parseFloat(a.getAttribute('data-fill-rate'));
                        bVal = parseFloat(b.getAttribute('data-fill-rate'));
                        break;
                    case 3: // Unique Count
                        aVal = parseInt(a.getAttribute('data-unique-count'));
                        bVal = parseInt(b.getAttribute('data-unique-count'));
                        break;
                }}
                
                if (typeof aVal === 'string') {{
                    return currentSortDirection === 'asc' 
                        ? aVal.localeCompare(bVal)
                        : bVal.localeCompare(aVal);
                }} else {{
                    return currentSortDirection === 'asc' 
                        ? aVal - bVal
                        : bVal - aVal;
                }}
            }});
            
            currentPage = 1;
            updatePagination();
        }}
        
        // Pagination
        function updatePagination() {{
            const tbody = document.getElementById('fieldTableBody');
            const totalPages = Math.ceil(filteredRows.length / rowsPerPage);
            const start = (currentPage - 1) * rowsPerPage;
            const end = start + rowsPerPage;
            
            // Clear tbody
            tbody.innerHTML = '';
            
            // Add visible rows
            filteredRows.slice(start, end).forEach(row => {{
                tbody.appendChild(row);
            }});
            
            // Update page info
            document.getElementById('pageInfo').textContent = 
                `Page ${{currentPage}} of ${{totalPages || 1}}`;
            
            // Update button states
            document.getElementById('prevBtn').disabled = currentPage === 1;
            document.getElementById('nextBtn').disabled = currentPage >= totalPages;
        }}
        
        function nextPage() {{
            const totalPages = Math.ceil(filteredRows.length / rowsPerPage);
            if (currentPage < totalPages) {{
                currentPage++;
                updatePagination();
            }}
        }}
        
        function previousPage() {{
            if (currentPage > 1) {{
                currentPage--;
                updatePagination();
            }}
        }}
        """
