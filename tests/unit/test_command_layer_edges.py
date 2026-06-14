import json
from pathlib import Path

import pytest

from src.commands.parse_command import run_parse_command
from src.commands.compare_command import run_compare_command
from src.commands.validate_command import run_validate_command


class _Logger:
    def error(self, msg):
        pass

    def info(self, msg):
        pass


ROOT = Path(__file__).resolve().parents[2]


def test_parse_command_unknown_format_exits():
    with pytest.raises(SystemExit):
        run_parse_command(
            file=str(ROOT / "data/samples/customers.txt"),
            mapping=None,
            format="unknown-format",
            output=None,
            use_chunked=False,
            chunk_size=100000,
            logger=_Logger(),
        )


def test_parse_command_chunked_stdout_path():
    # Exercises chunked parse without output file.
    run_parse_command(
        file=str(ROOT / "data/samples/customers.txt"),
        mapping=None,
        format="pipe",
        output=None,
        use_chunked=True,
        chunk_size=5,
        logger=_Logger(),
    )


def test_parse_command_fixed_with_mapping_chunked(tmp_path):
    out_file = tmp_path / "fixed_chunked.csv"
    run_parse_command(
        file=str(ROOT / "data/files/manifest_scenarios/scenario_01_all_valid.txt"),
        mapping=str(ROOT / "config/mappings/manifest_scenarios/scenario_01_all_valid.json"),
        format=None,
        output=str(out_file),
        use_chunked=True,
        chunk_size=10,
        logger=_Logger(),
    )
    assert out_file.exists()


def test_compare_command_chunked_without_keys_exits():
    with pytest.raises(SystemExit):
        run_compare_command(
            file1=str(ROOT / "data/samples/customers.txt"),
            file2=str(ROOT / "data/samples/customers_updated.txt"),
            keys=None,
            mapping=None,
            output=None,
            thresholds=None,
            detailed=False,
            chunk_size=100000,
            progress=False,
            use_chunked=True,
            logger=_Logger(),
        )


def test_compare_command_non_chunked_with_header_fallback(tmp_path):
    out_file = tmp_path / "cmp.html"
    run_compare_command(
        file1=str(ROOT / "data/samples/customers.txt"),
        file2=str(ROOT / "data/samples/customers_updated.txt"),
        keys="customer_id",
        mapping=None,
        output=str(out_file),
        thresholds=None,
        detailed=False,
        chunk_size=100000,
        progress=False,
        use_chunked=False,
        logger=_Logger(),
    )
    assert out_file.exists()


def test_compare_command_with_invalid_threshold_source_exits(tmp_path):
    out_file = tmp_path / "cmp_thresholds.html"
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text('{"thresholds": {"max_difference_percentage": 99.9}}', encoding="utf-8")

    with pytest.raises(SystemExit):
        run_compare_command(
            file1=str(ROOT / "data/samples/customers.txt"),
            file2=str(ROOT / "data/samples/customers_updated.txt"),
            keys="customer_id",
            mapping=None,
            output=str(out_file),
            thresholds=str(thresholds),
            detailed=True,
            chunk_size=100000,
            progress=False,
            use_chunked=True,
            logger=_Logger(),
        )


def test_validate_command_unsupported_extension_no_file(tmp_path):
    out_file = tmp_path / "validation.unsupported"
    run_validate_command(
        file=str(ROOT / "data/samples/customers.txt"),
        mapping=str(ROOT / "config/mappings/customer_mapping.json"),
        rules=None,
        output=str(out_file),
        detailed=False,
        use_chunked=False,
        chunk_size=100000,
        progress=False,
        logger=_Logger(),
    )
    assert not out_file.exists()


def test_validate_command_chunked_html_output(tmp_path):
    out_file = tmp_path / "validation_chunked.html"
    run_validate_command(
        file=str(ROOT / "data/samples/customers.txt"),
        mapping=str(ROOT / "config/mappings/customer_mapping.json"),
        rules=None,
        output=str(out_file),
        detailed=False,
        use_chunked=True,
        chunk_size=100000,
        progress=False,
        logger=_Logger(),
    )
    assert out_file.exists()


def test_compare_command_fixed_width_without_mapping_exits():
    with pytest.raises(SystemExit):
        run_compare_command(
            file1=str(ROOT / "data/files/manifest_scenarios/scenario_01_all_valid.txt"),
            file2=str(ROOT / "data/files/manifest_scenarios/scenario_01_all_valid.txt"),
            keys="ACCOUNT_ID",
            mapping=None,
            output=None,
            thresholds=None,
            detailed=False,
            chunk_size=100000,
            progress=False,
            use_chunked=False,
            logger=_Logger(),
        )


def test_validate_command_chunked_missing_fixed_width_length_exits(tmp_path):
    bad_mapping = tmp_path / "bad_mapping.json"
    bad_mapping.write_text(
        json.dumps(
            {
                "mapping_name": "bad",
                "source": {"format": "fixed_width"},
                "fields": [
                    {"name": "LOCATION-CODE", "position": 1, "length": 6, "required": True},
                    {"name": "ACCT-NUM", "position": 7, "required": True},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as exc_info:
        run_validate_command(
            file=str(ROOT / "data/files/manifest_scenarios/scenario_01_all_valid.txt"),
            mapping=str(bad_mapping),
            rules=None,
            output=None,
            detailed=False,
            use_chunked=True,
            chunk_size=100000,
            progress=False,
            logger=_Logger(),
        )
    assert "missing required 'length'" in str(exc_info.value)


def test_validate_command_json_payload_written(tmp_path):
    out_file = tmp_path / "validation.json"
    run_validate_command(
        file=str(ROOT / "data/samples/customers.txt"),
        mapping=str(ROOT / "config/mappings/customer_mapping.json"),
        rules=None,
        output=str(out_file),
        detailed=False,
        use_chunked=False,
        chunk_size=100000,
        progress=False,
        logger=_Logger(),
    )
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "quality_metrics" in payload
