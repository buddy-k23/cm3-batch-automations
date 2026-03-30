"""Transform mismatch reporter — structured comparison of source, transformed, and file values.

Produces both JSON and HTML representations of per-field transform results,
making it easy to identify where transformed DB values differ from the values
found in the actual batch file.

Typical usage::

    details = [
        {"field": "STATUS", "source_value": "X",   "transformed_value": "ACTIVE", "file_value": "ACTIVE"},
        {"field": "CODE",   "source_value": "GBP",  "transformed_value": "GBP",    "file_value": "USD"},
    ]
    reporter = TransformMismatchReporter(details)
    json_report = reporter.to_json()   # list of dicts with 'match' key added
    html_table  = reporter.to_html()   # self-contained <table> element
    summary     = reporter.summary()   # {"total_fields": 2, "matching": 1, "mismatching": 1}
"""

from __future__ import annotations

from typing import List


class TransformMismatchReporter:
    """Generate structured mismatch reports from per-field transform details.

    Each entry in *details* describes one field in one row of the comparison:

    - ``field``: Target field name.
    - ``source_value``: Raw DB value before transformation.
    - ``transformed_value``: DB value after :class:`~src.transforms.transform_orchestrator.TransformEngine`
      was applied.  Equals ``source_value`` when the field has no transform.
    - ``file_value``: The value read from the actual batch file.

    The reporter derives a ``match`` boolean by comparing ``transformed_value``
    against ``file_value`` (exact string equality).

    Args:
        details: List of field-detail dicts as described above.
    """

    def __init__(self, details: List[dict]) -> None:
        """Initialise with a list of per-field transform detail dicts.

        Args:
            details: Per-field records, each with keys ``field``,
                ``source_value``, ``transformed_value``, ``file_value``.
        """
        self._details = list(details)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_json(self) -> List[dict]:
        """Return per-field records with a computed ``match`` boolean.

        Returns:
            List of dicts, each containing ``field``, ``source_value``,
            ``transformed_value``, ``file_value``, and ``match``.
        """
        result = []
        for entry in self._details:
            result.append({
                "field": entry.get("field", ""),
                "source_value": entry.get("source_value", ""),
                "transformed_value": entry.get("transformed_value", ""),
                "file_value": entry.get("file_value", ""),
                "match": entry.get("transformed_value", "") == entry.get("file_value", ""),
            })
        return result

    def summary(self) -> dict:
        """Return aggregate counts across all field entries.

        Returns:
            Dict with keys ``total_fields``, ``matching``, ``mismatching``.
        """
        records = self.to_json()
        matching = sum(1 for r in records if r["match"])
        return {
            "total_fields": len(records),
            "matching": matching,
            "mismatching": len(records) - matching,
        }

    def to_html(self) -> str:
        """Render a self-contained HTML ``<table>`` of transform comparison results.

        Match rows are styled with a green background; mismatch rows with a
        red/warning background.  The table includes columns: Field, Source,
        Transformed, File, Status.

        Returns:
            HTML string containing a ``<table>`` element.
        """
        rows_html = ""
        for entry in self.to_json():
            match = entry["match"]
            row_class = "tm-match" if match else "tm-mismatch"
            status_cell = (
                '<td style="color:#1a7a1a;font-weight:bold">✓ Match</td>'
                if match else
                '<td style="color:#a31515;font-weight:bold">✗ Mismatch</td>'
            )
            rows_html += (
                f'<tr class="{row_class}">'
                f'<td>{_esc(entry["field"])}</td>'
                f'<td>{_esc(entry["source_value"])}</td>'
                f'<td>{_esc(entry["transformed_value"])}</td>'
                f'<td>{_esc(entry["file_value"])}</td>'
                f'{status_cell}'
                f'</tr>\n'
            )

        return (
            '<table class="transform-mismatch-report" '
            'style="border-collapse:collapse;width:100%;font-family:monospace;font-size:13px">\n'
            '<thead><tr style="background:#f0f0f0">'
            '<th style="text-align:left;padding:4px 8px;border:1px solid #ccc">Field</th>'
            '<th style="text-align:left;padding:4px 8px;border:1px solid #ccc">Source</th>'
            '<th style="text-align:left;padding:4px 8px;border:1px solid #ccc">Transformed</th>'
            '<th style="text-align:left;padding:4px 8px;border:1px solid #ccc">File</th>'
            '<th style="text-align:left;padding:4px 8px;border:1px solid #ccc">Status</th>'
            '</tr></thead>\n'
            f'<tbody>\n{rows_html}</tbody>\n'
            '</table>'
        )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _esc(value: str) -> str:
    """HTML-escape a string value for safe table cell rendering.

    Args:
        value: Raw string to escape.

    Returns:
        HTML-safe string with ``&``, ``<``, ``>``, ``"`` escaped.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
