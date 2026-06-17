"""Unit tests for the templates://etl/* MCP resource adapter (S17-4, #432).

Uses a synthetic templates_dir under tmp_path so discovery, description
resolution, single-shape reads, and the sample manifest are all exercised
without touching the committed templates tree.
"""

from pathlib import Path

import pytest

from src.mcp.resources import etl_templates as et


# A minimal YAML that round-trips through SourceConfig. We patch the
# validator to accept it so the tests do not couple to the full SourceConfig
# schema (which is exercised by its own tests).
@pytest.fixture(autouse=True)
def _accept_all_source_configs(monkeypatch):
    monkeypatch.setattr(et, "_validates_through_source_config", lambda p: True)


def _make_template(dir_: Path, shape: str, body: str) -> Path:
    p = dir_ / f"{shape}.yml"
    p.write_text(body, encoding="utf-8")
    return p


class TestReadYamlDescription:
    def test_returns_stripped_description(self, tmp_path):
        p = _make_template(tmp_path, "s", "description:  hello world  \n")
        assert et._read_yaml_description(p) == "hello world"

    def test_no_description_field(self, tmp_path):
        p = _make_template(tmp_path, "s", "other: 1\n")
        assert et._read_yaml_description(p) == ""

    def test_non_mapping_root(self, tmp_path):
        p = _make_template(tmp_path, "s", "- a\n- b\n")
        assert et._read_yaml_description(p) == ""

    def test_unparseable_yaml(self, tmp_path):
        p = _make_template(tmp_path, "s", "key: : :\n  - [")
        assert et._read_yaml_description(p) == ""


class TestReadReadmeDescription:
    def test_first_paragraph_after_h1(self, tmp_path):
        readme = tmp_path / "s_README.md"
        readme.write_text(
            "# My Template\n\nFirst prose line\ncontinued.\n\nSecond para.\n"
        )
        assert et._read_readme_description(readme) == "First prose line continued."

    def test_missing_file(self, tmp_path):
        assert et._read_readme_description(tmp_path / "none.md") == ""

    def test_no_h1(self, tmp_path):
        readme = tmp_path / "s_README.md"
        readme.write_text("just text, no heading\n")
        assert et._read_readme_description(readme) == ""

    def test_stops_at_next_heading(self, tmp_path):
        readme = tmp_path / "s_README.md"
        readme.write_text("# H1\n\nPara line\n## H2\nmore\n")
        assert et._read_readme_description(readme) == "Para line"


class TestResolveDescription:
    def test_yaml_wins(self, tmp_path):
        p = _make_template(tmp_path, "s", "description: from-yaml\n")
        assert et._resolve_description("s", p) == "from-yaml"

    def test_falls_back_to_readme(self, tmp_path):
        p = _make_template(tmp_path, "s", "other: 1\n")
        (tmp_path / "s_README.md").write_text("# T\n\nfrom-readme\n")
        assert et._resolve_description("s", p) == "from-readme"

    def test_placeholder_when_all_empty(self, tmp_path):
        p = _make_template(tmp_path, "s", "other: 1\n")
        assert et._resolve_description("s", p) == et._NO_DESCRIPTION_PLACEHOLDER


class TestDiscoverAndList:
    def test_discovers_sorted_entries(self, tmp_path):
        _make_template(tmp_path, "b_shape", "description: B\n")
        _make_template(tmp_path, "a_shape", "description: A\n")
        entries = et.discover_templates(templates_dir=tmp_path)
        assert [e["shape"] for e in entries] == ["a_shape", "b_shape"]
        assert entries[0]["description"] == "A"

    def test_missing_dir_returns_empty(self, tmp_path):
        assert et.discover_templates(templates_dir=tmp_path / "nope") == []

    def test_list_payload_aliases_discover(self, tmp_path):
        _make_template(tmp_path, "x", "description: X\n")
        assert et.list_templates_payload(templates_dir=tmp_path) == et.discover_templates(
            templates_dir=tmp_path
        )

    def test_invalid_template_omitted(self, tmp_path, monkeypatch):
        _make_template(tmp_path, "good", "description: ok\n")
        _make_template(tmp_path, "bad", "description: nope\n")
        monkeypatch.setattr(
            et, "_validates_through_source_config", lambda p: p.stem == "good"
        )
        shapes = [e["shape"] for e in et.discover_templates(templates_dir=tmp_path)]
        assert shapes == ["good"]


class TestLoadTemplateYaml:
    def test_returns_verbatim_body(self, tmp_path):
        body = "description: hi\n# a comment\nfield: <FILL_IN>\n"
        _make_template(tmp_path, "s", body)
        assert et.load_template_yaml("s", templates_dir=tmp_path) == body

    def test_unknown_shape_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown ETL template shape"):
            et.load_template_yaml("ghost", templates_dir=tmp_path)

    def test_broken_template_raises(self, tmp_path, monkeypatch):
        _make_template(tmp_path, "s", "description: x\n")
        monkeypatch.setattr(et, "_validates_through_source_config", lambda p: False)
        with pytest.raises(ValueError, match="failed SourceConfig validation"):
            et.load_template_yaml("s", templates_dir=tmp_path)


class TestReadPreview:
    def test_text_preview_truncated(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x" * 500)
        preview = et._read_preview(f, max_bytes=10)
        assert preview == "x" * 10

    def test_binary_returns_none(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"abc\x00def")
        assert et._read_preview(f) is None

    def test_empty_file_returns_empty_string(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert et._read_preview(f) == ""

    def test_missing_file_returns_none(self, tmp_path):
        assert et._read_preview(tmp_path / "none") is None


class TestLoadSampleManifest:
    def test_manifest_lists_files_sorted(self, tmp_path):
        _make_template(tmp_path, "s", "description: x\n")
        sample = tmp_path / "s_sample"
        (sample / "sub").mkdir(parents=True)
        (sample / "b.csv").write_text("col\n1\n")
        (sample / "sub" / "a.txt").write_text("hi")
        manifest = et.load_sample_manifest("s", templates_dir=tmp_path)
        paths = [f["path"] for f in manifest["files"]]
        assert paths == sorted(paths)
        assert any(f["preview"] == "col\n1\n" for f in manifest["files"])
        assert manifest["sample_dir"].endswith("s_sample")

    def test_unknown_shape_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown ETL template shape"):
            et.load_sample_manifest("ghost", templates_dir=tmp_path)

    def test_missing_sample_dir_raises(self, tmp_path):
        _make_template(tmp_path, "s", "description: x\n")
        with pytest.raises(ValueError, match="no paired sample directory"):
            et.load_sample_manifest("s", templates_dir=tmp_path)
