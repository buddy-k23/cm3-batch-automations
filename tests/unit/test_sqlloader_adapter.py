"""Tests for SQL*Loader adapter parsing/evaluation."""

import os
import tempfile

from src.pipeline.sqlloader_adapter import parse_sqlloader_log, evaluate_sqlloader_stage


def _tmp_log(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log')
    f.write(content)
    f.close()
    return f.name


def test_sqlloader_adapter_positive_thresholds_pass():
    p = _tmp_log("""\n100 Rows successfully loaded.\n0 Rows not loaded due to data errors.\n0 Rows not loaded because all WHEN clauses were failed.\n""")
    try:
        metrics = parse_sqlloader_log(p)
        assert metrics['rows_loaded'] == 100
        out = evaluate_sqlloader_stage({'log_file': p, 'max_rejected': 0, 'max_discarded': 0})
        assert out['status'] == 'passed'
    finally:
        os.unlink(p)


def test_sqlloader_adapter_negative_rejected_fails():
    p = _tmp_log("""\n100 Rows successfully loaded.\n2 Rows not loaded due to data errors.\n0 Rows not loaded because all WHEN clauses were failed.\n""")
    try:
        out = evaluate_sqlloader_stage({'log_file': p, 'max_rejected': 0, 'max_discarded': 0})
        assert out['status'] == 'failed'
        assert 'rows_rejected' in out['message']
    finally:
        os.unlink(p)
