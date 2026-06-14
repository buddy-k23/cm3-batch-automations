"""Unit tests for the ETL pipeline gate orchestrator (issue #156).

Tests are written before implementation (TDD red phase) and cover:
  - Pipeline config loading from YAML
  - Gate execution with mock service delegates
  - for_each source expansion
  - Template variable expansion
  - Blocking gate stops pipeline on failure
  - Non-blocking gate continues pipeline on failure
  - Threshold evaluation
  - Aggregate results structure
"""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.pipeline.etl_config import (
    Gate,
    GateStep,
    PipelineDefinition,
    SourceDefinition,
    ThresholdConfig,
)
from src.pipeline.etl_pipeline_runner import ETLPipelineRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def minimal_pipeline_yaml(tmp_path):
    """Write a minimal pipeline YAML to a temp file and return the path."""
    content = textwrap.dedent("""\
        name: test-pipeline
        description: Unit test pipeline

        sources:
          - name: customers
            mapping: config/mappings/customers.json
            input_path: data/customers.txt

        gates:
          - name: input_validation
            blocking: true
            steps:
              - type: validate
                file: "{source.input_path}"
                mapping: "{source.mapping}"
                thresholds:
                  max_error_pct: 5.0
    """)
    p = tmp_path / "pipeline.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture()
def two_source_pipeline_yaml(tmp_path):
    """Write a multi-source pipeline YAML to a temp file and return the path."""
    content = textwrap.dedent("""\
        name: multi-source-pipeline
        description: Two sources, one gate each

        sources:
          - name: accounts
            mapping: config/mappings/accounts.json
            input_path: data/accounts.txt
          - name: transactions
            mapping: config/mappings/transactions.json
            input_path: data/transactions.txt

        gates:
          - name: validate_all
            for_each: source
            blocking: true
            steps:
              - type: validate
                file: "{source.input_path}"
                mapping: "{source.mapping}"
    """)
    p = tmp_path / "multi_pipeline.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture()
def non_blocking_gate_yaml(tmp_path):
    """Pipeline with one blocking gate followed by one non-blocking gate."""
    content = textwrap.dedent("""\
        name: non-blocking-pipeline

        sources:
          - name: source_a
            mapping: config/mappings/source_a.json
            input_path: data/source_a.txt

        gates:
          - name: gate_pass
            blocking: true
            steps:
              - type: validate
                file: data/source_a.txt
                mapping: config/mappings/source_a.json

          - name: gate_nonblocking_fail
            blocking: false
            steps:
              - type: validate
                file: data/source_a.txt
                mapping: config/mappings/source_a.json
    """)
    p = tmp_path / "nb_pipeline.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------


class TestPipelineDefinition:
    """Tests for Pydantic config models in etl_config."""

    def test_source_definition_defaults(self):
        """SourceDefinition provides sensible defaults for optional fields."""
        src = SourceDefinition(name="test", mapping="config/mappings/test.json")
        assert src.name == "test"
        assert src.mapping == "config/mappings/test.json"
        assert src.rules == ""
        assert src.output_pattern == ""
        assert src.input_path == ""
        assert src.target_mapping == ""
        assert src.staging_tables == []

    def test_threshold_config_defaults(self):
        """ThresholdConfig uses -1 sentinel to mean 'no threshold'."""
        t = ThresholdConfig()
        assert t.max_error_pct == -1
        assert t.max_errors == -1
        assert t.min_rows == -1

    def test_threshold_config_custom(self):
        """ThresholdConfig accepts explicit values."""
        t = ThresholdConfig(max_error_pct=5.0, max_errors=100, min_rows=50)
        assert t.max_error_pct == 5.0
        assert t.max_errors == 100
        assert t.min_rows == 50

    def test_gate_step_defaults(self):
        """GateStep defaults are empty strings and empty lists."""
        step = GateStep(type="validate")
        assert step.type == "validate"
        assert step.file == ""
        assert step.mapping == ""
        assert step.rules == ""
        assert step.query == ""
        assert step.key_columns == []
        assert isinstance(step.thresholds, ThresholdConfig)

    def test_gate_defaults(self):
        """Gate defaults: non-blocking is false, for_each is empty."""
        gate = Gate(name="my_gate", steps=[GateStep(type="validate")])
        assert gate.blocking is True
        assert gate.for_each == ""
        assert gate.stage == ""
        assert gate.description == ""

    def test_pipeline_definition_requires_name_and_gates(self):
        """PipelineDefinition requires name and at least the gates list."""
        with pytest.raises(Exception):
            PipelineDefinition(gates=[])  # missing name

    def test_pipeline_definition_full(self):
        """PipelineDefinition parses a complete config correctly."""
        pipeline = PipelineDefinition(
            name="test-etl",
            description="ETL test",
            sources=[
                SourceDefinition(name="src1", mapping="m.json", input_path="f.txt")
            ],
            gates=[
                Gate(
                    name="gate1",
                    blocking=True,
                    steps=[GateStep(type="validate", file="f.txt")],
                )
            ],
        )
        assert pipeline.name == "test-etl"
        assert len(pipeline.sources) == 1
        assert len(pipeline.gates) == 1


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for YAML loading in ETLPipelineRunner."""

    def test_load_valid_yaml(self, minimal_pipeline_yaml):
        """Runner loads a valid pipeline YAML and returns PipelineDefinition."""
        runner = ETLPipelineRunner()
        pipeline = runner.load_config(minimal_pipeline_yaml)
        assert isinstance(pipeline, PipelineDefinition)
        assert pipeline.name == "test-pipeline"
        assert len(pipeline.sources) == 1
        assert pipeline.sources[0].name == "customers"

    def test_load_missing_file_raises(self):
        """Runner raises FileNotFoundError for a missing config path."""
        runner = ETLPipelineRunner()
        with pytest.raises(FileNotFoundError):
            runner.load_config("/nonexistent/pipeline.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path):
        """Runner raises a descriptive error on malformed YAML."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: [unclosed", encoding="utf-8")
        runner = ETLPipelineRunner()
        with pytest.raises(Exception):
            runner.load_config(str(bad))


