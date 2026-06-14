from pathlib import Path
import json

from src.commands.parse_command import run_parse_command
from src.commands.compare_command import run_compare_command
from src.commands.validate_command import run_validate_command


class _Logger:
    def error(self, msg):
        # tests only
        pass

    def info(self, msg):
        # tests only
        pass


ROOT = Path(__file__).resolve().parents[2]


def test_parse_command_creates_output_dir_and_file(tmp_path):
    out_file = tmp_path / "nested" / "parsed.csv"
    run_parse_command(
        file=str(ROOT / "data/samples/customers.txt"),
        mapping=None,
        format=None,
        output=str(out_file),
        use_chunked=False,
        chunk_size=100000,
        logger=_Logger(),
    )
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "customer_id" in text


def test_compare_command_chunked_generates_report(tmp_path):
    out_file = tmp_path / "nested" / "compare.html"
    run_compare_command(
        file1=str(ROOT / "data/samples/customers.txt"),
        file2=str(ROOT / "data/samples/customers_updated.txt"),
        keys="customer_id",
        mapping=None,
        output=str(out_file),
        thresholds=None,
        detailed=True,
        chunk_size=100000,
        progress=False,
        use_chunked=True,
        logger=_Logger(),
    )
    assert out_file.exists()
    assert "<html" in out_file.read_text(encoding="utf-8").lower()


def test_validate_command_json_and_html_outputs(tmp_path):
    mapping = str(ROOT / "config/mappings/customer_mapping.json")
    source = str(ROOT / "data/samples/customers.txt")

    json_out = tmp_path / "nested" / "validation.json"
    run_validate_command(
        file=source,
        mapping=mapping,
        rules=None,
        output=str(json_out),
        detailed=False,
        use_chunked=False,
        chunk_size=100000,
        progress=False,
        logger=_Logger(),
    )
    assert json_out.exists()
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert "valid" in payload

    html_out = tmp_path / "nested" / "validation.html"
    run_validate_command(
        file=source,
        mapping=mapping,
        rules=None,
        output=str(html_out),
        detailed=False,
        use_chunked=False,
        chunk_size=100000,
        progress=False,
        logger=_Logger(),
    )
    assert html_out.exists()
    assert "<html" in html_out.read_text(encoding="utf-8").lower()
