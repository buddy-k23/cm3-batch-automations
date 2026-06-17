"""Command-module + CLI-parity tests for ``valdo submit-task`` (S-followup, #422).

Pins the behaviour of the relocated ``submit-task`` logic now living in
:mod:`src.commands.submit_task_command`: invalid-JSON handling, contract
validation failure, idempotency-key deduplication early return, and the
happy-path queue/create flow.  Exercised both through the extracted command
function and the thin CLI stub (``CliRunner``) for parity.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.commands.submit_task_command import run_submit_task_command
from src.main import cli


# ---------------------------------------------------------------------------
# Invalid JSON payload
# ---------------------------------------------------------------------------
class TestInvalidPayload:
    def test_invalid_json_machine_errors_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            run_submit_task_command(
                intent="validate", payload="{not-json}", task_id=None,
                trace_id=None, idempotency_key=None, priority="normal",
                deadline=None, machine_errors=True,
            )
        assert exc.value.code == 2
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["errors"][0]["code"] == "INVALID_JSON"

    def test_invalid_json_human_readable_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            run_submit_task_command(
                intent="validate", payload="{nope}", task_id=None,
                trace_id=None, idempotency_key=None, priority="normal",
                deadline=None, machine_errors=False,
            )
        assert exc.value.code == 2
        assert "Invalid payload JSON" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Contract validation failure
# ---------------------------------------------------------------------------
class TestContractValidation:
    def test_contract_errors_exit_2(self, capsys):
        err = MagicMock()
        err.model_dump.return_value = {"code": "BAD", "message": "nope", "path": "x"}
        with patch(
            "src.contracts.validation.validate_task_request",
            return_value=(None, [err]),
        ), patch("src.adapters.cli_task_adapter.normalize_cli_task_request") as norm:
            norm.return_value = MagicMock(model_dump=lambda: {})
            with pytest.raises(SystemExit) as exc:
                run_submit_task_command(
                    intent="validate", payload="{}", task_id=None,
                    trace_id=None, idempotency_key=None, priority="normal",
                    deadline=None, machine_errors=True,
                )
        assert exc.value.code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["errors"][0]["code"] == "BAD"


# ---------------------------------------------------------------------------
# Idempotency dedup early return + happy path
# ---------------------------------------------------------------------------
class TestStoreFlow:
    def _patch_valid(self):
        req = MagicMock()
        req.model_dump.return_value = {}
        req.idempotency_key = "idem-1"
        req.intent = "validate"
        req.source = "cli"
        req.task_id = "t1"
        req.trace_id = "tr1"
        return req

    def test_idempotency_dedup_returns_without_create(self, capsys):
        req = self._patch_valid()
        store = MagicMock()
        store.get_by_idempotency_key.return_value = {
            "task_id": "existing", "trace_id": "trX", "status": "queued",
            "result": {"x": 1},
        }
        with patch(
            "src.adapters.cli_task_adapter.normalize_cli_task_request",
            return_value=req,
        ), patch(
            "src.contracts.validation.validate_task_request",
            return_value=(req, []),
        ), patch(
            "src.services.job_state_store.JobStateStore", return_value=store
        ):
            run_submit_task_command(
                intent="validate", payload="{}", task_id=None,
                trace_id=None, idempotency_key="idem-1", priority="normal",
                deadline=None, machine_errors=False,
            )
        store.create.assert_not_called()
        out = json.loads(capsys.readouterr().out)
        assert out["task_id"] == "existing"
        assert out["warnings"] == ["duplicate idempotency key"]

    def test_happy_path_creates_and_emits_result(self, capsys):
        req = self._patch_valid()
        req.idempotency_key = None
        store = MagicMock()
        with patch(
            "src.adapters.cli_task_adapter.normalize_cli_task_request",
            return_value=req,
        ), patch(
            "src.contracts.validation.validate_task_request",
            return_value=(req, []),
        ), patch(
            "src.services.job_state_store.JobStateStore", return_value=store
        ):
            run_submit_task_command(
                intent="validate", payload="{}", task_id=None,
                trace_id=None, idempotency_key=None, priority="normal",
                deadline=None, machine_errors=False,
            )
        store.create.assert_called_once()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "queued"
        assert out["result"] == {"accepted": True}


# ---------------------------------------------------------------------------
# CLI parity — the thin stub delegates to the command module
# ---------------------------------------------------------------------------
class TestSubmitTaskCliParity:
    def test_cli_invalid_json_machine_errors_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["submit-task", "--intent", "validate", "--payload",
             "{not-json}", "--machine-errors"],
        )
        assert result.exit_code != 0
        payload = json.loads(result.output)
        assert payload["errors"][0]["code"] == "INVALID_JSON"
