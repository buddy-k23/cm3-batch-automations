"""Round-trip regression tests for the ``run-tests`` Oracle gate fast path.

These tests pin two confirmed bugs in
:func:`src.commands.run_tests_command._run_oracle_vs_file_test`:

1. **Delimiter mismatch** — the Oracle extract is written pipe-delimited
   (``extract_to_file`` defaults ``delimiter='|'``) but was read back with
   ``pandas.read_csv`` using the default comma separator, collapsing every
   row into one bogus column.
2. **Column-name mismatch** — the file side uses mapping field names
   (HYPHENATED + uppercase, e.g. ``ACCT-NUM``) while the Oracle side uses raw
   cursor column names (UPPER + underscores, e.g. ``ACCT_NUM``).  Without
   normalization the merge key never lines up.

The round-trip test stubs the Oracle adapter/extractor so it writes a
pipe-delimited temp file with UPPER+UNDERSCORE columns (mirroring real Oracle
output) and uses a fixed-width mapping whose field names are HYPHENATED +
uppercase (mirroring real configs).  When both bugs are fixed, matching rows
are reported as MATCHES (0 differences / 0 only-in-X).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.commands.run_tests_command import _run_oracle_vs_file_test
from src.contracts.test_suite import TestConfig
from src.database.extractor import DEFAULT_DELIMITER


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------

# Fixed-width batch rows: ACCT-NUM (10 chars) + AMOUNT (8 chars).
# Three rows (the fixed-width detector needs >=3 lines of uniform length and
# no delimiter chars) whose key (ACCT-NUM) matches the stubbed Oracle rows.
_BATCH_ROWS = [
    "ACCT000001" + "00010000",
    "ACCT000002" + "00020000",
    "ACCT000003" + "00030000",
]

# Mapping field names use HYPHENS + uppercase, mirroring real configs
# (e.g. config/mappings/est_cds_m06_mapping.json uses ACCT-NUM).
_MAPPING = {
    "fields": [
        {"name": "ACCT-NUM", "position": 1, "length": 10},
        {"name": "AMOUNT", "position": 11, "length": 8},
    ]
}


def _write_batch_file(tmp_path: Path) -> str:
    batch = tmp_path / "batch.txt"
    batch.write_text("\n".join(_BATCH_ROWS) + "\n", encoding="utf-8")
    return str(batch)


def _write_mapping_file(tmp_path: Path) -> str:
    import json

    mpath = tmp_path / "mapping.json"
    mpath.write_text(json.dumps(_MAPPING), encoding="utf-8")
    return str(mpath)


class _StubExtractor:
    """Stand-in for :class:`DataExtractor` that mimics real Oracle output.

    ``extract_to_file`` writes a pipe-delimited file (matching the real
    ``_delimited_writer`` behaviour) whose header uses UPPER+UNDERSCORE
    column names (matching raw Oracle cursor descriptions, where hyphens are
    illegal SQL identifiers).
    """

    def __init__(self, adapter):
        self.adapter = adapter

    def extract_to_file(self, query=None, output_file=None, params=None,
                        delimiter=DEFAULT_DELIMITER, **kwargs):
        # Oracle column names: UPPER + underscores. Values match the batch
        # rows on the key (ACCT_NUM <-> ACCT-NUM) and on AMOUNT.
        rows = [
            ("ACCT000001", "00010000"),
            ("ACCT000002", "00020000"),
            ("ACCT000003", "00030000"),
        ]
        with open(output_file, "w", encoding="utf-8", newline="") as fh:
            fh.write(delimiter.join(["ACCT_NUM", "AMOUNT"]) + "\n")
            for r in rows:
                fh.write(delimiter.join(r) + "\n")
        return {"output_file": output_file, "total_rows": len(rows)}


class _StubAdapter:
    """Context-manager stub returned by the patched ``get_database_adapter``."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def _oracle_env(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "APP_INT")
    monkeypatch.setenv("ORACLE_DSN", "localhost:1521/FREEPDB1")


def _make_test(mapping_path: str) -> TestConfig:
    return TestConfig(
        name="acct round trip",
        type="oracle_vs_file",
        mapping=mapping_path,
        oracle_query="SELECT acct_num, amount FROM accounts",
        key_columns=["ACCT-NUM"],
    )


def _run(tmp_path):
    batch_file = _write_batch_file(tmp_path)
    mapping_file = _write_mapping_file(tmp_path)
    test = _make_test(mapping_file)

    with patch(
        "src.database.adapters.factory.get_database_adapter",
        return_value=_StubAdapter(),
    ), patch(
        "src.database.extractor.DataExtractor",
        _StubExtractor,
    ):
        return _run_oracle_vs_file_test(
            test, batch_file, str(tmp_path), run_id="rt1"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOracleRoundTrip:
    def test_default_delimiter_is_pipe(self):
        """The shared default delimiter is the pipe character."""
        assert DEFAULT_DELIMITER == "|"

    def test_stub_writes_pipe_delimited_multi_column(self, tmp_path):
        """Sanity: the stub writes a >1-column pipe-delimited file."""
        out = tmp_path / "oracle.csv"
        _StubExtractor(None).extract_to_file(output_file=str(out))
        df = pd.read_csv(out, dtype=str, keep_default_na=False, sep=DEFAULT_DELIMITER)
        assert list(df.columns) == ["ACCT_NUM", "AMOUNT"]
        assert len(df) == 3

    def test_matching_rows_report_zero_differences(self, _oracle_env, tmp_path):
        """End-to-end: matching rows yield 0 differences and 0 only-in-X.

        FAILS against the buggy code (comma read collapses the row into one
        column AND ``ACCT-NUM`` never equals ``ACCT_NUM``), PASSES once the
        delimiter is read as pipe and the columns/keys are normalized.
        """
        result = _run(tmp_path)

        assert result.get("status") != "ERROR", result

        # Counts are shape-agnostic: the inline fast path returns lists for
        # only_in_file1/file2/differences while the service fallback returns
        # integer counts — accept either so the assertion pins the *semantics*
        # (matching rows) rather than the result shape.
        def _count(v):
            return v if isinstance(v, int) else len(v)

        only_1 = _count(result.get("only_in_file1", 0))
        only_2 = _count(result.get("only_in_file2", 0))
        diffs = _count(result.get("differences", 0))

        assert only_1 == 0, f"unexpected only-in-file1: {result.get('only_in_file1')}"
        assert only_2 == 0, f"unexpected only-in-file2: {result.get('only_in_file2')}"
        assert diffs == 0, f"unexpected differences: {result.get('differences')}"
        assert result.get("matching_rows") == 3, result
