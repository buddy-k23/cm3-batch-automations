from pathlib import Path

from src.commands.convert_mappings_command import run_convert_mappings_command


class _Logger:
    def __init__(self):
        self.messages = []

    def error(self, msg):
        self.messages.append(("error", str(msg)))

    def info(self, msg):
        self.messages.append(("info", str(msg)))


def test_convert_mappings_command_converts_valid_csv(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    err_dir = tmp_path / "err"
    in_dir.mkdir()

    (in_dir / "sample.csv").write_text(
        "Field Name,Data Type\nACCT-NUM,string\nLOCATION-CODE,string\n",
        encoding="utf-8",
    )

    logger = _Logger()
    rc = run_convert_mappings_command(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        file_format=None,
        error_report_dir=str(err_dir),
        logger=logger,
    )

    assert rc == 0
    assert (out_dir / "sample.json").exists()


def test_convert_mappings_command_detects_fixed_width_guardrail_issues(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    err_dir = tmp_path / "err"
    in_dir.mkdir()

    # bad: duplicate field name, missing length, and overlap
    (in_dir / "bad_guardrails.csv").write_text(
        "Field Name,Data Type,Position,Length\n"
        "A,string,1,5\n"
        "A,string,6,\n"
        "C,string,5,3\n",
        encoding="utf-8",
    )

    logger = _Logger()
    rc = run_convert_mappings_command(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        file_format="fixed_width",
        error_report_dir=str(err_dir),
        logger=logger,
    )

    assert rc == 1
    report = err_dir / "bad_guardrails.errors.csv"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "Duplicate field name" in text
    assert "both Position and Length are required together" in text
    assert "Overlapping or out-of-order" in text


def test_convert_mappings_command_writes_error_report_for_invalid_template(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    err_dir = tmp_path / "err"
    in_dir.mkdir()

    # Missing required header "Data Type"
    (in_dir / "bad.csv").write_text(
        "Field Name\nACCT-NUM\n",
        encoding="utf-8",
    )

    logger = _Logger()
    rc = run_convert_mappings_command(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        file_format=None,
        error_report_dir=str(err_dir),
        logger=logger,
    )

    assert rc == 1
    assert (err_dir / "bad.errors.csv").exists()
    assert not (out_dir / "bad.json").exists()
