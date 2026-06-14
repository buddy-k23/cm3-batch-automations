"""Generate HTML reports for comparison results."""

from typing import Dict, Any
from datetime import datetime
from jinja2 import Template


class HTMLReporter:
    """Generates HTML reports from comparison results."""

    TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Valdo Comparison Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        .summary { background: #f0f0f0; padding: 15px; border-radius: 5px; margin: 20px 0; }
        .summary-item { margin: 10px 0; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #4CAF50; color: white; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        .difference { background-color: #ffeb3b; }
        .timestamp { color: #666; font-size: 0.9em; }
        .source-row { color: #555; font-size: 0.85em; }
    </style>
</head>
<body>
    <h1>Valdo Comparison Report</h1>
    <p class="timestamp">Generated: {{ timestamp }}</p>

    <div class="summary">
        <h2>Summary</h2>
        <div class="summary-item"><strong>Total Rows (File 1):</strong> {{ summary.total_rows_file1 }}</div>
        <div class="summary-item"><strong>Total Rows (File 2):</strong> {{ summary.total_rows_file2 }}</div>
        <div class="summary-item"><strong>Matching Rows:</strong> {{ summary.matching_rows }}</div>
        <div class="summary-item"><strong>Only in File 1:</strong> {{ summary.only_in_file1|length }}</div>
        <div class="summary-item"><strong>Only in File 2:</strong> {{ summary.only_in_file2|length }}</div>
        <div class="summary-item"><strong>Rows with Differences:</strong> {{ summary.differences|length }}</div>
    </div>

    {% if summary.differences %}
    <h2>Differences Found</h2>
    <table>
        <tr>
            <th>File 1 Row</th>
            <th>File 2 Row</th>
            <th>Keys</th>
            <th>Column</th>
            <th>File 1 Value</th>
            <th>File 2 Value</th>
        </tr>
        {% for diff in summary.differences %}
            {% for col, values in diff.differences.items() %}
            <tr class="difference">
                <td class="source-row">{{ diff.source_row_file1 if diff.source_row_file1 is defined else '' }}</td>
                <td class="source-row">{{ diff.source_row_file2 if diff.source_row_file2 is defined else '' }}</td>
                <td>{{ diff.keys }}</td>
                <td>{{ col }}</td>
                <td>{{ values.file1 }}</td>
                <td>{{ values.file2 }}</td>
            </tr>
            {% endfor %}
        {% endfor %}
    </table>
    {% endif %}
</body>
</html>
    """

    def generate(self, comparison_results: Dict[str, Any], output_path: str) -> None:
        """Generate HTML report from comparison results.

        The differences table includes ``File 1 Row`` and ``File 2 Row``
        columns that show the physical source file line number for each
        difference, enabling users to trace mismatches back to the exact
        line in the original batch files.

        Args:
            comparison_results: Results dict from FileComparator, including
                optional ``source_row_file1`` / ``source_row_file2`` keys on
                each difference entry.
            output_path: Filesystem path to write the HTML report to.
        """
        template = Template(self.TEMPLATE)

        html_content = template.render(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            summary=comparison_results,
        )

        with open(output_path, "w") as f:
            f.write(html_content)
