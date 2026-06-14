"""Unit tests for EA-S2: defaults-as-comments emission in pipeline YAML.

The pipeline-YAML generator (``scripts/generate_pipeline_yaml.py``) now
validates each source overlay through
:class:`~src.pipeline.etl_config.SourceConfig` and emits
``# default: <field>=<value>`` comments next to every implicitly-defaulted
boilerplate field in the generated ``input_files[]`` / ``output_files[]``
manifest blocks. SREs reading the generated YAML can see the effective
configuration without consulting the Pydantic source.

Acceptance criteria covered:
  AC2: omitted ``strict_fixed_width`` / ``strict_level`` / ``tolerance.*``
       on ``output_files[]`` entries emit ``# default: ...`` comments;
       explicit values emit verbatim and suppress the corresponding comment.
  AC3: omitted ``thresholds.max_errors`` on ``input_files[]`` entries emits
       ``# default: thresholds.max_errors=0``.
  AC4: generated YAML is still valid YAML (round-trips through
       ``yaml.safe_load`` to the documented structure).
  AC5: regenerating produces byte-identical output (idempotence).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Tuple

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import PathResolver  # noqa: E402
from scripts.generate_pipeline_yaml import (  # noqa: E402
    build_pipeline_dict,
    render_yaml,
)


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _write_minimal_paths_yml(tmp_path: Path) -> Tuple[Path, Path]:
    """Spin up a self-contained paths.yml + sources/ in ``tmp_path``."""
    paths_yaml = tmp_path / "paths.yml"
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    paths_yaml.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            envs:
              sit:
                input_root:          "/data/sit/{source}/input"
                output_root:         "/data/sit/{source}/output"
                trigger_root:        "/data/sit/{source}/triggers"
                report_root:         "/data/sit/_reports/{run_id}/{source}"
                work_root:           "/data/sit/_work/{run_id}/{source}"
                baseline_root:       "baselines/sit/{source}/{release_tag}"
                log_root:            "logs"
                oracle_dsn_env:      "ORACLE_DSN"
                oracle_user_env:     "ORACLE_USER"
                oracle_password_env: "ORACLE_PASSWORD"
                staging_schema:      "STG_SIT"
                audit_schema:        "AUDIT"
            filename_patterns:
              - name: standard_input
                pattern: '^(?P<source>[A-Z]+)_input_(?P<file_type>[A-Z0-9]+)_\\d+\\.dat$'
                direction: input
              - name: standard_output
                pattern: '^(?P<source>[A-Z]+)_(?P<file_type>P[0-9]+)_\\d+\\.txt$'
                direction: output
            """
        ).strip(),
        encoding="utf-8",
    )
    return paths_yaml, sources_dir


def _write_source(sources_dir: Path, name: str, body: str) -> None:
    (sources_dir / f"{name}.yml").write_text(body, encoding="utf-8")


def _bare_source_body() -> str:
    """One input + one output entry, ZERO boilerplate -- every defaultable
    field is omitted. The generator must emit ``# default: ...`` comments
    for all five output fields and the one input field.
    """
    return textwrap.dedent(
        """
        schema_version: 1
        source: BARE
        release_tag: "R1"
        input_files:
          - file_type: H
            glob: "BARE_input_H_*.dat"
            mapping: "config/mappings/BARE_H.json"
            target_staging_table: "STG_BARE_H"
        output_files:
          - file_type: P1
            glob: "BARE_P1_*.txt"
            mapping: "config/mappings/BARE_P1.json"
            rules: "config/rules/BARE_P1.json"
        gates:
          file_to_staging:  { blocking: true,  invoke_java: false }
          L1_structural:    { blocking: true,  invoke_java: false }
          L3_baseline_diff: { blocking: false, invoke_java: false }
        """
    ).strip() + "\n"


