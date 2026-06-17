"""Unit tests for the MCP ``parse_file`` tool adapter (S22-1, #438).

Exercises :func:`src.mcp.parse_tools.parse_file_payload` directly (no MCP
transport — that path is covered by
``tests/integration/test_mcp_parse_tool.py``). The adapter is a thin wrapper
over the existing parser layer, so these tests pin its bounding behaviour, its
format/mapping routing, and every caller-fixable ``ToolError`` branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.mcp.parse_tools import (
    DEFAULT_PREVIEW_LIMIT,
    MAX_PREVIEW_LIMIT,
    parse_file_payload,
)


def _write_pipe(path: Path, n_rows: int) -> None:
    # Headerless data — the parser defaults to has_header=False (matching the
    # `valdo parse` CLI), so every line is a data row and the count is exact.
    lines = []
    for i in range(1, n_rows + 1):
        lines.append(f"{i:04d}|{i * 10}.00|2026-01-{i % 28 + 1:02d}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_pipe_format_returns_preview(tmp_path):
    f = tmp_path / "txns.psv"
    _write_pipe(f, n_rows=3)

    out = parse_file_payload(str(f), format="pipe")

    assert out["format"] == "pipe"
    assert out["row_count"] == 3
    assert out["preview_count"] == 3
    assert out["truncated"] is False
    assert isinstance(out["columns"], list) and out["columns"]
    assert len(out["rows"]) == 3
    # Each row is a dict keyed by the advertised columns; values are strings.
    assert set(out["rows"][0].keys()) == set(out["columns"])
    assert all(isinstance(v, str) for v in out["rows"][0].values())


def test_csv_alias_comma_resolves_to_csv(tmp_path):
    f = tmp_path / "data.txt"
    # Headerless rows -> exact data-row count (parser default has_header=False).
    f.write_text("1,2,3\n4,5,6\n", encoding="utf-8")

    out = parse_file_payload(str(f), format="comma")

    assert out["format"] == "csv"
    assert out["row_count"] == 2


def test_tsv_format(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("1\t2\n", encoding="utf-8")

    out = parse_file_payload(str(f), format="tsv")

    assert out["format"] == "tsv"
    assert out["row_count"] == 1


def test_default_limit_applied(tmp_path):
    f = tmp_path / "big.psv"
    _write_pipe(f, n_rows=DEFAULT_PREVIEW_LIMIT + 5)

    out = parse_file_payload(str(f), format="pipe")

    assert out["row_count"] == DEFAULT_PREVIEW_LIMIT + 5
    assert out["preview_count"] == DEFAULT_PREVIEW_LIMIT
    assert out["truncated"] is True


def test_explicit_limit_is_bounded(tmp_path):
    f = tmp_path / "big.psv"
    _write_pipe(f, n_rows=50)

    out = parse_file_payload(str(f), format="pipe", limit=5)

    assert out["row_count"] == 50
    assert out["preview_count"] == 5
    assert out["truncated"] is True


def test_limit_clamped_to_max(tmp_path):
    f = tmp_path / "big.psv"
    _write_pipe(f, n_rows=MAX_PREVIEW_LIMIT + 50)

    # Asking for more than the hard cap clamps the materialised slice.
    out = parse_file_payload(str(f), format="pipe", limit=MAX_PREVIEW_LIMIT + 100)

    assert out["row_count"] == MAX_PREVIEW_LIMIT + 50
    assert out["preview_count"] == MAX_PREVIEW_LIMIT
    assert out["truncated"] is True


def test_auto_detect_format(tmp_path):
    f = tmp_path / "auto.csv"
    f.write_text("1,2\n3,4\n", encoding="utf-8")

    out = parse_file_payload(str(f))

    assert out["format"] == "auto"
    assert out["row_count"] == 2


def test_mapping_fixed_width(tmp_path):
    f = tmp_path / "fw.dat"
    # Two 6-char records: a 3-char ID + 3-char CODE.
    f.write_text("001ABC\n002DEF\n", encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {"fields": [{"name": "ID", "length": 3}, {"name": "CODE", "length": 3}]}
        ),
        encoding="utf-8",
    )

    out = parse_file_payload(str(f), mapping=str(mapping))

    assert out["format"] == "fixed-width"
    assert out["row_count"] == 2
    assert "ID" in out["columns"] and "CODE" in out["columns"]
    assert out["rows"][0]["ID"] == "001"
    assert out["rows"][0]["CODE"] == "ABC"


# ---------------------------------------------------------------------------
# Caller-fixable ToolError branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", None, 123])
def test_blank_file_raises(bad):
    with pytest.raises(ToolError, match="file is required"):
        parse_file_payload(bad)  # type: ignore[arg-type]


def test_missing_file_raises(tmp_path):
    with pytest.raises(ToolError, match="not found"):
        parse_file_payload(str(tmp_path / "nope.psv"), format="pipe")


def test_missing_mapping_raises(tmp_path):
    f = tmp_path / "fw.dat"
    f.write_text("001ABC\n", encoding="utf-8")
    with pytest.raises(ToolError, match="Mapping file not found"):
        parse_file_payload(str(f), mapping=str(tmp_path / "missing.json"))


def test_unparseable_mapping_raises(tmp_path):
    f = tmp_path / "fw.dat"
    f.write_text("001ABC\n", encoding="utf-8")
    mapping = tmp_path / "bad.json"
    mapping.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ToolError, match="Failed to load mapping"):
        parse_file_payload(str(f), mapping=str(mapping))


def test_unknown_format_raises(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("a|b\n", encoding="utf-8")
    with pytest.raises(ToolError, match="Unknown format"):
        parse_file_payload(str(f), format="xlsx")


@pytest.mark.parametrize("bad_limit", [0, -1, 3.5, "5", True])
def test_non_positive_or_non_int_limit_raises(tmp_path, bad_limit):
    f = tmp_path / "data.psv"
    _write_pipe(f, n_rows=2)
    with pytest.raises(ToolError, match="positive integer"):
        parse_file_payload(str(f), format="pipe", limit=bad_limit)  # type: ignore[arg-type]


def test_auto_detect_failure_raises_tool_error(tmp_path):
    # An empty file is undetectable -> FormatDetector raises -> ToolError.
    f = tmp_path / "empty.dat"
    f.write_text("", encoding="utf-8")
    with pytest.raises(ToolError):
        parse_file_payload(str(f))


def test_fixed_width_parse_error_raises_tool_error(tmp_path, monkeypatch):
    f = tmp_path / "fw.dat"
    f.write_text("001ABC\n", encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps({"fields": [{"name": "ID", "length": 3}]}), encoding="utf-8"
    )

    import src.parsers.fixed_width_parser as fw

    def _boom(self):
        raise ValueError("synthetic parse failure")

    monkeypatch.setattr(fw.FixedWidthParser, "parse", _boom)

    with pytest.raises(ToolError, match="synthetic parse failure"):
        parse_file_payload(str(f), mapping=str(mapping))
