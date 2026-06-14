"""Tests for pipeline runner scaffold."""

import json
import os
import tempfile

from src.pipeline.runner import PipelineRunner


def _tmp_profile(profile: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
    f.write(json.dumps(profile))
    f.close()
    return f.name


def test_pipeline_runner_dry_run_positive():
    p = _tmp_profile({
        'source_system': 'SRC_X',
        'stages': {
            'ingest': {'enabled': True, 'command': 'echo ingest'},
            'sqlloader': {'enabled': False},
            'java_batch': {'enabled': True, 'command': 'echo java'},
            'output_validation': {'enabled': True, 'command': 'echo validate'}
        }
    })
    try:
        out = PipelineRunner(p).run(dry_run=True)
        assert out['status'] == 'passed'
        assert any(s['status'] == 'dry_run' for s in out['steps'])
    finally:
        os.unlink(p)


def test_pipeline_runner_sqlloader_log_eval_negative():
    import tempfile

    log = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log')
    log.write('10 Rows successfully loaded.\n1 Rows not loaded due to data errors.\n0 Rows not loaded because all WHEN clauses were failed.\n')
    log.close()

    p = _tmp_profile({
        'source_system': 'SRC_X',
        'stages': {
            'ingest': {'enabled': False},
            'sqlloader': {'enabled': True, 'log_file': log.name, 'max_rejected': 0, 'max_discarded': 0},
            'java_batch': {'enabled': True, 'command': 'echo java'},
            'output_validation': {'enabled': False}
        }
    })
    try:
        out = PipelineRunner(p).run(dry_run=False)
        assert out['status'] == 'failed'
        assert out['steps'][1]['name'] == 'sqlloader'
        assert out['steps'][1]['status'] == 'failed'
    finally:
        os.unlink(p)
        os.unlink(log.name)


def test_pipeline_runner_output_validation_targets_dry_run():
    p = _tmp_profile({
        'source_system': 'SRC_X',
        'stages': {
            'ingest': {'enabled': False},
            'sqlloader': {'enabled': False},
            'java_batch': {'enabled': False},
            'output_validation': {
                'enabled': True,
                'targets': [{'name': 'p327', 'file': 'a.txt', 'mapping': 'm.json'}]
            }
        }
    })
    try:
        out = PipelineRunner(p).run(dry_run=True)
        assert out['status'] == 'passed'
        assert out['steps'][3]['status'] == 'dry_run'
    finally:
        os.unlink(p)


def test_pipeline_runner_negative_missing_required_top_level():
    p = _tmp_profile({'stages': {}})
    try:
        try:
            PipelineRunner(p).run(dry_run=True)
            assert False, 'expected ValueError'
        except ValueError as e:
            assert 'Missing required top-level key: source_system' in str(e)
    finally:
        os.unlink(p)