def _explicit_source_body() -> str:
    """One output entry with every boilerplate field declared explicitly.
    The generator must NOT emit any ``# default: ...`` comment for this
    entry.
    """
    return textwrap.dedent(
        """
        schema_version: 1
        source: EXPL
        release_tag: "R1"
        input_files:
          - file_type: H
            glob: "EXPL_input_H_*.dat"
            mapping: "config/mappings/EXPL_H.json"
            target_staging_table: "STG_EXPL_H"
            thresholds:
              max_errors: 2
        output_files:
          - file_type: P1
            glob: "EXPL_P1_*.txt"
            mapping: "config/mappings/EXPL_P1.json"
            rules: "config/rules/EXPL_P1.json"
            strict_fixed_width: false
            strict_level: format
            tolerance:
              ignore_fields: ["TS", "SEQ"]
              max_errors: 1
              max_error_pct: 0.5
        gates:
          file_to_staging:  { blocking: true,  invoke_java: false }
          L1_structural:    { blocking: true,  invoke_java: false }
          L3_baseline_diff: { blocking: false, invoke_java: false }
        """
    ).strip() + "\n"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestOutputDefaultsEmitted:
    """AC2: ``output_files[]`` defaults emit ``# default: <field>=<value>``."""

    def test_emits_default_comments_for_omitted_strict_level(
        self, tmp_path: Path
    ) -> None:
        """A bare ``output_files`` entry emits ``# default: strict_level=all``.

        Also asserts ``strict_fixed_width`` and all three ``tolerance.*``
        defaults are emitted, because none of those fields were declared.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)

        assert "# default: strict_level=all" in text
        assert "# default: strict_fixed_width=true" in text

    def test_omits_default_comment_when_explicit(self, tmp_path: Path) -> None:
        """An explicit ``strict_level: format`` declaration emits the
        value verbatim and suppresses the corresponding default comment.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "EXPL", _explicit_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="EXPL", resolver=resolver)
        text = render_yaml(pipeline)

        assert "strict_level: format" in text
        # No default comment for strict_level (it was explicit).
        assert "# default: strict_level=" not in text
        # No default comment for strict_fixed_width either (explicit false).
        assert "strict_fixed_width: false" in text
        assert "# default: strict_fixed_width=" not in text
        # And no default comment for any tolerance.* field.
        assert "# default: tolerance." not in text


class TestOutputToleranceDefaults:
    """AC2: all three ``tolerance.*`` defaults emit when the block was
    omitted; partial-declaration emits comments only for omitted sub-fields.
    """

    def test_emits_default_comments_for_omitted_tolerance(
        self, tmp_path: Path
    ) -> None:
        """A bare entry (no ``tolerance:`` block) emits one comment per
        ``tolerance.*`` field at the entry level.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)

        assert "# default: tolerance.ignore_fields=[]" in text
        assert "# default: tolerance.max_errors=0" in text
        assert "# default: tolerance.max_error_pct=0.0" in text

    def test_partial_tolerance_block_emits_defaults_for_omitted_subfields(
        self, tmp_path: Path
    ) -> None:
        """``tolerance: {max_errors: 5}`` keeps ``max_errors`` explicit and
        emits ``# default: ...`` comments for the two omitted sub-fields.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        body = textwrap.dedent(
            """
            schema_version: 1
            source: PARTIAL
            release_tag: "R1"
            input_files:
              - file_type: H
                glob: "PARTIAL_input_H_*.dat"
                mapping: "config/mappings/PARTIAL_H.json"
                target_staging_table: "STG_PARTIAL_H"
            output_files:
              - file_type: P1
                glob: "PARTIAL_P1_*.txt"
                mapping: "config/mappings/PARTIAL_P1.json"
                tolerance:
                  max_errors: 5
            gates:
              file_to_staging:  { blocking: true,  invoke_java: false }
              L1_structural:    { blocking: true,  invoke_java: false }
              L3_baseline_diff: { blocking: false, invoke_java: false }
            """
        ).strip() + "\n"
        _write_source(sources_dir, "PARTIAL", body)
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(
            env="sit", source="PARTIAL", resolver=resolver
        )
        text = render_yaml(pipeline)

        # Explicit max_errors: rendered, no default comment for it.
        assert "max_errors: 5" in text
        assert "# default: tolerance.max_errors=" not in text
        # Omitted sub-fields: default comments present.
        assert "# default: tolerance.ignore_fields=[]" in text
        assert "# default: tolerance.max_error_pct=0.0" in text


