"""Unit tests for the MCP ``run_etl_pipeline`` tool adapter (S22-2, #439).

Exercises :func:`src.mcp.etl_pipeline_tools.run_etl_pipeline_payload` directly
(no MCP transport — that path is covered by
``tests/integration/test_mcp_etl_pipeline_tool.py``). The adapter is a thin
wrapper over :class:`src.pipeline.etl_pipeline_runner.ETLPipelineRunner`, so
these tests pin: a real file-based validate pipeline (no Oracle), the gate-failure
RESULT (not a tool error), the JSON-native normalisation of embedded
numpy/pandas scalars, and every caller-fixable ``ToolError`` branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.mcp.etl_pipeline_tools import (
    _json_default,
    _to_json_native,
    run_etl_pipeline_payload,
)


# Three clean rows: string name + integer age. Headerless (has_header=False).
_PIPE_DATA = "Alice|30\nBob|25\nCarol|35\n"

_MAPPING = {
    "mapping_name": "etl_pipeline_unit_test",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "name", "data_type": "string"},
        {"name": "age", "data_type": "integer"},
    ],
    "key_columns": ["name"],
}


def _write_pipeline(tmp_path: Path, *, min_rows: int) -> str:
    """Write data + mapping + a single-gate validate pipeline; return config path."""
    data_file = tmp_path / "people.psv"
    data_file.write_text(_PIPE_DATA, encoding="utf-8")
    mapping_file = tmp_path / "people_mapping.json"
    mapping_file.write_text(json.dumps(_MAPPING), encoding="utf-8")

    pipeline = {
        "name": "etl-unit-test-pipeline",
        "gates": [
            {
                "name": "input_validation",
                "blocking": True,
                "steps": [
                    {
                        "type": "validate",
                        "file": str(data_file),
                        "mapping": str(mapping_file),
                        "thresholds": {"min_rows": min_rows},
                    }
                ],
            }
        ],
    }
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(json.dumps(pipeline), encoding="utf-8")
    return str(config_file)


# ---------------------------------------------------------------------------
# Happy paths — real file-based pipeline (no Oracle)
# ---------------------------------------------------------------------------


def test_clean_pipeline_passes(tmp_path):
    config = _write_pipeline(tmp_path, min_rows=1)

    out = run_etl_pipeline_payload(config=config)

    assert out["pipeline_name"] == "etl-unit-test-pipeline"
    assert out["status"] == "passed"
    assert len(out["gates"]) == 1
    gate = out["gates"][0]
    assert gate["name"] == "input_validation"
    assert gate["status"] == "passed"
    assert gate["steps"][0]["status"] == "passed"
    assert out["started_at"] and out["finished_at"]


def test_returned_payload_is_json_native(tmp_path):
    """The result round-trips through json.dumps with no custom default."""
    config = _write_pipeline(tmp_path, min_rows=1)

    out = run_etl_pipeline_payload(config=config)

    # Would raise TypeError if any numpy/pandas scalar leaked through.
    json.dumps(out)


def test_breached_threshold_is_a_failed_result(tmp_path):
    """A breached min_rows threshold is a RESULT (status failed), not a ToolError."""
    config = _write_pipeline(tmp_path, min_rows=10)

    out = run_etl_pipeline_payload(config=config)

    assert out["status"] == "failed"
    assert out["gates"][0]["status"] == "failed"
    assert out["gates"][0]["steps"][0]["status"] == "failed"


def test_run_date_and_params_are_passed_through(tmp_path, monkeypatch):
    """run_date + params are forwarded to the runner verbatim."""
    captured = {}

    class _StubRunner:
        def run_pipeline(self, config_path, run_date=None, params=None):
            captured["config_path"] = config_path
            captured["run_date"] = run_date
            captured["params"] = params
            return {"pipeline_name": "x", "status": "passed", "gates": []}

    monkeypatch.setattr(
        "src.pipeline.etl_pipeline_runner.ETLPipelineRunner", _StubRunner
    )
    config = tmp_path / "p.yaml"
    config.write_text("name: x\n", encoding="utf-8")

    run_etl_pipeline_payload(
        config=str(config), run_date="20260326", params={"env": "staging"}
    )

    assert captured["run_date"] == "20260326"
    assert captured["params"] == {"env": "staging"}


# ---------------------------------------------------------------------------
# Caller-fixable ToolError branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", None, 123])
def test_blank_config_raises(bad):
    with pytest.raises(ToolError, match="config is required"):
        run_etl_pipeline_payload(config=bad)  # type: ignore[arg-type]


def test_missing_config_raises(tmp_path):
    with pytest.raises(ToolError, match="not found"):
        run_etl_pipeline_payload(config=str(tmp_path / "nope.yaml"))


def test_malformed_yaml_raises_toolerror(tmp_path):
    bad = tmp_path / "bad.yaml"
    # A YAML scalar (not a mapping) fails pydantic validation in the runner.
    bad.write_text("just a string, not a pipeline\n", encoding="utf-8")

    with pytest.raises(ToolError, match="Pipeline run failed"):
        run_etl_pipeline_payload(config=str(bad))


def test_runner_filenotfound_surfaces_as_toolerror(tmp_path, monkeypatch):
    class _Boom:
        def run_pipeline(self, **kwargs):
            raise FileNotFoundError("referenced artefact missing")

    monkeypatch.setattr(
        "src.pipeline.etl_pipeline_runner.ETLPipelineRunner", _Boom
    )
    config = tmp_path / "p.yaml"
    config.write_text("name: x\n", encoding="utf-8")

    with pytest.raises(ToolError, match="referenced artefact missing"):
        run_etl_pipeline_payload(config=str(config))


# ---------------------------------------------------------------------------
# JSON normalisation helpers
# ---------------------------------------------------------------------------


def test_json_default_unwraps_item_protocol():
    class _Scalar:
        def item(self):
            return 42

    assert _json_default(_Scalar()) == 42


def test_json_default_falls_back_to_str():
    class _Weird:
        def __str__(self):
            return "weird"

    assert _json_default(_Weird()) == "weird"


def test_json_default_str_fallback_when_item_raises():
    class _BadScalar:
        def item(self):
            raise ValueError("no scalar")

        def __str__(self):
            return "bad"

    assert _json_default(_BadScalar()) == "bad"


def test_to_json_native_normalises_nested_scalars():
    class _Scalar:
        def item(self):
            return 7

    out = _to_json_native({"a": {"b": [_Scalar()]}})
    assert out == {"a": {"b": [7]}}
    json.dumps(out)  # confirms native
