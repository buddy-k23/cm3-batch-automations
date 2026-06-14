from pathlib import Path

import scripts.run_manifest_workflow as manifest_wf
import scripts.run_regression_workflow as regression_wf


def test_regression_parse_stage_delegates_to_engine(monkeypatch):
    captured = {}

    def fake_build_parse_cmd(**kwargs):
        captured["kwargs"] = kwargs
        return ["python", "-m", "src.main", "parse"]

    def fake_run_subprocess(cmd, cwd):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return {"exit_code": 0, "output": "ok", "status": "PASS"}

    monkeypatch.setattr(regression_wf, "build_parse_cmd", fake_build_parse_cmd)
    monkeypatch.setattr(regression_wf, "run_subprocess", fake_run_subprocess)

    cfg = {
        "input_file": "data/samples/customers.txt",
        "mapping": "config/mappings/customer_mapping.json",
        "use_chunked": True,
        "chunk_size": 123,
    }
    out = regression_wf._parse_stage("python", cfg)

    assert out["exit_code"] == 0
    assert captured["kwargs"]["chunk_size"] == 123
    assert captured["kwargs"]["use_chunked"] is True


def test_manifest_run_validate_delegates_to_engine(monkeypatch, tmp_path):
    captured = {}

    def fake_build_validate_cmd(**kwargs):
        captured["kwargs"] = kwargs
        return ["python", "-m", "src.main", "validate"]

    def fake_run_subprocess(cmd, cwd):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return {"exit_code": 0, "output": "ok", "status": "PASS"}

    monkeypatch.setattr(manifest_wf, "build_validate_cmd", fake_build_validate_cmd)
    monkeypatch.setattr(manifest_wf, "run_subprocess", fake_run_subprocess)

    rc, output = manifest_wf.run_validate(
        project_root=Path.cwd(),
        py=Path("python"),
        data_file=tmp_path / "in.txt",
        mapping_file=tmp_path / "map.json",
        rules_file=None,
        report_file=tmp_path / "report.html",
        chunked=True,
        chunk_size=500,
    )

    assert rc == 0
    assert output == "ok"
    assert captured["kwargs"]["use_chunked"] is True
    assert captured["kwargs"]["chunk_size"] == 500