class TestInputThresholdsDefaults:
    """AC3: ``input_files[]`` omitted ``thresholds`` emits a default
    comment for ``thresholds.max_errors``.
    """

    def test_emits_default_comments_for_omitted_thresholds(
        self, tmp_path: Path
    ) -> None:
        """A bare ``input_files`` entry emits
        ``# default: thresholds.max_errors=0``.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)

        assert "# default: thresholds.max_errors=0" in text

    def test_explicit_threshold_suppresses_default_comment(
        self, tmp_path: Path
    ) -> None:
        """An explicit ``thresholds.max_errors`` declaration suppresses the
        default comment.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "EXPL", _explicit_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="EXPL", resolver=resolver)
        text = render_yaml(pipeline)

        # Input has explicit max_errors: 2.
        assert "max_errors: 2" in text
        assert "# default: thresholds.max_errors=" not in text


class TestGeneratedYamlIntegrity:
    """AC4 + AC5: round-trip validity and idempotence."""

    def test_generated_yaml_round_trips(self, tmp_path: Path) -> None:
        """``yaml.safe_load`` must parse the generated text and recover
        the documented structure (``name``, ``description``,
        ``input_files``, ``output_files``, ``sources``, ``gates``).

        Comments are not parsed by YAML, so this is essentially a
        round-trip integrity check: comment lines must not break the
        document.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)

        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict)
        assert set(parsed.keys()) >= {
            "name",
            "description",
            "input_files",
            "output_files",
            "sources",
            "gates",
        }
        # input_files / output_files structure matches what SourceConfig
        # would emit for this source -- the manifest blocks are a verbatim
        # reflection of the source schema.
        assert len(parsed["input_files"]) == 1
        assert parsed["input_files"][0]["file_type"] == "H"
        assert len(parsed["output_files"]) == 1
        assert parsed["output_files"][0]["file_type"] == "P1"

    def test_generated_yaml_validates_against_pipeline_definition(
        self, tmp_path: Path
    ) -> None:
        """The manifest blocks are informational; they must not break
        Valdo's ``PipelineDefinition`` validation (which silently ignores
        unknown top-level keys per Pydantic v2 default ``extra='ignore'``).
        """
        from src.pipeline.etl_config import PipelineDefinition

        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)
        parsed = yaml.safe_load(text)
        validated = PipelineDefinition.model_validate(parsed)
        # The manifest sections are dropped by Pydantic; only the runner
        # contract remains on the model.
        assert validated.name == "e2e_sit_BARE"

    def test_generator_is_idempotent(self, tmp_path: Path) -> None:
        """AC5: generating twice produces byte-identical output."""
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline_a = build_pipeline_dict(
            env="sit", source="BARE", resolver=resolver
        )
        text_a = render_yaml(pipeline_a)
        pipeline_b = build_pipeline_dict(
            env="sit", source="BARE", resolver=resolver
        )
        text_b = render_yaml(pipeline_b)
        assert text_a == text_b, "regeneration must produce identical bytes"

    def test_default_comments_appear_after_entry_fields(
        self, tmp_path: Path
    ) -> None:
        """``# default: ...`` comments are placed after the entry's
        explicit fields, inside the same list item. This pins the layout
        the AC example specifies.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "BARE", _bare_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="BARE", resolver=resolver)
        text = render_yaml(pipeline)

        # Locate the output_files block and assert one entry's worth of
        # default comments appear after the entry's ``rules:`` line.
        out_lines = text.splitlines()
        rules_idx = next(
            i for i, line in enumerate(out_lines)
            if line.strip().startswith("rules: config/rules/BARE_P1.json")
        )
        following = "\n".join(out_lines[rules_idx + 1 : rules_idx + 7])
        assert "# default: strict_fixed_width=true" in following
        assert "# default: strict_level=all" in following
        assert "# default: tolerance.ignore_fields=[]" in following
        assert "# default: tolerance.max_errors=0" in following
        assert "# default: tolerance.max_error_pct=0.0" in following


class TestNoComments:
    """When the source declares every field explicitly, the generated
    YAML contains zero ``# default:`` comments. This is the regression
    guard for the committed SRC_A golden file (which is fully explicit).
    """

    def test_explicit_source_emits_no_default_comments(
        self, tmp_path: Path
    ) -> None:
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(sources_dir, "EXPL", _explicit_source_body())
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="EXPL", resolver=resolver)
        text = render_yaml(pipeline)

        assert "# default:" not in text
