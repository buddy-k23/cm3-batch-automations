"""Unit tests for ``scripts.e2e_lib.split_pipeline``."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.split_pipeline import (  # noqa: E402
    SplitPipelineError,
    split_pipeline_yaml,
)


_PIPELINE_YAML = textwrap.dedent(
    """
    name: e2e_sit_X
    description: full pipeline
    sources:
      - name: X__input__H
        mapping: m.json
        rules: ''
        output_pattern: ''
        input_path: /in/x.dat
        target_mapping: ''
        staging_tables: []
    gates:
      - name: file_to_staging
        stage: input
        description: ''
        for_each: ''
        blocking: true
        steps: []
      - name: L1_structural
        stage: output
        description: ''
        for_each: ''
        blocking: true
        steps: []
      - name: L3_baseline_diff
        stage: output
        description: ''
        for_each: ''
        blocking: false
        steps: []
    """
).strip()


@pytest.fixture()
def source_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline.yaml"
    p.write_text(_PIPELINE_YAML, encoding="utf-8")
    return p


class TestSplitPipelineYaml:
    def test_subset_keeps_order_from_source(
        self, tmp_path: Path, source_yaml: Path
    ) -> None:
        out = tmp_path / "trim.yaml"
        # Request in a different order than the source declares.
        kept = split_pipeline_yaml(
            source_yaml, ["L3_baseline_diff", "L1_structural"], out
        )
        # The function returns the order they actually appear in the source.
        assert kept == ["L1_structural", "L3_baseline_diff"]
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert [g["name"] for g in data["gates"]] == kept

    def test_single_gate(self, tmp_path: Path, source_yaml: Path) -> None:
        out = tmp_path / "single.yaml"
        kept = split_pipeline_yaml(source_yaml, ["file_to_staging"], out)
        assert kept == ["file_to_staging"]
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert len(data["gates"]) == 1
        # Sources are preserved verbatim.
        assert data["sources"]
        assert data["name"] == "e2e_sit_X"

    def test_preserves_top_level_fields(
        self, tmp_path: Path, source_yaml: Path
    ) -> None:
        out = tmp_path / "trim.yaml"
        split_pipeline_yaml(source_yaml, ["L1_structural"], out)
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert data["name"] == "e2e_sit_X"
        # The description gets a suffix noting the filter; the original
        # text must still be present.
        assert "full pipeline" in data["description"]
        assert "filtered to gates" in data["description"]

    def test_unknown_gate_raises(
        self, tmp_path: Path, source_yaml: Path
    ) -> None:
        out = tmp_path / "x.yaml"
        with pytest.raises(SplitPipelineError, match="not found"):
            split_pipeline_yaml(source_yaml, ["nope"], out)
        assert not out.exists()

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SplitPipelineError, match="not found"):
            split_pipeline_yaml(tmp_path / "nope.yaml", ["L1_structural"], tmp_path / "o.yaml")

    def test_empty_gate_list_raises(
        self, tmp_path: Path, source_yaml: Path
    ) -> None:
        with pytest.raises(SplitPipelineError, match="at least one"):
            split_pipeline_yaml(source_yaml, [], tmp_path / "o.yaml")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: x\n  bad: indent\n", encoding="utf-8")
        with pytest.raises(SplitPipelineError, match="failed to parse"):
            split_pipeline_yaml(bad, ["L1_structural"], tmp_path / "o.yaml")

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(SplitPipelineError, match="mapping"):
            split_pipeline_yaml(bad, ["L1_structural"], tmp_path / "o.yaml")
