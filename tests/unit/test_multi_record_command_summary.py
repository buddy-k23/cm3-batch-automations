"""Unit tests pinning the ``run_multi_record_command`` CLI summary output.

Regression coverage for the cosmetic reporter fix where:

* The header verb (``✓ passed`` vs ``✗ failed``) reflects the ``error_count``
  printed underneath it — NOT the raw ``result["valid"]`` flag (which can be
  False for benign per-type structural reasons even when no cross-type
  errors fire).
* Per-record-type lines use a neutral bullet (``•``) by default. A ``✗``
  marker is only emitted when ``cross_type_violations`` proves the type
  failed an ``expect`` cardinality check (``issue_code`` starts with
  ``CT_EXPECT_``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _write_dummy_yaml_config(tmp_path: Path) -> Path:
    """Write a minimal-but-valid multi-record YAML config to a temp file.

    The body is deliberately tiny — the actual validation is stubbed out by
    monkeypatching :class:`MultiRecordValidator`, so the only requirement
    here is that :class:`MultiRecordConfig` deserializes the YAML cleanly.
    Mapping paths point at on-disk dummy files because the pydantic model
    requires the ``mapping`` field on every record type.
    """
    dummy_mapping = tmp_path / "dummy_mapping.json"
    dummy_mapping.write_text("{}", encoding="utf-8")

    cfg: Dict[str, Any] = {
        "discriminator": {"field": "TYPE", "position": 1, "length": 1},
        "record_types": {
            "header": {"position": "first", "mapping": str(dummy_mapping)},
            "detail": {"match": "D", "mapping": str(dummy_mapping)},
        },
        "cross_type_rules": [],
        "default_action": "error",
    }
    cfg_path = tmp_path / "mr_config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return cfg_path


def _stub_validator(monkeypatch: pytest.MonkeyPatch, result: Dict[str, Any]) -> None:
    """Stub :class:`MultiRecordValidator` so ``.validate(...)`` returns ``result``."""
    from src.validators import multi_record_validator as mod

    class _Stub:
        def __init__(self) -> None:
            pass

        def validate(self, _file_path: str, _config: Any) -> Dict[str, Any]:
            return result

    monkeypatch.setattr(mod, "MultiRecordValidator", _Stub)


def _run_and_capture(
    tmp_path: Path,
    *,
    data_file_text: str = "x\n",
) -> str:
    """Invoke the command and return everything written to stdout."""
    from src.commands.multi_record_command import run_multi_record_command

    data_file = tmp_path / "data.txt"
    data_file.write_text(data_file_text, encoding="utf-8")
    cfg_path = _write_dummy_yaml_config(tmp_path)

    import io
    import logging
    from contextlib import redirect_stdout

    buf = io.StringIO()
    logger = logging.getLogger("test_multi_record_command_summary")

    try:
        with redirect_stdout(buf):
            run_multi_record_command(
                file=str(data_file),
                multi_record_config=str(cfg_path),
                output=None,
                logger=logger,
            )
    except SystemExit:
        # The command exits non-zero when ``result["valid"]`` is False; the
        # tests below cover both pass-verb and fail-verb cases, so swallow
        # the SystemExit and assert on captured stdout instead.
        pass
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_summary_header_verb_uses_error_count_zero_errors_says_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero cross-type errors → header reads ``✓ Multi-record validation passed``.

    Even when the validator marks ``result["valid"] = False`` (e.g. because a
    per-type structural check fired without surfacing into the cross-type
    error list), the CLI header verb is driven by ``error_count``.
    """
    _stub_validator(
        monkeypatch,
        {
            "total_rows": 19,
            "valid": False,  # Deliberately False — must be ignored by the verb.
            "cross_type_violations": [],
            "record_type_results": {
                "header": {"row_count": 1, "valid": False},
                "detail": {"row_count": 18, "valid": False},
            },
        },
    )

    out = _run_and_capture(tmp_path)

    assert "✓ Multi-record validation passed" in out
    assert "✗ Multi-record validation failed" not in out
    assert "Error Count    : 0" in out


def test_summary_header_verb_says_failed_when_cross_type_errors_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-zero cross-type error count → header reads ``✗ ... failed``."""
    _stub_validator(
        monkeypatch,
        {
            "total_rows": 5,
            "valid": False,
            "cross_type_violations": [
                {
                    "severity": "error",
                    "message": "boom",
                    "issue_code": "CT_HEADER_TRAILER_COUNT_MISMATCH",
                    "field": "ITM-CNT-BRT",
                },
            ],
            "record_type_results": {
                "header": {"row_count": 1, "valid": True},
                "detail": {"row_count": 4, "valid": True},
            },
        },
    )

    out = _run_and_capture(tmp_path)

    assert "✗ Multi-record validation failed" in out
    assert "Error Count    : 1" in out


def test_summary_per_type_rows_use_neutral_bullet_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Types with rows but no cardinality failure render with the neutral ``•``."""
    _stub_validator(
        monkeypatch,
        {
            "total_rows": 7,
            "valid": True,
            "cross_type_violations": [],
            "record_type_results": {
                "batch_header": {"row_count": 1, "valid": True},
                "rt_32000": {"row_count": 1, "valid": True},
                "rt_32001": {"row_count": 0, "valid": True, "skipped": "no_rows"},
            },
        },
    )

    out = _run_and_capture(tmp_path)

    # No per-type line should carry the old ``✗`` marker.
    assert "✗ batch_header" not in out
    assert "✗ rt_32000" not in out
    # All per-type lines use the neutral bullet.
    assert "• batch_header: 1 rows" in out
    assert "• rt_32000: 1 rows" in out
    assert "• rt_32001: 0 rows" in out


def test_summary_per_type_row_uses_cross_marker_only_when_cardinality_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``✗`` is emitted next to a type only when an ``expect`` check failed.

    The marker is keyed off ``cross_type_violations`` whose ``issue_code``
    begins with ``CT_EXPECT_`` and whose ``field`` names the missing type —
    matching ``MultiRecordValidator._enforce_expect`` exactly.
    """
    _stub_validator(
        monkeypatch,
        {
            "total_rows": 2,
            "valid": False,
            "cross_type_violations": [
                {
                    "severity": "error",
                    "message": "Expected at least 1 row of type 'rt_32005' but found 0.",
                    "issue_code": "CT_EXPECT_AT_LEAST_ONE",
                    "field": "rt_32005",
                },
            ],
            "record_type_results": {
                "batch_header": {"row_count": 1, "valid": True},
                "rt_32000": {"row_count": 1, "valid": True},
                "rt_32005": {"row_count": 0, "valid": True, "skipped": "no_rows"},
            },
        },
    )

    out = _run_and_capture(tmp_path)

    # The cardinality-failed type carries the ✗ marker.
    assert "✗ rt_32005: 0 rows" in out
    # Healthy types keep the neutral bullet.
    assert "• batch_header: 1 rows" in out
    assert "• rt_32000: 1 rows" in out


# --------------------------------------------------------------------------- #
# Sanity guard: the repo root must be importable so ``src.*`` resolves.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _ensure_repo_root_on_sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
