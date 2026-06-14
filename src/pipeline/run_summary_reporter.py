"""Render pipeline-run summary reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def write_pipeline_summary_json(summary: Dict[str, Any], out_path: str) -> None:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2), encoding='utf-8')


def write_pipeline_summary_markdown(summary: Dict[str, Any], out_path: str) -> None:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Pipeline Run Summary - {summary.get('source_system', 'unknown')}",
        "",
        f"- Status: **{summary.get('status')}**",
        f"- Profile: `{summary.get('profile')}`",
        f"- Timestamp: `{summary.get('timestamp')}`",
        f"- Dry Run: `{summary.get('dry_run')}`",
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Message |",
        "|---|---|---|",
    ]

    for s in summary.get('steps', []):
        msg = str(s.get('message', '')).replace('\n', ' ').replace('|', '/')
        lines.append(f"| {s.get('name')} | {s.get('status')} | {msg} |")

    out = "\n".join(lines) + "\n"
    p.write_text(out, encoding='utf-8')