# ---------------------------------------------------------------------------
# Template expansion tests
# ---------------------------------------------------------------------------


class TestExpandTemplate:
    """Tests for _expand_template helper."""

    def test_expand_source_name(self):
        """Expands {source.name} to the source's name field."""
        runner = ETLPipelineRunner()
        source = {"name": "customers", "mapping": "c.json", "input_path": "c.txt"}
        result = runner._expand_template("{source.name}", source, {})
        assert result == "customers"

    def test_expand_source_mapping(self):
        """Expands {source.mapping}."""
        runner = ETLPipelineRunner()
        source = {"name": "x", "mapping": "config/mappings/x.json", "input_path": ""}
        result = runner._expand_template("{source.mapping}", source, {})
        assert result == "config/mappings/x.json"

    def test_expand_run_date(self):
        """Expands {run_date} from params dict."""
        runner = ETLPipelineRunner()
        result = runner._expand_template(
            "data_{run_date}.txt", {}, {"run_date": "20260326"}
        )
        assert result == "data_20260326.txt"

    def test_expand_no_placeholders(self):
        """Returns original string unchanged when no placeholders present."""
        runner = ETLPipelineRunner()
        result = runner._expand_template("plain/path.txt", {}, {})
        assert result == "plain/path.txt"

    def test_expand_multiple_placeholders(self):
        """Expands multiple source placeholders in one string."""
        runner = ETLPipelineRunner()
        source = {"name": "acct", "mapping": "acct.json", "input_path": "acct.txt"}
        tpl = "output/{source.name}_validated.json"
        result = runner._expand_template(tpl, source, {})
        assert result == "output/acct_validated.json"

    def test_expand_unknown_placeholder_left_intact(self):
        """Unknown placeholders are left as-is (no KeyError)."""
        runner = ETLPipelineRunner()
        result = runner._expand_template("{unknown.field}", {}, {})
        assert "{unknown.field}" in result


