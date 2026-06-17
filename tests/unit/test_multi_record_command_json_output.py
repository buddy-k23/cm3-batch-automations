"""Regression tests for the ``run_multi_record_command`` JSON output path.

Pins the defect fix where writing multi-record validation results to a
``.json`` output file crashed with::

    TypeError: Object of type int64 is not JSON serializable

The per-record-type results returned by
:class:`~src.validators.multi_record_validator.MultiRecordValidator`
embed NumPy scalars (e.g. ``numpy.int64`` row counts produced by pandas
operations in the per-type field validator). These must be coerced to
native Python types before :func:`json.dump`. Console output is
unaffected — only the ``.json`` file-write path was broken.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SHAW_CONFIG = _REPO_ROOT / "config" / "mappings" / "SHAW_TRANERT.yaml"
_SHAW_FIXTURE = (
    _REPO_ROOT / "tests" / "manual" / "fixtures" / "tranert_shaw_test_valid.txt"
)


def _run_to_json(output_path: Path) -> None:
    """Run real multi-record validation and write the result to ``output_path``.

    Exercises the exact CLI code path (``run_multi_record_command`` with a
    ``.json`` output), so the assertion below covers the real ``json.dump``
    invocation rather than a hand-rolled stand-in.
    """
    from src.commands.multi_record_command import run_multi_record_command

    buf = io.StringIO()
    logger = logging.getLogger("test_multi_record_command_json_output")
    try:
        with redirect_stdout(buf):
            run_multi_record_command(
                file=str(_SHAW_FIXTURE),
                multi_record_config=str(_SHAW_CONFIG),
                output=str(output_path),
                logger=logger,
            )
    except SystemExit:
        # The command may exit non-zero on cross-type violations; the JSON
        # report is written before the exit-code gate, so swallow it here.
        pass


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _SHAW_CONFIG.exists() or not _SHAW_FIXTURE.exists(),
    reason="SHAW_TRANERT multi-record config/fixture not present",
)
def test_json_output_does_not_raise_on_numpy_int64(tmp_path: Path) -> None:
    """Writing the result to ``.json`` must not raise on NumPy int64 counts."""
    out = tmp_path / "mr_report.json"

    # Before the fix this raised: TypeError: Object of type int64 ...
    _run_to_json(out)

    assert out.exists(), "Expected the .json report to be written"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


@pytest.mark.skipif(
    not _SHAW_CONFIG.exists() or not _SHAW_FIXTURE.exists(),
    reason="SHAW_TRANERT multi-record config/fixture not present",
)
def test_json_output_per_type_counts_are_native_ints(tmp_path: Path) -> None:
    """Per-record-type counts survive the round-trip as JSON integers."""
    out = tmp_path / "mr_report.json"
    _run_to_json(out)

    data = json.loads(out.read_text(encoding="utf-8"))

    assert "total_rows" in data
    assert isinstance(data["total_rows"], int)

    type_results: Dict[str, Any] = data.get("record_type_results", {})
    assert type_results, "Expected per-record-type results in the report"

    # Every numeric count present must deserialize as a plain int (JSON has
    # no numpy types — if json.dump succeeded, the values are native).
    found_a_count = False
    for type_result in type_results.values():
        for key in ("row_count", "total_rows"):
            if key in type_result and isinstance(type_result[key], int):
                found_a_count = True
    assert found_a_count, "Expected at least one integer per-type count"


@pytest.fixture(autouse=True)
def _ensure_repo_root_on_sys_path() -> None:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
