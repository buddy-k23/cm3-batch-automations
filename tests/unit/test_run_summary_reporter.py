"""Tests for pipeline run summary reporter."""

import json
from pathlib import Path

from src.pipeline.run_summary_reporter import write_pipeline_summary_json, write_pipeline_summary_markdown


def test_run_summary_reporter_writes_json_and_markdown(tmp_path: Path):
    summary = {
        'source_system': 'SRC_A',
        'status': 'passed',
        'profile': 'x.json',
        'timestamp': '2026-02-18T00:00:00Z',
        'dry_run': True,
        'steps': [{'name': 'ingest', 'status': 'dry_run', 'message': 'ok'}],
    }

    jp = tmp_path / 'sum.json'
    mp = tmp_path / 'sum.md'
    write_pipeline_summary_json(summary, str(jp))
    write_pipeline_summary_markdown(summary, str(mp))

    assert json.loads(jp.read_text())['status'] == 'passed'
    assert 'Pipeline Run Summary' in mp.read_text()