# ---------------------------------------------------------------------------
# Threshold evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateThresholds:
    """Tests for _evaluate_thresholds helper."""

    def test_no_thresholds_always_passes(self):
        """When all thresholds are -1 (disabled), result is always True."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig()  # all -1
        assert runner._evaluate_thresholds({"total_rows": 0, "error_count": 9999}, thresholds) is True

    def test_max_error_pct_passes(self):
        """Result passes when error_pct is below max_error_pct."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_error_pct=5.0)
        result = {"total_rows": 100, "error_count": 4}
        assert runner._evaluate_thresholds(result, thresholds) is True

    def test_max_error_pct_fails(self):
        """Result fails when error_pct exceeds max_error_pct."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_error_pct=5.0)
        result = {"total_rows": 100, "error_count": 10}
        assert runner._evaluate_thresholds(result, thresholds) is False

    def test_max_errors_passes(self):
        """Result passes when error_count is at or below max_errors."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_errors=10)
        result = {"total_rows": 1000, "error_count": 10}
        assert runner._evaluate_thresholds(result, thresholds) is True

    def test_max_errors_fails(self):
        """Result fails when error_count exceeds max_errors."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_errors=10)
        result = {"total_rows": 1000, "error_count": 11}
        assert runner._evaluate_thresholds(result, thresholds) is False

    def test_min_rows_passes(self):
        """Result passes when total_rows meets or exceeds min_rows."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(min_rows=50)
        result = {"total_rows": 100, "error_count": 0}
        assert runner._evaluate_thresholds(result, thresholds) is True

    def test_min_rows_fails(self):
        """Result fails when total_rows is below min_rows."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(min_rows=50)
        result = {"total_rows": 10, "error_count": 0}
        assert runner._evaluate_thresholds(result, thresholds) is False

    def test_zero_total_rows_max_error_pct(self):
        """With zero total rows, max_error_pct check treats 0% error as passing."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_error_pct=5.0)
        result = {"total_rows": 0, "error_count": 0}
        assert runner._evaluate_thresholds(result, thresholds) is True

    def test_compare_result_with_workflow_key(self):
        """DB-compare results with a workflow wrapper are handled correctly."""
        runner = ETLPipelineRunner()
        thresholds = ThresholdConfig(max_errors=0)
        # compare result wrapped like db_file_compare_service returns
        result = {
            "workflow": {"status": "failed"},
            "compare": {"rows_with_differences": 5, "total_rows": 100},
        }
        # Should extract relevant metrics and fail because differences > 0
        assert runner._evaluate_thresholds(result, thresholds) is False


# ---------------------------------------------------------------------------
# Step execution tests
# ---------------------------------------------------------------------------


