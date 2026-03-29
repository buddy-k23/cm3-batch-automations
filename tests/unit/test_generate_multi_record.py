"""Unit tests for generate_multi_record_command.

Tests cover the non-interactive helpers and the full YAML-generation path.
Interactive prompts are not tested here — they rely on builtins.input and
are exercised manually or via E2E.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mappings_dir(tmp_path):
    """Scratch directory pre-populated with three mapping JSON stubs."""
    d = tmp_path / "mappings"
    d.mkdir()
    for name in ("header_mapping", "detail_mapping", "trailer_mapping"):
        (d / f"{name}.json").write_text(json.dumps({"fields": []}))
    return d


@pytest.fixture()
def rules_dir(tmp_path):
    """Scratch directory pre-populated with a matching rules file."""
    d = tmp_path / "rules"
    d.mkdir()
    # exact-match variant
    (d / "header_mapping_rules.json").write_text(json.dumps({"rules": []}))
    # base-name variant (no _mapping suffix in rules filename)
    (d / "detail_rules.json").write_text(json.dumps({"rules": []}))
    return d


# ---------------------------------------------------------------------------
# _find_matching_rules
# ---------------------------------------------------------------------------

class TestFindMatchingRules:
    def test_finds_exact_match(self, rules_dir):
        from src.commands.generate_multi_record_command import _find_matching_rules

        result = _find_matching_rules("header_mapping", str(rules_dir))
        assert result is not None
        assert result.name == "header_mapping_rules.json"

    def test_finds_base_name_match(self, rules_dir):
        from src.commands.generate_multi_record_command import _find_matching_rules

        # detail_mapping → strips _mapping → looks for detail_rules.json
        result = _find_matching_rules("detail_mapping", str(rules_dir))
        assert result is not None
        assert result.name == "detail_rules.json"

    def test_returns_none_when_no_match(self, rules_dir):
        from src.commands.generate_multi_record_command import _find_matching_rules

        result = _find_matching_rules("nonexistent_mapping", str(rules_dir))
        assert result is None


# ---------------------------------------------------------------------------
# _parse_discriminator
# ---------------------------------------------------------------------------

class TestParseDiscriminator:
    def test_parses_valid_string(self):
        from src.commands.generate_multi_record_command import _parse_discriminator

        disc = _parse_discriminator("REC_TYPE:1:3")
        assert disc["field"] == "REC_TYPE"
        assert disc["position"] == 1
        assert disc["length"] == 3

    def test_parses_numeric_position_and_length(self):
        from src.commands.generate_multi_record_command import _parse_discriminator

        disc = _parse_discriminator("TRAN_CODE:25:5")
        assert disc["position"] == 25
        assert disc["length"] == 5

    def test_raises_on_wrong_segment_count(self):
        from src.commands.generate_multi_record_command import _parse_discriminator

        with pytest.raises(ValueError, match="FIELD:POSITION:LENGTH"):
            _parse_discriminator("FIELD:1")

    def test_raises_on_non_integer_position(self):
        from src.commands.generate_multi_record_command import _parse_discriminator

        with pytest.raises(ValueError):
            _parse_discriminator("FIELD:abc:3")


# ---------------------------------------------------------------------------
# _parse_type_string
# ---------------------------------------------------------------------------

class TestParseTypeString:
    def test_parses_code_equals_mapping(self):
        from src.commands.generate_multi_record_command import _parse_type_string

        code, mapping_name = _parse_type_string("32005=tranert_cus")
        assert code == "32005"
        assert mapping_name == "tranert_cus"

    def test_parses_position_code(self):
        from src.commands.generate_multi_record_command import _parse_type_string

        code, mapping_name = _parse_type_string("header:first=batch_header")
        assert code == "header:first"
        assert mapping_name == "batch_header"

    def test_raises_on_missing_equals(self):
        from src.commands.generate_multi_record_command import _parse_type_string

        with pytest.raises(ValueError, match="CODE=MAPPING_NAME"):
            _parse_type_string("no_equals_sign")


# ---------------------------------------------------------------------------
# _write_yaml
# ---------------------------------------------------------------------------

class TestWriteYaml:
    def test_writes_valid_yaml_file(self, tmp_path):
        from src.commands.generate_multi_record_command import _write_yaml

        out = tmp_path / "output.yaml"
        discriminator = {"field": "REC_TYPE", "position": 1, "length": 3}
        record_types = {
            "header": {"match": "HDR", "mapping": "config/mappings/h.json", "expect": "exactly_one"},
            "detail": {"match": "DTL", "mapping": "config/mappings/d.json", "expect": "at_least_one"},
        }

        _write_yaml(str(out), discriminator, record_types, cross_type_rules=None)

        assert out.exists()
        content = yaml.safe_load(out.read_text())
        assert "multi_record" in content
        mr = content["multi_record"]
        assert mr["discriminator"]["field"] == "REC_TYPE"
        assert "header" in mr["record_types"]
        assert mr["default_action"] == "warn"

    def test_includes_cross_type_rules_when_provided(self, tmp_path):
        from src.commands.generate_multi_record_command import _write_yaml

        out = tmp_path / "with_rules.yaml"
        discriminator = {"field": "F", "position": 1, "length": 1}
        record_types = {"detail": {"match": "D", "mapping": "m.json", "expect": "any"}}
        cross_rules = [{"check": "required_companion", "when_type": "header", "requires_type": "detail"}]

        _write_yaml(str(out), discriminator, record_types, cross_type_rules=cross_rules)

        content = yaml.safe_load(out.read_text())
        assert "cross_type_rules" in content["multi_record"]
        assert content["multi_record"]["cross_type_rules"][0]["check"] == "required_companion"

    def test_omits_cross_type_rules_when_none(self, tmp_path):
        from src.commands.generate_multi_record_command import _write_yaml

        out = tmp_path / "no_rules.yaml"
        _write_yaml(str(out), {"field": "F", "position": 1, "length": 1},
                    {"d": {"match": "D", "mapping": "m.json", "expect": "any"}},
                    cross_type_rules=None)

        content = yaml.safe_load(out.read_text())
        assert "cross_type_rules" not in content["multi_record"]

    def test_creates_parent_directories(self, tmp_path):
        from src.commands.generate_multi_record_command import _write_yaml

        out = tmp_path / "deep" / "nested" / "output.yaml"
        _write_yaml(str(out), {"field": "F", "position": 1, "length": 1},
                    {"d": {"match": "D", "mapping": "m.json", "expect": "any"}},
                    cross_type_rules=None)

        assert out.exists()


# ---------------------------------------------------------------------------
# Generated YAML round-trips through MultiRecordConfig
# ---------------------------------------------------------------------------

class TestYamlLoadsIntoMultiRecordConfig:
    def test_generated_yaml_is_valid_config(self, tmp_path):
        from src.commands.generate_multi_record_command import _write_yaml
        from src.config.multi_record_config import MultiRecordConfig

        out = tmp_path / "roundtrip.yaml"
        discriminator = {"field": "REC_TYPE", "position": 1, "length": 3}
        record_types = {
            "header": {"match": "HDR", "mapping": "config/mappings/h.json", "expect": "exactly_one"},
            "detail": {"match": "DTL", "mapping": "config/mappings/d.json", "expect": "at_least_one"},
            "trailer": {"match": "TRL", "mapping": "config/mappings/t.json", "expect": "exactly_one"},
        }

        _write_yaml(str(out), discriminator, record_types, cross_type_rules=None)

        raw = yaml.safe_load(out.read_text())
        config = MultiRecordConfig(**raw["multi_record"])

        assert config.discriminator.field == "REC_TYPE"
        assert config.discriminator.position == 1
        assert config.discriminator.length == 3
        assert "header" in config.record_types
        assert config.default_action == "warn"


# ---------------------------------------------------------------------------
# run_generate_multi_record_command (non-interactive path)
# ---------------------------------------------------------------------------

class TestRunGenerateMultiRecordCommandNonInteractive:
    def test_generates_yaml_from_all_params(self, tmp_path, mappings_dir, rules_dir):
        from src.commands.generate_multi_record_command import run_generate_multi_record_command

        out = tmp_path / "output.yaml"

        run_generate_multi_record_command(
            output=str(out),
            discriminator="REC_TYPE:1:3",
            types=["header=header_mapping", "detail=detail_mapping"],
            rules_dir=str(rules_dir),
            mappings_dir=str(mappings_dir),
        )

        assert out.exists()
        content = yaml.safe_load(out.read_text())
        mr = content["multi_record"]
        assert mr["discriminator"]["field"] == "REC_TYPE"
        assert "header" in mr["record_types"]
        assert "detail" in mr["record_types"]

    def test_raises_when_discriminator_malformed(self, tmp_path, mappings_dir, rules_dir):
        from src.commands.generate_multi_record_command import run_generate_multi_record_command

        with pytest.raises(ValueError):
            run_generate_multi_record_command(
                output=str(tmp_path / "out.yaml"),
                discriminator="BAD_FORMAT",
                types=["header=header_mapping"],
                rules_dir=str(rules_dir),
                mappings_dir=str(mappings_dir),
            )

    def test_type_string_with_position_qualifier(self, tmp_path, mappings_dir, rules_dir):
        """Code with colon qualifier like 'header:first' should work."""
        from src.commands.generate_multi_record_command import run_generate_multi_record_command

        out = tmp_path / "pos.yaml"
        run_generate_multi_record_command(
            output=str(out),
            discriminator="REC_TYPE:1:3",
            types=["header:first=header_mapping"],
            rules_dir=str(rules_dir),
            mappings_dir=str(mappings_dir),
        )

        content = yaml.safe_load(out.read_text())
        rt = content["multi_record"]["record_types"]
        assert "header" in rt
        assert rt["header"]["position"] == "first"


class TestInteractiveMode:
    """Test interactive mode with mocked input()."""

    def test_interactive_mode_generates_yaml(self, tmp_path, monkeypatch):
        """Interactive wizard should generate valid YAML."""
        from src.commands.generate_multi_record_command import run_interactive_mode

        # Create test mappings
        mappings_dir = tmp_path / "mappings"
        mappings_dir.mkdir()
        (mappings_dir / "cus.json").write_text('{"mapping_name":"cus"}')
        (mappings_dir / "ori.json").write_text('{"mapping_name":"ori"}')

        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()

        output = str(tmp_path / "out.yaml")

        # Mock input() responses
        responses = iter([
            "all",              # select mappings
            "TRN-CODE",         # discriminator field
            "25",               # position
            "5",                # length
            "32005",            # cus code
            "32010",            # ori code
            "n",                # no cross-type rules
        ])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

        run_interactive_mode(output, str(mappings_dir), str(rules_dir))

        import yaml
        with open(output) as f:
            config = yaml.safe_load(f)
        assert "multi_record" in config
        assert config["multi_record"]["discriminator"]["field"] == "TRN-CODE"
        assert config["multi_record"]["discriminator"]["position"] == 25
        assert len(config["multi_record"]["record_types"]) == 2

    def test_interactive_mode_no_mappings(self, tmp_path, monkeypatch, capsys):
        """Should exit cleanly when no mappings found."""
        from src.commands.generate_multi_record_command import run_interactive_mode

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        output = str(tmp_path / "out.yaml")

        run_interactive_mode(output, str(empty_dir), str(empty_dir))

        captured = capsys.readouterr()
        assert "No mapping files found" in captured.out

    def test_entry_point_routes_to_interactive(self, tmp_path, monkeypatch):
        """When discriminator is None, should route to interactive mode."""
        from src.commands.generate_multi_record_command import run_generate_multi_record_command

        mappings_dir = tmp_path / "mappings"
        mappings_dir.mkdir()
        (mappings_dir / "test.json").write_text('{"mapping_name":"test"}')

        output = str(tmp_path / "out.yaml")

        responses = iter(["all", "CODE", "1", "3", "100", "n"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

        run_generate_multi_record_command(output, None, [], str(tmp_path / "rules"), str(mappings_dir))

        assert Path(output).exists()
