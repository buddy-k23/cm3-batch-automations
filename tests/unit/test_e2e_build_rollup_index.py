"""Unit tests for ``scripts.build_rollup_index``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.build_rollup_index import (  # noqa: E402
    EXIT_BLOCKING_FAILURE,
    EXIT_INFRA_ERROR,
    EXIT_OK,
    MultiRecordLink,
    RollupError,
    discover_multi_record_reports,
    load_source_summary,
    main,
    render_index_html,
    render_summary_json,
    scan_reports_dir,
    write_rollup,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _gate(
    name: str, status: str, blocking: bool = True, **extras: Any
) -> Dict[str, Any]:
    out = {
        "name": name,
        "status": status,
        "blocking": blocking,
        "step_count": extras.get("step_count", 1),
        "error": extras.get("error", ""),
    }
    for k, v in extras.items():
        out.setdefault(k, v)
    return out


def _write_source_summary(
    reports_dir: Path,
    source: str,
    *,
    run_id: str = "20260514_120000",
    env: str = "sit",
    gates: Optional[List[Dict[str, Any]]] = None,
    write_index_html: bool = True,
) -> Path:
    src_dir = reports_dir / source
    src_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "env": env,
        "source": source,
        "gates": gates or [],
    }
    summary_path = src_dir / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    if write_index_html:
        (src_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    return summary_path


# --------------------------------------------------------------------------- #
# load_source_summary
# --------------------------------------------------------------------------- #


class TestLoadSourceSummary:
    def test_loads_well_formed_summary(self, tmp_path: Path) -> None:
        path = _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "passed")],
        )
        s = load_source_summary(path)
        assert s.source == "SRC_A"
        assert s.env == "sit"
        assert len(s.gates) == 1
        assert s.rollup_html is not None
        assert s.rollup_html.name == "index.html"

    def test_missing_summary_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RollupError, match="not found"):
            load_source_summary(tmp_path / "nope.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "summary.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(RollupError, match="failed to parse"):
            load_source_summary(p)

    def test_non_object_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "summary.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(RollupError, match="JSON object"):
            load_source_summary(p)

    def test_missing_required_keys_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "summary.json"
        p.write_text(json.dumps({"source": "X"}), encoding="utf-8")
        with pytest.raises(RollupError, match="missing required keys"):
            load_source_summary(p)

    def test_gates_must_be_list(self, tmp_path: Path) -> None:
        p = tmp_path / "summary.json"
        p.write_text(
            json.dumps({"run_id": "r", "env": "sit", "source": "X", "gates": {}}),
            encoding="utf-8",
        )
        with pytest.raises(RollupError, match="'gates' must be a list"):
            load_source_summary(p)

    def test_missing_index_html_not_fatal(self, tmp_path: Path) -> None:
        # The summary loads cleanly even when the sibling per-source
        # roll-up HTML hasn't been written yet.
        path = _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[],
            write_index_html=False,
        )
        s = load_source_summary(path)
        assert s.rollup_html is None


# --------------------------------------------------------------------------- #
# scan_reports_dir
# --------------------------------------------------------------------------- #


class TestScanReportsDir:
    def test_scans_multiple_sources_alphabetically(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path, "SRC_B", gates=[_gate("L1_structural", "passed")]
        )
        _write_source_summary(
            tmp_path, "SRC_A", gates=[_gate("L1_structural", "passed")]
        )
        _write_source_summary(
            tmp_path, "SRC_C", gates=[_gate("L1_structural", "passed")]
        )
        report = scan_reports_dir(tmp_path)
        assert [s.source for s in report.sources] == ["SRC_A", "SRC_B", "SRC_C"]

    def test_missing_summaries_recorded_not_fatal(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        (tmp_path / "SRC_INFLIGHT").mkdir()  # no summary.json yet
        report = scan_reports_dir(tmp_path)
        assert {s.source for s in report.sources} == {"SRC_A"}
        missing = getattr(report, "_missing_summaries", [])
        assert "SRC_INFLIGHT" in missing

    def test_run_id_and_env_inherited_from_summaries(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            run_id="20260514_120000",
            env="ait",
            gates=[],
        )
        report = scan_reports_dir(tmp_path)
        assert report.run_id == "20260514_120000"
        assert report.env == "ait"

    def test_run_id_and_env_overrides_win(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            run_id="from_file",
            env="sit",
            gates=[],
        )
        report = scan_reports_dir(tmp_path, run_id="from_cli", env="ait")
        assert report.run_id == "from_cli"
        assert report.env == "ait"

    def test_run_id_falls_back_to_dir_basename_when_no_sources(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "20260514_120000"
        run_dir.mkdir()
        report = scan_reports_dir(run_dir)
        assert report.run_id == "20260514_120000"
        assert report.env == "unknown"

    def test_missing_reports_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RollupError, match="not found"):
            scan_reports_dir(tmp_path / "nope")


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


class TestAggregation:
    def test_totals_count_correctly_across_sources(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate("file_to_staging", "passed"),
                _gate("L1_structural", "passed"),
                _gate("L3_baseline_diff", "failed", blocking=False),
            ],
        )
        _write_source_summary(
            tmp_path,
            "SRC_B",
            gates=[
                _gate("file_to_staging", "passed"),
                _gate("L1_structural", "failed", blocking=True),
                _gate("L3_baseline_diff", "passed", blocking=False),
            ],
        )
        report = scan_reports_dir(tmp_path)
        assert report.total_sources == 2
        assert report.total_gates == 6
        assert report.gates_passed == 4
        assert report.gates_failed == 2
        assert report.gates_skipped == 0
        assert report.gates_infra_error == 0
        assert report.sources_with_blocking_failure == ["SRC_B"]
        assert sorted(report.sources_with_any_failure) == ["SRC_A", "SRC_B"]

    def test_gate_rows_carry_layer_tags(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate("file_to_staging", "passed"),
                _gate("L1_structural", "passed"),
                _gate("L3_baseline_diff", "passed", blocking=False),
            ],
        )
        report = scan_reports_dir(tmp_path)
        rows = report.gate_rows()
        layers = {r.gate: r.layer for r in rows}
        assert layers == {
            "file_to_staging": None,
            "L1_structural": "L1",
            "L3_baseline_diff": "L3",
        }


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #


class TestRender:
    def test_summary_json_schema_shape(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "passed")],
        )
        report = scan_reports_dir(tmp_path)
        parsed = json.loads(render_summary_json(report))
        assert parsed["schema_version"] == 1
        assert parsed["run_id"] == "20260514_120000"
        assert parsed["env"] == "sit"
        assert parsed["totals"]["sources"] == 1
        assert parsed["totals"]["gates"] == 1
        assert parsed["totals"]["gates_passed"] == 1
        assert parsed["sources"][0]["source"] == "SRC_A"

    def test_summary_includes_missing_sources_list(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        (tmp_path / "SRC_INFLIGHT").mkdir()
        report = scan_reports_dir(tmp_path)
        parsed = json.loads(render_summary_json(report))
        assert "sources_without_summary" in parsed
        assert parsed["sources_without_summary"] == ["SRC_INFLIGHT"]

    def test_html_contains_overall_banner(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "failed", blocking=True)],
        )
        report = scan_reports_dir(tmp_path)
        html = render_index_html(report)
        assert "BLOCKING FAILURE" in html
        assert "banner bad" in html

    def test_html_shows_warn_banner_for_non_blocking_only(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate("L1_structural", "passed"),
                _gate("L3_baseline_diff", "failed", blocking=False),
            ],
        )
        report = scan_reports_dir(tmp_path)
        html = render_index_html(report)
        assert "NON-BLOCKING FAILURES" in html
        assert "banner warn" in html

    def test_html_shows_ok_banner_when_clean(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "passed")],
        )
        report = scan_reports_dir(tmp_path)
        html = render_index_html(report)
        assert "ALL PASSED" in html
        assert "banner ok" in html

    def test_html_escapes_user_data(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate(
                    "L1_structural",
                    "failed",
                    error="<script>alert('xss')</script>",
                    file_name="<img src=x onerror=alert(1)>",
                )
            ],
        )
        report = scan_reports_dir(tmp_path)
        html = render_index_html(report)
        assert "<script>alert" not in html
        assert "&lt;script&gt;alert" in html
        assert "&lt;img src=x" in html

    def test_html_link_to_per_source_rollup_is_relative(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        html = render_index_html(report)
        # The href must be the relative path so the global index.html
        # is portable when the reports tree is rsynced elsewhere. The
        # meta header is allowed to print the absolute scan path for
        # debugging; the assertion targets the href cells specifically.
        assert 'href="SRC_A' in html
        # Extract just href="..." occurrences and make sure none of them
        # carry an absolute path.
        import re

        hrefs = re.findall(r'href="([^"]+)"', html)
        for href in hrefs:
            assert not href.startswith("/"), f"absolute href: {href!r}"
            assert not re.match(
                r"^[A-Za-z]:[\\/]", href
            ), f"absolute Windows href: {href!r}"


# --------------------------------------------------------------------------- #
# write_rollup (atomicity, replace semantics)
# --------------------------------------------------------------------------- #


class TestWriteRollup:
    def test_writes_both_files(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        idx, summ = write_rollup(report)
        assert idx.is_file() and idx.name == "index.html"
        assert summ.is_file() and summ.name == "summary.json"

    def test_idempotent_overwrite(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        write_rollup(report)
        first = (tmp_path / "summary.json").read_bytes()
        write_rollup(report)
        second = (tmp_path / "summary.json").read_bytes()
        # The generated_at timestamp differs between runs, so equality is
        # not guaranteed — but the file shape must remain valid JSON and
        # contain the same source list.
        parsed_first = json.loads(first)
        parsed_second = json.loads(second)
        assert parsed_first["sources"] == parsed_second["sources"]
        assert parsed_first["schema_version"] == parsed_second["schema_version"]

    def test_no_temp_file_left_behind(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        write_rollup(report)
        # The atomic rename should leave no .tmp siblings behind.
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    def test_cli_clean_run_returns_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "passed")],
        )
        rc = main(["--reports-dir", str(tmp_path)])
        assert rc == EXIT_OK
        out = capsys.readouterr().out
        assert "wrote" in out
        assert (tmp_path / "index.html").is_file()
        assert (tmp_path / "summary.json").is_file()

    def test_cli_blocking_failure_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[_gate("L1_structural", "failed", blocking=True)],
        )
        rc = main(["--reports-dir", str(tmp_path)])
        assert rc == EXIT_BLOCKING_FAILURE

    def test_cli_non_blocking_default_is_ok(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate("L3_baseline_diff", "failed", blocking=False),
            ],
        )
        rc = main(["--reports-dir", str(tmp_path)])
        assert rc == EXIT_OK

    def test_cli_strict_escalates_non_blocking(self, tmp_path: Path) -> None:
        _write_source_summary(
            tmp_path,
            "SRC_A",
            gates=[
                _gate("L3_baseline_diff", "failed", blocking=False),
            ],
        )
        rc = main(["--reports-dir", str(tmp_path), "--strict"])
        assert rc == EXIT_BLOCKING_FAILURE

    def test_cli_missing_reports_dir_returns_three(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["--reports-dir", str(tmp_path / "nope")])
        assert rc == EXIT_INFRA_ERROR
        assert "not found" in capsys.readouterr().err

    def test_cli_quiet_suppresses_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        rc = main(["--reports-dir", str(tmp_path), "--quiet"])
        assert rc == EXIT_OK
        assert capsys.readouterr().out == ""

    def test_cli_out_dir_override(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        reports.mkdir()
        _write_source_summary(reports, "SRC_A", gates=[])
        out_dir = tmp_path / "elsewhere"
        rc = main(
            [
                "--reports-dir",
                str(reports),
                "--out-dir",
                str(out_dir),
            ]
        )
        assert rc == EXIT_OK
        assert (out_dir / "index.html").is_file()
        assert (out_dir / "summary.json").is_file()
        # Reports dir must NOT have been written to.
        assert not (reports / "index.html").exists()
        assert not (reports / "summary.json").exists()


# --------------------------------------------------------------------------- #
# Multi-record report linkage (R-13)
# --------------------------------------------------------------------------- #


def _write_mr_report(
    source_dir: Path,
    file_type: str,
    *,
    write_index: bool = True,
) -> Path:
    """Create a fake multi_record/<file_type>/index.html under source_dir."""
    mr_dir = source_dir / "multi_record" / file_type
    mr_dir.mkdir(parents=True, exist_ok=True)
    index = mr_dir / "index.html"
    if write_index:
        index.write_text("<html>mr</html>", encoding="utf-8")
    return index


class TestDiscoverMultiRecordReports:
    """Tests for discover_multi_record_reports()."""

    def test_returns_empty_when_no_multi_record_dir(self, tmp_path: Path) -> None:
        result = discover_multi_record_reports(tmp_path)
        assert result == []

    def test_discovers_single_file_type(self, tmp_path: Path) -> None:
        _write_mr_report(tmp_path, "TRANERT")
        result = discover_multi_record_reports(tmp_path)
        assert len(result) == 1
        assert result[0].file_type == "TRANERT"
        assert result[0].index_html.name == "index.html"

    def test_discovers_multiple_file_types_sorted(self, tmp_path: Path) -> None:
        _write_mr_report(tmp_path, "ZTYPE")
        _write_mr_report(tmp_path, "ATYPE")
        _write_mr_report(tmp_path, "MTYPE")
        result = discover_multi_record_reports(tmp_path)
        assert [r.file_type for r in result] == ["ATYPE", "MTYPE", "ZTYPE"]

    def test_skips_subdir_without_index_html(self, tmp_path: Path) -> None:
        _write_mr_report(tmp_path, "TRANERT", write_index=False)
        result = discover_multi_record_reports(tmp_path)
        assert result == []

    def test_skips_files_in_multi_record_dir(self, tmp_path: Path) -> None:
        mr_root = tmp_path / "multi_record"
        mr_root.mkdir()
        (mr_root / "stray.txt").write_text("x", encoding="utf-8")
        _write_mr_report(tmp_path, "TRANERT")
        result = discover_multi_record_reports(tmp_path)
        assert len(result) == 1
        assert result[0].file_type == "TRANERT"

    def test_returns_multirecordlink_instances(self, tmp_path: Path) -> None:
        _write_mr_report(tmp_path, "TRANERT")
        result = discover_multi_record_reports(tmp_path)
        assert all(isinstance(r, MultiRecordLink) for r in result)


class TestMultiRecordLinkageIntegration:
    """Integration tests: multi-record links flow through scan -> render."""

    def test_load_source_summary_populates_mr_reports(self, tmp_path: Path) -> None:
        summary_path = _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        s = load_source_summary(summary_path)
        assert len(s.multi_record_reports) == 1
        assert s.multi_record_reports[0].file_type == "TRANERT"

    def test_load_source_summary_empty_when_no_mr_dir(self, tmp_path: Path) -> None:
        summary_path = _write_source_summary(tmp_path, "SRC_A", gates=[])
        s = load_source_summary(summary_path)
        assert s.multi_record_reports == []

    def test_scan_propagates_mr_reports_to_source_summary(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        report = scan_reports_dir(tmp_path)
        assert len(report.sources[0].multi_record_reports) == 1

    def test_html_contains_mr_link_when_present(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        report = scan_reports_dir(tmp_path)
        rendered = render_index_html(report)
        assert "TRANERT" in rendered
        assert "multi_record" in rendered
        assert "Multi-record reports" in rendered

    def test_html_shows_dash_when_no_mr_reports(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        rendered = render_index_html(report)
        assert "&mdash;" in rendered
        assert "Multi-record reports" in rendered

    def test_html_mr_link_is_relative(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        report = scan_reports_dir(tmp_path)
        rendered = render_index_html(report)
        import re

        hrefs = re.findall(r'href="([^"]+)"', rendered)
        mr_hrefs = [h for h in hrefs if "multi_record" in h]
        assert mr_hrefs, "no multi_record href found"
        for href in mr_hrefs:
            assert not href.startswith("/"), f"absolute href: {href!r}"
            assert not __import__("re").match(
                r"^[A-Za-z]:[\\\\]", href
            ), f"absolute Windows href: {href!r}"

    def test_summary_json_includes_mr_reports(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        report = scan_reports_dir(tmp_path)
        parsed = json.loads(render_summary_json(report))
        mr_list = parsed["sources"][0]["multi_record_reports"]
        assert len(mr_list) == 1
        assert mr_list[0]["file_type"] == "TRANERT"
        assert "index_html" in mr_list[0]
        # Must be a relative path
        assert not mr_list[0]["index_html"].startswith("/")

    def test_summary_json_empty_mr_list_when_no_reports(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        report = scan_reports_dir(tmp_path)
        parsed = json.loads(render_summary_json(report))
        assert parsed["sources"][0]["multi_record_reports"] == []

    def test_multiple_mr_file_types_all_linked(self, tmp_path: Path) -> None:
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        _write_mr_report(tmp_path / "SRC_A", "P327")
        report = scan_reports_dir(tmp_path)
        rendered = render_index_html(report)
        assert "TRANERT" in rendered
        assert "P327" in rendered
        parsed = json.loads(render_summary_json(report))
        mr_list = parsed["sources"][0]["multi_record_reports"]
        assert len(mr_list) == 2
        assert {r["file_type"] for r in mr_list} == {"TRANERT", "P327"}

    def test_source_without_mr_reports_graceful(self, tmp_path: Path) -> None:
        # SRC_A has multi-record; SRC_B does not — both must render cleanly.
        _write_source_summary(tmp_path, "SRC_A", gates=[])
        _write_mr_report(tmp_path / "SRC_A", "TRANERT")
        _write_source_summary(tmp_path, "SRC_B", gates=[])
        report = scan_reports_dir(tmp_path)
        rendered = render_index_html(report)
        assert "TRANERT" in rendered
        assert "&mdash;" in rendered  # SRC_B has no MR reports
        parsed = json.loads(render_summary_json(report))
        src_b = next(s for s in parsed["sources"] if s["source"] == "SRC_B")
        assert src_b["multi_record_reports"] == []