class TestExecuteStep:
    """Tests for _execute_step routing."""

    def test_execute_validate_step(self):
        """validate step delegates to run_validate_service."""
        runner = ETLPipelineRunner()
        step = GateStep(
            type="validate",
            file="data/test.txt",
            mapping="config/mappings/test.json",
        )
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service"
        ) as mock_svc:
            mock_svc.return_value = {"valid": True, "error_count": 0, "total_rows": 100}
            result = runner._execute_step(step, {})

        mock_svc.assert_called_once()
        call_kwargs = mock_svc.call_args
        assert call_kwargs.kwargs.get("file") == "data/test.txt" or call_kwargs.args[0] == "data/test.txt"
        assert result["valid"] is True

    def test_execute_compare_step(self):
        """compare step delegates to run_compare_service."""
        runner = ETLPipelineRunner()
        step = GateStep(
            type="compare",
            file="data/output.txt",
            mapping="config/mappings/test.json",
        )
        with patch(
            "src.pipeline.etl_pipeline_runner.run_compare_service"
        ) as mock_svc:
            mock_svc.return_value = {"structure_compatible": True, "rows_with_differences": 0}
            result = runner._execute_step(step, {})

        mock_svc.assert_called_once()
        assert result["structure_compatible"] is True

    def test_execute_db_compare_step(self):
        """db_compare step delegates to compare_db_to_file."""
        runner = ETLPipelineRunner()
        step = GateStep(
            type="db_compare",
            query="SELECT * FROM staging",
            file="data/actual.txt",
            mapping="config/mappings/test.json",
            key_columns=["ACCT_KEY"],
        )
        fake_mapping = {"fields": [{"name": "ACCT_KEY"}]}
        with patch(
            "src.pipeline.etl_pipeline_runner._load_mapping_config",
            return_value=fake_mapping,
        ), patch(
            "src.pipeline.etl_pipeline_runner.compare_db_to_file"
        ) as mock_svc:
            mock_svc.return_value = {
                "workflow": {"status": "passed"},
                "compare": {"rows_with_differences": 0},
            }
            result = runner._execute_step(step, {})

        mock_svc.assert_called_once()
        assert result["workflow"]["status"] == "passed"

    def test_execute_validate_multi_record_step(self, tmp_path):
        """validate_multi_record dispatches to run_multi_record_validate_service.

        Per ADR 0005, the dispatcher reads the umbrella YAML at
        ``step.mapping`` and forwards its text as ``config_yaml`` to the
        service. The raw service result is augmented with a synthesized
        ``error_count`` so ``_evaluate_thresholds`` works uniformly.
        """
        umbrella = tmp_path / "umbrella.yaml"
        umbrella.write_text("# fake umbrella body\n", encoding="utf-8")
        runner = ETLPipelineRunner()
        step = GateStep(
            type="validate_multi_record",
            file="data/atoctran.txt",
            mapping=str(umbrella),
        )
        with patch(
            "src.pipeline.etl_pipeline_runner.run_multi_record_validate_service"
        ) as mock_svc:
            mock_svc.return_value = {
                "valid": True,
                "total_rows": 42,
                "record_type_results": {
                    "rt_100": {"valid": True, "error_count": 0},
                    "rt_200": {"valid": True, "error_count": 0},
                },
                "cross_type_violations": [],
            }
            result = runner._execute_step(step, {})

        mock_svc.assert_called_once()
        call_kwargs = mock_svc.call_args
        # File path is forwarded verbatim.
        assert call_kwargs.kwargs.get("file_path") == "data/atoctran.txt"
        # Umbrella file content is forwarded as a string, not its path.
        assert call_kwargs.kwargs.get("config_yaml") == "# fake umbrella body\n"
        # Raw fields preserved; error_count synthesized at zero on a clean run.
        assert result["valid"] is True
        assert result["total_rows"] == 42
        assert result["error_count"] == 0

    def test_validate_multi_record_synthesizes_error_count(self, tmp_path):
        """error_count = cross-type errors + per-type error_count sum.

        ``_evaluate_thresholds`` only knows about ``error_count``; the
        service does not produce one, so the dispatcher must compute it
        from per-type ``error_count`` and cross-type error-severity
        violations. Warnings are not counted.
        """
        umbrella = tmp_path / "umbrella.yaml"
        umbrella.write_text("# fake\n", encoding="utf-8")
        runner = ETLPipelineRunner()
        step = GateStep(
            type="validate_multi_record",
            file="data/atoctran.txt",
            mapping=str(umbrella),
        )
        with patch(
            "src.pipeline.etl_pipeline_runner.run_multi_record_validate_service"
        ) as mock_svc:
            mock_svc.return_value = {
                "valid": False,
                "total_rows": 10,
                "record_type_results": {
                    "rt_100": {"valid": False, "error_count": 3},
                    "rt_200": {"valid": True, "error_count": 0},
                    "rt_300": {"valid": False, "error_count": 2},
                },
                "cross_type_violations": [
                    {"check": "header_trailer_count", "severity": "error"},
                    {"check": "type_sequence", "severity": "warning"},
                ],
            }
            result = runner._execute_step(step, {})

        # 3 + 0 + 2 per-type errors + 1 cross-type error (the warning is
        # excluded) = 6.
        assert result["error_count"] == 6
        assert result["valid"] is False

    def test_execute_unknown_step_type_raises(self):
        """Unknown step type raises ValueError with descriptive message."""
        runner = ETLPipelineRunner()
        step = GateStep(type="unknown_type")
        with pytest.raises(ValueError, match="unknown_type"):
            runner._execute_step(step, {})

    def test_unknown_step_type_message_lists_multi_record(self):
        """The error message must advertise the new dispatchable step type."""
        runner = ETLPipelineRunner()
        step = GateStep(type="nope")
        with pytest.raises(ValueError, match="validate_multi_record"):
            runner._execute_step(step, {})

    def test_step_dispatch_goes_through_registry(self):
        """Dispatch is registry-driven (arch-review R-11), not a type-switch.

        ``_step_handlers`` is the single source of truth for recognised step
        types; ``_execute_step`` must look the handler up there. Every key
        maps to a callable, and the key set matches the documented step types.
        """
        runner = ETLPipelineRunner()
        handlers = runner._step_handlers()
        assert set(handlers) == {
            "validate",
            "validate_multi_record",
            "compare",
            "db_compare",
            "reconcile",
        }
        assert all(callable(h) for h in handlers.values())

    def test_register_new_step_type_without_editing_switch(self):
        """A new step type is added via the registry, not by editing a switch.

        Injecting an entry into the mapping returned by ``_step_handlers`` is
        sufficient for ``_execute_step`` to dispatch to it, demonstrating the
        "add, don't edit-the-switch" extensibility property.
        """
        runner = ETLPipelineRunner()
        sentinel = {"valid": True, "custom": True}
        original = runner._step_handlers

        def _patched():
            handlers = original()
            handlers["custom_step"] = lambda step: sentinel
            return handlers

        runner._step_handlers = _patched  # type: ignore[method-assign]
        result = runner._execute_step(GateStep(type="custom_step"), {})
        assert result is sentinel

    def test_unknown_step_type_message_lists_all_registered_types(self):
        """The ValueError advertises every registered step type."""
        runner = ETLPipelineRunner()
        with pytest.raises(ValueError) as exc:
            runner._execute_step(GateStep(type="bogus"), {})
        message = str(exc.value)
        for step_type in runner._step_handlers():
            assert step_type in message

    def test_execute_step_with_rules(self):
        """validate step passes rules to run_validate_service when provided."""
        runner = ETLPipelineRunner()
        step = GateStep(
            type="validate",
            file="data/test.txt",
            mapping="config/mappings/test.json",
            rules="config/rules/test_rules.json",
        )
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service"
        ) as mock_svc:
            mock_svc.return_value = {"valid": True, "error_count": 0, "total_rows": 50}
            runner._execute_step(step, {})

        call_kwargs = mock_svc.call_args
        # rules should be forwarded
        passed_rules = call_kwargs.kwargs.get("rules") or (
            call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
        )
        assert passed_rules == "config/rules/test_rules.json"


