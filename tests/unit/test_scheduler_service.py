"""Unit tests for scheduler_service.py — TDD: written before implementation."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typing import Any


# ---------------------------------------------------------------------------
# Suite config model tests (src.pipeline.suite_config)
# ---------------------------------------------------------------------------

class TestSuiteDefinitionModel:
    """Tests for SuiteDefinition and StepDefinition Pydantic models."""

    def test_step_definition_validate_type(self):
        """StepDefinition accepts type='validate'."""
        from src.pipeline.suite_config import StepDefinition
        step = StepDefinition(name="check", type="validate", file_pattern="*.txt")
        assert step.type == "validate"
        assert step.file_pattern == "*.txt"

    def test_step_definition_compare_type(self):
        """StepDefinition accepts type='compare'."""
        from src.pipeline.suite_config import StepDefinition
        step = StepDefinition(name="compare check", type="compare", file_pattern="src.txt")
        assert step.type == "compare"

    def test_step_definition_invalid_type_raises(self):
        """StepDefinition rejects unsupported type values."""
        from src.pipeline.suite_config import StepDefinition
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            StepDefinition(name="bad", type="unknown_type", file_pattern="x.txt")

    def test_step_definition_mapping_optional(self):
        """StepDefinition mapping and rules fields are optional."""
        from src.pipeline.suite_config import StepDefinition
        step = StepDefinition(name="s", type="validate", file_pattern="*.txt")
        assert step.mapping is None
        assert step.rules is None

    def test_step_definition_with_mapping_and_rules(self):
        """StepDefinition stores mapping and rules paths when provided."""
        from src.pipeline.suite_config import StepDefinition
        step = StepDefinition(
            name="s",
            type="validate",
            file_pattern="*.txt",
            mapping="config/mappings/foo.json",
            rules="config/rules/bar.json",
        )
        assert step.mapping == "config/mappings/foo.json"
        assert step.rules == "config/rules/bar.json"

    def test_suite_definition_minimal(self):
        """SuiteDefinition requires only name and steps."""
        from src.pipeline.suite_config import SuiteDefinition, StepDefinition
        suite = SuiteDefinition(
            name="my-suite",
            steps=[StepDefinition(name="s1", type="validate", file_pattern="*.txt")],
        )
        assert suite.name == "my-suite"
        assert len(suite.steps) == 1
        assert suite.description is None

    def test_suite_definition_full(self):
        """SuiteDefinition stores description and thresholds."""
        from src.pipeline.suite_config import SuiteDefinition, StepDefinition
        suite = SuiteDefinition(
            name="full-suite",
            description="A complete test suite",
            steps=[StepDefinition(name="v", type="validate", file_pattern="data/*.txt")],
            thresholds={"max_errors": 5},
        )
        assert suite.description == "A complete test suite"
        assert suite.thresholds == {"max_errors": 5}

    def test_suite_definition_empty_steps_is_valid(self):
        """SuiteDefinition allows empty steps list."""
        from src.pipeline.suite_config import SuiteDefinition
        suite = SuiteDefinition(name="empty", steps=[])
        assert suite.steps == []


# ---------------------------------------------------------------------------
# Scheduler service tests (src.services.scheduler_service)
# ---------------------------------------------------------------------------

class TestLoadSuiteDefinitions:
    """Tests for scheduler_service.load_suite_definitions()."""

    def test_load_returns_empty_list_when_dir_missing(self, tmp_path):
        """Returns [] when suites directory does not exist."""
        from src.services.scheduler_service import load_suite_definitions
        missing_dir = tmp_path / "no_such_dir"
        result = load_suite_definitions(suites_dir=str(missing_dir))
        assert result == []

    def test_load_returns_empty_list_when_dir_empty(self, tmp_path):
        """Returns [] when suites directory contains no YAML files."""
        from src.services.scheduler_service import load_suite_definitions
        result = load_suite_definitions(suites_dir=str(tmp_path))
        assert result == []

    def test_load_reads_single_yaml_file(self, tmp_path):
        """Parses a valid YAML suite file and returns one SuiteDefinition."""
        from src.services.scheduler_service import load_suite_definitions
        suite_yaml = {
            "name": "daily-validate",
            "description": "Daily validation run",
            "steps": [
                {"name": "check customers", "type": "validate", "file_pattern": "data/*.txt"},
            ],
        }
        (tmp_path / "daily.yaml").write_text(yaml.dump(suite_yaml))
        result = load_suite_definitions(suites_dir=str(tmp_path))
        assert len(result) == 1
        assert result[0].name == "daily-validate"
        assert result[0].description == "Daily validation run"

    def test_load_reads_multiple_yaml_files(self, tmp_path):
        """Returns one SuiteDefinition per valid YAML file in the directory."""
        from src.services.scheduler_service import load_suite_definitions
        for i in range(3):
            data = {
                "name": f"suite-{i}",
                "steps": [{"name": "s", "type": "validate", "file_pattern": "*.txt"}],
            }
            (tmp_path / f"suite{i}.yaml").write_text(yaml.dump(data))
        result = load_suite_definitions(suites_dir=str(tmp_path))
        assert len(result) == 3
        names = {s.name for s in result}
        assert names == {"suite-0", "suite-1", "suite-2"}

    def test_load_ignores_non_yaml_files(self, tmp_path):
        """Non-.yaml / .yml files in the directory are silently ignored."""
        from src.services.scheduler_service import load_suite_definitions
        (tmp_path / "readme.txt").write_text("ignore me")
        (tmp_path / "config.json").write_text("{}")
        suite_yaml = {
            "name": "only-suite",
            "steps": [{"name": "s", "type": "validate", "file_pattern": "*.txt"}],
        }
        (tmp_path / "valid.yml").write_text(yaml.dump(suite_yaml))
        result = load_suite_definitions(suites_dir=str(tmp_path))
        assert len(result) == 1
        assert result[0].name == "only-suite"

    def test_load_skips_invalid_yaml_with_warning(self, tmp_path, caplog):
        """Malformed YAML files are skipped and a warning is logged."""
        import logging
        from src.services.scheduler_service import load_suite_definitions
        (tmp_path / "broken.yaml").write_text(": this: is: broken: yaml: {{{")
        with caplog.at_level(logging.WARNING):
            result = load_suite_definitions(suites_dir=str(tmp_path))
        assert result == []
        # Should log a warning about the broken file
        assert any("broken.yaml" in r.message for r in caplog.records)

    def test_load_uses_default_config_suites_dir(self, tmp_path, monkeypatch):
        """When suites_dir is None, defaults to config/suites/ next to project root."""
        from src.services import scheduler_service
        # Patch _default_suites_dir to return tmp_path so we don't depend on disk layout
        monkeypatch.setattr(scheduler_service, "_default_suites_dir", lambda: str(tmp_path))
        suite_yaml = {
            "name": "default-dir-suite",
            "steps": [{"name": "s", "type": "validate", "file_pattern": "*.txt"}],
        }
        (tmp_path / "s.yaml").write_text(yaml.dump(suite_yaml))
        result = scheduler_service.load_suite_definitions()
        assert len(result) == 1
        assert result[0].name == "default-dir-suite"


class TestRunSuiteByName:
    """Tests for scheduler_service.run_suite_by_name()."""

    def _make_suite_yaml(self, name: str, steps: list[dict]) -> dict:
        return {"name": name, "steps": steps}

    def test_run_suite_not_found_raises(self, tmp_path):
        """Raises ValueError when the named suite does not exist in the directory."""
        from src.services.scheduler_service import run_suite_by_name
        result = run_suite_by_name("nonexistent", suites_dir=str(tmp_path))
        assert result["status"] == "error"
        assert "nonexistent" in result["message"]

    def test_run_suite_validate_step_calls_validate_service(self, tmp_path):
        """A 'validate' step delegates to run_validate_service."""
        suite_yaml = {
            "name": "test-suite",
            "steps": [
                {
                    "name": "validate step",
                    "type": "validate",
                    "file_pattern": "data/test.txt",
                    "mapping": "config/mappings/map.json",
                }
            ],
        }
        (tmp_path / "test-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_validate = MagicMock(return_value={"error_count": 0, "total_rows": 100, "valid": True})
        with patch("src.services.scheduler_service.run_validate_service", mock_validate):
            from src.services.scheduler_service import run_suite_by_name
            result = run_suite_by_name("test-suite", suites_dir=str(tmp_path))

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        assert call_kwargs.kwargs.get("file") == "data/test.txt" or call_kwargs.args[0] == "data/test.txt"
        assert result["status"] == "passed"

    def test_run_suite_step_failure_reflected_in_result(self, tmp_path):
        """When validate service returns errors, suite result reflects failure."""
        suite_yaml = {
            "name": "failing-suite",
            "steps": [
                {"name": "bad step", "type": "validate", "file_pattern": "data/bad.txt"},
            ],
        }
        (tmp_path / "failing-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_validate = MagicMock(return_value={"error_count": 5, "total_rows": 10, "valid": False})
        with patch("src.services.scheduler_service.run_validate_service", mock_validate):
            from src.services.scheduler_service import run_suite_by_name
            result = run_suite_by_name("failing-suite", suites_dir=str(tmp_path))

        assert result["status"] == "failed"
        assert result["step_results"][0]["error_count"] == 5

    def test_run_suite_result_includes_suite_name_and_run_id(self, tmp_path):
        """Result dict always includes suite_name and run_id."""
        suite_yaml = {
            "name": "info-suite",
            "steps": [
                {"name": "s", "type": "validate", "file_pattern": "*.txt"},
            ],
        }
        (tmp_path / "info-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_validate = MagicMock(return_value={"error_count": 0, "total_rows": 1, "valid": True})
        with patch("src.services.scheduler_service.run_validate_service", mock_validate):
            from src.services.scheduler_service import run_suite_by_name
            result = run_suite_by_name("info-suite", suites_dir=str(tmp_path))

        assert "suite_name" in result
        assert result["suite_name"] == "info-suite"
        assert "run_id" in result
        assert len(result["run_id"]) > 0

    def test_run_suite_step_exception_marks_step_as_error(self, tmp_path):
        """When validate service raises, the step result has status='error'."""
        suite_yaml = {
            "name": "error-suite",
            "steps": [
                {"name": "boom", "type": "validate", "file_pattern": "missing.txt"},
            ],
        }
        (tmp_path / "error-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_validate = MagicMock(side_effect=FileNotFoundError("file not found"))
        with patch("src.services.scheduler_service.run_validate_service", mock_validate):
            from src.services.scheduler_service import run_suite_by_name
            result = run_suite_by_name("error-suite", suites_dir=str(tmp_path))

        assert result["status"] == "failed"
        assert result["step_results"][0]["status"] == "error"

    def test_run_suite_empty_steps_passes(self, tmp_path):
        """A suite with no steps completes with status 'passed'."""
        suite_yaml = {"name": "empty-suite", "steps": []}
        (tmp_path / "empty-suite.yaml").write_text(yaml.dump(suite_yaml))

        from src.services.scheduler_service import run_suite_by_name
        result = run_suite_by_name("empty-suite", suites_dir=str(tmp_path))

        assert result["status"] == "passed"
        assert result["step_results"] == []

    def test_run_suite_result_includes_step_names(self, tmp_path):
        """Each step result dict contains the step name."""
        suite_yaml = {
            "name": "named-suite",
            "steps": [
                {"name": "step-alpha", "type": "validate", "file_pattern": "a.txt"},
                {"name": "step-beta", "type": "validate", "file_pattern": "b.txt"},
            ],
        }
        (tmp_path / "named-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_validate = MagicMock(return_value={"error_count": 0, "total_rows": 1, "valid": True})
        with patch("src.services.scheduler_service.run_validate_service", mock_validate):
            from src.services.scheduler_service import run_suite_by_name
            result = run_suite_by_name("named-suite", suites_dir=str(tmp_path))

        step_names = [s["name"] for s in result["step_results"]]
        assert step_names == ["step-alpha", "step-beta"]


class TestListSuites:
    """Tests for scheduler_service.list_suites()."""

    def test_list_returns_suite_names_and_descriptions(self, tmp_path):
        """list_suites() returns list of dicts with name and description."""
        from src.services.scheduler_service import list_suites
        for i, desc in enumerate(["First suite", None]):
            data: dict[str, Any] = {
                "name": f"suite-{i}",
                "steps": [],
            }
            if desc:
                data["description"] = desc
            (tmp_path / f"s{i}.yaml").write_text(yaml.dump(data))

        result = list_suites(suites_dir=str(tmp_path))
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"suite-0", "suite-1"}

    def test_list_returns_empty_when_no_suites(self, tmp_path):
        """list_suites() returns [] when directory is empty."""
        from src.services.scheduler_service import list_suites
        result = list_suites(suites_dir=str(tmp_path))
        assert result == []

    def test_list_includes_step_count(self, tmp_path):
        """Each entry in list_suites() includes the number of steps."""
        from src.services.scheduler_service import list_suites
        data = {
            "name": "multi-step",
            "steps": [
                {"name": "s1", "type": "validate", "file_pattern": "a.txt"},
                {"name": "s2", "type": "validate", "file_pattern": "b.txt"},
            ],
        }
        (tmp_path / "multi.yaml").write_text(yaml.dump(data))
        result = list_suites(suites_dir=str(tmp_path))
        assert result[0]["step_count"] == 2


# ---------------------------------------------------------------------------
# CLI command tests (src.commands.schedule_command)
# ---------------------------------------------------------------------------

class TestScheduleCliCommands:
    """Tests for schedule CLI command group."""

    def test_schedule_list_command_exists(self):
        """schedule list command is registered and importable."""
        from src.commands.schedule_command import schedule
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(schedule, ["list", "--help"])
        assert result.exit_code == 0

    def test_schedule_run_command_exists(self):
        """schedule run command is registered and importable."""
        from src.commands.schedule_command import schedule
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(schedule, ["run", "--help"])
        assert result.exit_code == 0

    def test_schedule_list_output(self, tmp_path):
        """schedule list prints suite names to stdout."""
        suite_yaml = {
            "name": "my-suite",
            "description": "Test description",
            "steps": [{"name": "s", "type": "validate", "file_pattern": "*.txt"}],
        }
        (tmp_path / "my.yaml").write_text(yaml.dump(suite_yaml))

        from src.commands.schedule_command import schedule
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(schedule, ["list", "--suites-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-suite" in result.output

    def test_schedule_list_no_suites_message(self, tmp_path):
        """schedule list prints a helpful message when no suites are defined."""
        from src.commands.schedule_command import schedule
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(schedule, ["list", "--suites-dir", str(tmp_path)])
        assert result.exit_code == 0
        # Should say "no suites" or similar
        assert "no suite" in result.output.lower() or "0" in result.output

    def test_schedule_run_suite_not_found(self, tmp_path):
        """schedule run exits with error when suite name is not found."""
        from src.commands.schedule_command import schedule
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(
            schedule, ["run", "nonexistent-suite", "--suites-dir", str(tmp_path)]
        )
        # Should indicate failure (non-zero exit or error message)
        assert result.exit_code != 0 or "error" in result.output.lower() or "not found" in result.output.lower()

    def test_schedule_run_delegates_to_service(self, tmp_path):
        """schedule run delegates to scheduler_service.run_suite_by_name."""
        suite_yaml = {
            "name": "cli-suite",
            "steps": [{"name": "s", "type": "validate", "file_pattern": "*.txt"}],
        }
        (tmp_path / "cli-suite.yaml").write_text(yaml.dump(suite_yaml))

        mock_result = {
            "suite_name": "cli-suite",
            "run_id": "abc123",
            "status": "passed",
            "step_results": [],
        }
        with patch("src.commands.schedule_command.run_suite_by_name", return_value=mock_result) as mock_run:
            from src.commands.schedule_command import schedule
            from click.testing import CliRunner
            runner = CliRunner()
            result = runner.invoke(
                schedule, ["run", "cli-suite", "--suites-dir", str(tmp_path)]
            )

        mock_run.assert_called_once()
        assert "cli-suite" in mock_run.call_args.kwargs.get("suite_name", "") or \
               "cli-suite" in str(mock_run.call_args)


# ---------------------------------------------------------------------------
# API endpoint tests (src.api.routers.runs — new schedule endpoints)
# ---------------------------------------------------------------------------

class TestScheduleApiEndpoints:
    """Tests for GET /api/v1/schedules and POST /api/v1/schedules/run."""

    def test_get_schedules_returns_200(self):
        """GET /api/v1/schedules returns 200 with a list."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        with patch("src.services.scheduler_service.list_suites", return_value=[]):
            client = TestClient(app)
            resp = client.get("/api/v1/schedules")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_schedules_returns_suite_list(self):
        """GET /api/v1/schedules returns suites from scheduler_service.list_suites."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        mock_suites = [
            {"name": "suite-a", "description": "First", "step_count": 2},
            {"name": "suite-b", "description": None, "step_count": 1},
        ]
        with patch("src.services.scheduler_service.list_suites", return_value=mock_suites):
            client = TestClient(app)
            resp = client.get("/api/v1/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "suite-a"

    def test_post_schedules_run_returns_202(self):
        """POST /api/v1/schedules/run returns 202 Accepted."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        mock_result = {
            "suite_name": "test-suite",
            "run_id": "xyz789",
            "status": "passed",
            "step_results": [],
        }
        with patch("src.services.scheduler_service.run_suite_by_name", return_value=mock_result):
            client = TestClient(app)
            resp = client.post("/api/v1/schedules/run", json={"suite_name": "test-suite"})
        assert resp.status_code == 202

    def test_post_schedules_run_returns_run_id(self):
        """POST /api/v1/schedules/run response includes run_id."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        mock_result = {
            "suite_name": "run-suite",
            "run_id": "run-xyz",
            "status": "passed",
            "step_results": [],
        }
        with patch("src.services.scheduler_service.run_suite_by_name", return_value=mock_result):
            client = TestClient(app)
            resp = client.post("/api/v1/schedules/run", json={"suite_name": "run-suite"})
        payload = resp.json()
        assert "run_id" in payload
        assert payload["run_id"] == "run-xyz"

    def test_post_schedules_run_suite_not_found_returns_error(self):
        """POST /api/v1/schedules/run returns error status when suite not found."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        mock_result = {
            "suite_name": "ghost-suite",
            "run_id": "",
            "status": "error",
            "message": "Suite 'ghost-suite' not found",
            "step_results": [],
        }
        with patch("src.services.scheduler_service.run_suite_by_name", return_value=mock_result):
            client = TestClient(app)
            resp = client.post("/api/v1/schedules/run", json={"suite_name": "ghost-suite"})
        # Accepts either 202 with error status in body, or 404
        assert resp.status_code in (202, 404)

    def test_post_schedules_run_missing_body_returns_422(self):
        """POST /api/v1/schedules/run with empty body returns 422."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)
        resp = client.post("/api/v1/schedules/run", json={})
        assert resp.status_code == 422