# ---------------------------------------------------------------------------
# Pipeline run tests
# ---------------------------------------------------------------------------


class TestRunPipeline:
    """Integration-style tests for the full run_pipeline method."""

    def _make_passing_validate(self):
        """Return a mock run_validate_service that always passes."""
        mock = MagicMock(
            return_value={"valid": True, "error_count": 0, "total_rows": 100}
        )
        return mock

    def _make_failing_validate(self):
        """Return a mock run_validate_service that always fails."""
        mock = MagicMock(
            return_value={"valid": False, "error_count": 20, "total_rows": 100}
        )
        return mock

    def test_all_gates_pass_returns_passed(self, minimal_pipeline_yaml):
        """Pipeline returns 'passed' when all gate steps pass."""
        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            return_value={"valid": True, "error_count": 0, "total_rows": 100},
        ):
            result = runner.run_pipeline(minimal_pipeline_yaml)

        assert result["status"] == "passed"
        assert len(result["gates"]) == 1
        assert result["gates"][0]["status"] == "passed"

    def test_blocking_gate_failure_stops_pipeline(self, two_source_pipeline_yaml, tmp_path):
        """A blocking gate failure sets pipeline status to 'failed' and stops."""
        # Two-gate pipeline: first gate blocks, second should be skipped
        content = textwrap.dedent("""\
            name: blocking-test

            sources:
              - name: src
                mapping: config/mappings/s.json
                input_path: data/s.txt

            gates:
              - name: gate_fails
                blocking: true
                steps:
                  - type: validate
                    file: data/s.txt
                    mapping: config/mappings/s.json

              - name: gate_skipped
                blocking: true
                steps:
                  - type: validate
                    file: data/s.txt
                    mapping: config/mappings/s.json
        """)
        p = tmp_path / "blocking.yaml"
        p.write_text(content, encoding="utf-8")

        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            return_value={"valid": False, "error_count": 50, "total_rows": 100},
        ):
            result = runner.run_pipeline(str(p))

        assert result["status"] == "failed"
        # Second gate must be skipped
        executed_names = [g["name"] for g in result["gates"]]
        assert "gate_fails" in executed_names
        assert "gate_skipped" not in executed_names

    def test_non_blocking_gate_failure_continues(self, non_blocking_gate_yaml):
        """A non-blocking gate failure does not stop the pipeline."""
        runner = ETLPipelineRunner()
        call_count = {"n": 0}

        def alternating_validate(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"valid": True, "error_count": 0, "total_rows": 100}
            # second call (non-blocking gate) fails
            return {"valid": False, "error_count": 10, "total_rows": 100}

        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            side_effect=alternating_validate,
        ):
            result = runner.run_pipeline(non_blocking_gate_yaml)

        assert len(result["gates"]) == 2
        gate_names = [g["name"] for g in result["gates"]]
        assert "gate_nonblocking_fail" in gate_names

    def test_for_each_source_runs_per_source(self, two_source_pipeline_yaml):
        """for_each=source runs the gate steps once per source."""
        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            return_value={"valid": True, "error_count": 0, "total_rows": 200},
        ) as mock_svc:
            result = runner.run_pipeline(two_source_pipeline_yaml)

        # One gate with two sources → two validate calls
        assert mock_svc.call_count == 2
        assert result["status"] == "passed"

    def test_template_expansion_in_for_each(self, two_source_pipeline_yaml):
        """Source template variables are correctly expanded in step fields."""
        runner = ETLPipelineRunner()
        captured_calls = []

        def capture_validate(**kwargs):
            captured_calls.append(kwargs.get("file", ""))
            return {"valid": True, "error_count": 0, "total_rows": 100}

        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            side_effect=capture_validate,
        ):
            runner.run_pipeline(two_source_pipeline_yaml)

        assert "data/accounts.txt" in captured_calls
        assert "data/transactions.txt" in captured_calls

    def test_run_date_param_expansion(self, tmp_path):
        """run_date param is expanded in step file templates."""
        content = textwrap.dedent("""\
            name: date-param-pipeline

            gates:
              - name: date_gate
                blocking: true
                steps:
                  - type: validate
                    file: "data/batch_{run_date}.txt"
                    mapping: config/mappings/batch.json
        """)
        p = tmp_path / "date_pipeline.yaml"
        p.write_text(content, encoding="utf-8")

        runner = ETLPipelineRunner()
        captured = []

        def capture(**kwargs):
            captured.append(kwargs.get("file", ""))
            return {"valid": True, "error_count": 0, "total_rows": 10}

        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            side_effect=capture,
        ):
            runner.run_pipeline(str(p), run_date="20260326")

        assert "data/batch_20260326.txt" in captured

    def test_result_structure(self, minimal_pipeline_yaml):
        """run_pipeline returns a dict with expected top-level keys."""
        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            return_value={"valid": True, "error_count": 0, "total_rows": 10},
        ):
            result = runner.run_pipeline(minimal_pipeline_yaml)

        assert "status" in result
        assert "pipeline_name" in result
        assert "gates" in result
        assert "started_at" in result
        assert "finished_at" in result

    def test_params_dict_passed_through(self, tmp_path):
        """Custom params are accessible in template expansion."""
        content = textwrap.dedent("""\
            name: custom-params-pipeline

            gates:
              - name: g
                blocking: true
                steps:
                  - type: validate
                    file: "data/{env}/file.txt"
                    mapping: config/mappings/test.json
        """)
        p = tmp_path / "params_pipeline.yaml"
        p.write_text(content, encoding="utf-8")

        runner = ETLPipelineRunner()
        captured = []

        def capture(**kwargs):
            captured.append(kwargs.get("file", ""))
            return {"valid": True, "error_count": 0, "total_rows": 5}

        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            side_effect=capture,
        ):
            runner.run_pipeline(str(p), params={"env": "staging"})

        assert "data/staging/file.txt" in captured

    def test_threshold_breach_fails_gate(self, tmp_path):
        """A step whose result breaches the threshold marks the gate as failed."""
        content = textwrap.dedent("""\
            name: threshold-pipeline

            gates:
              - name: strict_gate
                blocking: true
                steps:
                  - type: validate
                    file: data/test.txt
                    mapping: config/mappings/test.json
                    thresholds:
                      max_error_pct: 1.0
        """)
        p = tmp_path / "threshold_pipeline.yaml"
        p.write_text(content, encoding="utf-8")

        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            return_value={"valid": True, "error_count": 5, "total_rows": 100},
        ):
            result = runner.run_pipeline(str(p))

        assert result["status"] == "failed"
        assert result["gates"][0]["status"] == "failed"

    def test_step_exception_marks_gate_failed(self, tmp_path):
        """An exception raised by a service step marks the gate and pipeline as failed."""
        content = textwrap.dedent("""\
            name: exception-pipeline

            gates:
              - name: boom_gate
                blocking: true
                steps:
                  - type: validate
                    file: data/test.txt
                    mapping: config/mappings/test.json
        """)
        p = tmp_path / "exception_pipeline.yaml"
        p.write_text(content, encoding="utf-8")

        runner = ETLPipelineRunner()
        with patch(
            "src.pipeline.etl_pipeline_runner.run_validate_service",
            side_effect=RuntimeError("service exploded"),
        ):
            result = runner.run_pipeline(str(p))

        assert result["status"] == "failed"
        gate = result["gates"][0]
        assert gate["status"] == "failed"
        assert "service exploded" in gate.get("error", "")
