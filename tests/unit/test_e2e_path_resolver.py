"""Unit tests for ``scripts.lib.path_resolver``.

These tests cover the M1 deliverable from
``prompts/e2e_batch_testing_prompt.md``. They use only ``tmp_path`` fixtures
and have no Oracle / file-system / network dependencies.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Make ``scripts`` importable as a package root for tests that run from the
# repo root. ``scripts/lib/__init__.py`` already exists.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import (  # noqa: E402
    MatchedFile,
    PathResolver,
    PathResolverError,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


_VALID_PATHS_YML = textwrap.dedent("""
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
        oracle_dsn_env:      "ORACLE_DSN_SIT"
        oracle_user_env:     "ORACLE_USER_SIT"
        oracle_password_env: "ORACLE_PASSWORD_SIT"
        staging_schema:      "STG_SIT"
        audit_schema:        "AUDIT"
      ait:
        input_root:          "/data/ait/{source}/input"
        output_root:         "/data/ait/{source}/output"
        trigger_root:        "/data/ait/{source}/triggers"
        report_root:         "/data/ait/_reports/{run_id}/{source}"
        work_root:           "/data/ait/_work/{run_id}/{source}"
        baseline_root:       "baselines/ait/{source}/{release_tag}"
        log_root:            "logs"
        oracle_dsn_env:      "ORACLE_DSN_AIT"
        oracle_user_env:     "ORACLE_USER_AIT"
        oracle_password_env: "ORACLE_PASSWORD_AIT"
        staging_schema:      "STG_AIT"
        audit_schema:        "AUDIT"

    filename_patterns:
      - name: standard_input
        pattern: '^(?P<source>[A-Z0-9_]+)_input_(?P<file_type>[A-Z0-9_]+)_\\d{8}\\.dat$'
        direction: input
      - name: standard_output
        pattern: '^(?P<source>[A-Z0-9_]+)_(?P<file_type>P327|P328|P329|P330|P331|P332)_\\d{8}\\.txt$'
        direction: output
    """).strip()


_VALID_SRC_A_YML = textwrap.dedent("""
    schema_version: 1
    source: SRC_A
    release_tag: "R2026.05"
    staging_tables: [STG_SRC_A_HEADER]
    input_files: []
    output_files: []
    gates: {}
    """).strip()


@pytest.fixture
def harness(tmp_path: Path):
    """Build a writable on-disk config tree and return a resolver factory."""
    paths_yml = tmp_path / "paths.yml"
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    paths_yml.write_text(_VALID_PATHS_YML, encoding="utf-8")
    (sources_dir / "SRC_A.yml").write_text(_VALID_SRC_A_YML, encoding="utf-8")
    return paths_yml, sources_dir


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


class TestLoadAndResolve:
    def test_from_files_loads_known_envs(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.known_envs() == ["ait", "sit"]

    def test_resolve_input_root_for_sit(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve("input_root", env="sit", source="SRC_A")
        assert result == "/data/sit/SRC_A/input"

    def test_resolve_report_root_includes_run_id(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve(
            "report_root",
            env="ait",
            source="SRC_A",
            run_id="20260513_120000",
        )
        assert result == "/data/ait/_reports/20260513_120000/SRC_A"

    def test_resolve_baseline_root_pulls_release_tag_from_source(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve("baseline_root", env="sit", source="SRC_A")
        assert result == "baselines/sit/SRC_A/R2026.05"

    def test_resolve_explicit_release_tag_overrides_source(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve(
            "baseline_root",
            env="sit",
            source="SRC_A",
            release_tag="R2026.06",
        )
        assert result == "baselines/sit/SRC_A/R2026.06"

    def test_non_template_scalar_returned_as_is(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.resolve("staging_schema", env="sit") == "STG_SIT"
        assert resolver.resolve("audit_schema", env="ait") == "AUDIT"


# --------------------------------------------------------------------------- #
# Filename classification
# --------------------------------------------------------------------------- #


class TestClassifyFilename:
    def test_input_filename_matches_input_pattern(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename("SRC_A_input_HEADER_20260513.dat")
        assert result == MatchedFile(
            source="SRC_A",
            file_type="HEADER",
            direction="input",
            pattern_name="standard_input",
        )

    def test_output_filename_matches_output_pattern(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename("SRC_B_P328_20260513.txt")
        assert result is not None
        assert result.source == "SRC_B"
        assert result.file_type == "P328"
        assert result.direction == "output"

    def test_unknown_filename_returns_none(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.classify_filename("garbage.txt") is None

    def test_disallowed_file_type_does_not_match(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        # P999 is not in the output pattern allow-list.
        assert resolver.classify_filename("SRC_A_P999_20260513.txt") is None


# --------------------------------------------------------------------------- #
# Per-source overlay
# --------------------------------------------------------------------------- #


class TestSourceConfig:
    def test_source_config_round_trip(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        cfg = resolver.source_config("SRC_A")
        assert cfg["source"] == "SRC_A"
        assert cfg["release_tag"] == "R2026.05"

    def test_source_config_is_cached(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        first = resolver.source_config("SRC_A")
        second = resolver.source_config("SRC_A")
        assert first is second  # cached reference

    def test_missing_source_file_raises(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="source config not found"):
            resolver.source_config("DOES_NOT_EXIST")

    def test_mismatched_source_in_file_raises(self, harness, tmp_path: Path):
        paths_yml, sources_dir = harness
        bad = sources_dir / "SRC_B.yml"
        bad.write_text(
            "schema_version: 1\nsource: WRONG\nrelease_tag: x\n",
            encoding="utf-8",
        )
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="source mismatch"):
            resolver.source_config("SRC_B")


# --------------------------------------------------------------------------- #
# Negative paths / validation
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_unknown_env_raises(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="unknown env 'prod'"):
            resolver.resolve("input_root", env="prod", source="SRC_A")

    def test_unknown_key_raises(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="no path entry"):
            resolver.resolve("nope", env="sit")

    def test_missing_substitution_raises(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="missing required substitution"):
            # input_root needs {source} but we did not pass one.
            resolver.resolve("input_root", env="sit")

    def test_unknown_placeholder_in_paths_yml_is_rejected(self, tmp_path: Path):
        bad = tmp_path / "paths.yml"
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        bad.write_text(
            textwrap.dedent("""
                schema_version: 1
                envs:
                  sit:
                    input_root:          "/data/sit/{bogus}/input"
                    output_root:         "/x"
                    trigger_root:        "/x"
                    report_root:         "/x"
                    work_root:           "/x"
                    baseline_root:       "/x"
                    log_root:            "logs"
                    oracle_dsn_env:      "X"
                    oracle_user_env:     "X"
                    oracle_password_env: "X"
                    staging_schema:      "X"
                    audit_schema:        "X"
                filename_patterns: []
                """).strip(),
            encoding="utf-8",
        )
        with pytest.raises(PathResolverError, match="unknown placeholder"):
            PathResolver.from_files(bad, sources_dir)

    def test_missing_required_env_key_raises(self, tmp_path: Path):
        bad = tmp_path / "paths.yml"
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        bad.write_text(
            textwrap.dedent("""
                schema_version: 1
                envs:
                  sit:
                    input_root: "/x"
                filename_patterns: []
                """).strip(),
            encoding="utf-8",
        )
        with pytest.raises(PathResolverError, match="missing keys"):
            PathResolver.from_files(bad, sources_dir)

    def test_pattern_missing_named_group_raises(self, tmp_path: Path):
        bad = tmp_path / "paths.yml"
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        bad.write_text(
            textwrap.dedent("""
                schema_version: 1
                envs:
                  sit:
                    input_root:          "/x"
                    output_root:         "/x"
                    trigger_root:        "/x"
                    report_root:         "/x"
                    work_root:           "/x"
                    baseline_root:       "/x"
                    log_root:            "logs"
                    oracle_dsn_env:      "X"
                    oracle_user_env:     "X"
                    oracle_password_env: "X"
                    staging_schema:      "X"
                    audit_schema:        "X"
                filename_patterns:
                  - name: bad
                    pattern: 'no_groups_here'
                    direction: input
                """).strip(),
            encoding="utf-8",
        )
        with pytest.raises(PathResolverError, match="missing named groups"):
            PathResolver.from_files(bad, sources_dir)

    def test_invalid_direction_raises(self, tmp_path: Path):
        bad = tmp_path / "paths.yml"
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        bad.write_text(
            textwrap.dedent("""
                schema_version: 1
                envs:
                  sit:
                    input_root:          "/x"
                    output_root:         "/x"
                    trigger_root:        "/x"
                    report_root:         "/x"
                    work_root:           "/x"
                    baseline_root:       "/x"
                    log_root:            "logs"
                    oracle_dsn_env:      "X"
                    oracle_user_env:     "X"
                    oracle_password_env: "X"
                    staging_schema:      "X"
                    audit_schema:        "X"
                filename_patterns:
                  - name: bad
                    pattern: '^(?P<source>X)_(?P<file_type>Y)$'
                    direction: sideways
                """).strip(),
            encoding="utf-8",
        )
        with pytest.raises(PathResolverError, match="direction must be one of"):
            PathResolver.from_files(bad, sources_dir)


# --------------------------------------------------------------------------- #
# Source-level overrides (F1 / F1b)
# --------------------------------------------------------------------------- #


_SHAW_OVERLAY = textwrap.dedent("""
    schema_version: 1
    source: SHAW
    release_tag: "2026.M06"
    staging_schema: "APP_INT"
    output_root: "/app/software/APPS/ftp/input/shaw"
    staging_tables: [SHAW_COLLATERAL]
    input_files: []
    output_files: []
    gates: {}
    """).strip()


class TestSourceLevelOverrides:
    """F1 / F1b: per-source `staging_schema` and `output_root` overrides.

    Backward compatibility is preserved by ``SRC_A`` (no overrides declared),
    which is exercised in :class:`TestLoadAndResolve` above.
    """

    def _write_overlay(self, sources_dir: Path, name: str, body: str) -> None:
        (sources_dir / f"{name}.yml").write_text(body, encoding="utf-8")

    def test_output_root_override_returns_literal(self, harness):
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "SHAW", _SHAW_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve("output_root", env="sit", source="SHAW")
        # The env-level template would produce "/data/sit/SHAW/output".
        # The source-level override must win and be returned verbatim.
        assert result == "/app/software/APPS/ftp/input/shaw"

    def test_staging_schema_override_returns_literal(self, harness):
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "SHAW", _SHAW_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.resolve("staging_schema", env="sit", source="SHAW")
        assert result == "APP_INT"

    def test_override_applies_in_both_envs(self, harness):
        # Same override applies regardless of env when source is given.
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "SHAW", _SHAW_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert (
            resolver.resolve("output_root", env="sit", source="SHAW")
            == resolver.resolve("output_root", env="ait", source="SHAW")
            == "/app/software/APPS/ftp/input/shaw"
        )

    def test_no_override_falls_back_to_env(self, harness):
        # SRC_A has no override → existing env-level resolution.
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert (
            resolver.resolve("output_root", env="sit", source="SRC_A")
            == "/data/sit/SRC_A/output"
        )
        assert (
            resolver.resolve("staging_schema", env="sit", source="SRC_A") == "STG_SIT"
        )

    def test_override_ignored_when_source_arg_is_none(self, harness):
        # Without a source, overrides cannot be consulted; env wins.
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "SHAW", _SHAW_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.resolve("staging_schema", env="sit") == "STG_SIT"

    def test_non_string_override_raises(self, harness):
        paths_yml, sources_dir = harness
        bad_overlay = textwrap.dedent("""
            schema_version: 1
            source: BAD1
            release_tag: "x"
            staging_schema: 42
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            """).strip()
        self._write_overlay(sources_dir, "BAD1", bad_overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="must be a string"):
            resolver.resolve("staging_schema", env="sit", source="BAD1")

    def test_override_with_placeholder_braces_raises(self, harness):
        paths_yml, sources_dir = harness
        bad_overlay = textwrap.dedent("""
            schema_version: 1
            source: BAD2
            release_tag: "x"
            output_root: "/data/{source}/output"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            """).strip()
        self._write_overlay(sources_dir, "BAD2", bad_overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="must be a literal"):
            resolver.resolve("output_root", env="sit", source="BAD2")

    def test_disallowed_override_key_raises_at_load(self, harness):
        # Option (b): fail fast when a source overlay tries to override
        # a key that is not on the allow-list.
        paths_yml, sources_dir = harness
        bad_overlay = textwrap.dedent("""
            schema_version: 1
            source: BAD3
            release_tag: "x"
            input_root: "/data/whatever"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            """).strip()
        self._write_overlay(sources_dir, "BAD3", bad_overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="not allowed"):
            resolver.source_config("BAD3")

    def test_disallowed_override_message_lists_allow_list(self, harness):
        paths_yml, sources_dir = harness
        bad_overlay = textwrap.dedent("""
            schema_version: 1
            source: BAD4
            release_tag: "x"
            baseline_root: "/elsewhere"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            """).strip()
        self._write_overlay(sources_dir, "BAD4", bad_overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError) as excinfo:
            resolver.source_config("BAD4")
        msg = str(excinfo.value)
        assert "baseline_root" in msg
        assert "output_root" in msg  # part of allow-list mention
        assert "staging_schema" in msg


# --------------------------------------------------------------------------- #
# Per-source filename_patterns override (R-07)
# --------------------------------------------------------------------------- #


_CUSTOM_OVERLAY = textwrap.dedent("""
    schema_version: 1
    source: CUSTOM
    release_tag: "x"
    staging_tables: []
    input_files: []
    output_files: []
    gates: {}
    filename_patterns:
      - name: custom_in
        pattern: '^(?P<source>[a-z0-9]+)-(?P<file_type>[a-z]+)\\.csv$'
        direction: input
    """).strip()


class TestPerSourceFilenamePatterns:
    """R-07: a source overlay's `filename_patterns` replaces the global set."""

    def _write_overlay(self, sources_dir: Path, name: str, body: str) -> None:
        (sources_dir / f"{name}.yml").write_text(body, encoding="utf-8")

    def test_source_without_override_uses_global_patterns(self, harness):
        # SRC_A declares no filename_patterns → global patterns apply.
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename(
            "SRC_A_input_HEADER_20260513.dat", source="SRC_A"
        )
        assert result is not None
        assert result.pattern_name == "standard_input"

    def test_classify_without_source_uses_global_patterns(self, harness):
        # No source argument → historical behaviour, global patterns.
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename("SRC_A_input_HEADER_20260513.dat")
        assert result is not None
        assert result.pattern_name == "standard_input"

    def test_source_override_matches_custom_pattern(self, harness):
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "CUSTOM", _CUSTOM_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename("acct-header.csv", source="CUSTOM")
        assert result is not None
        assert result.source == "acct"
        assert result.file_type == "header"
        assert result.direction == "input"
        assert result.pattern_name == "custom_in"

    def test_source_override_replaces_global_not_merges(self, harness):
        # A filename that matches the GLOBAL pattern must NOT match for a
        # source whose override replaces the global list.
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "CUSTOM", _CUSTOM_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        result = resolver.classify_filename(
            "SRC_A_input_HEADER_20260513.dat", source="CUSTOM"
        )
        assert result is None

    def test_override_patterns_are_cached(self, harness):
        paths_yml, sources_dir = harness
        self._write_overlay(sources_dir, "CUSTOM", _CUSTOM_OVERLAY)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        # Two classifications should reuse the same compiled list (no recompile).
        resolver.classify_filename("acct-header.csv", source="CUSTOM")
        first = resolver._source_patterns["CUSTOM"]
        resolver.classify_filename("other-thing.csv", source="CUSTOM")
        second = resolver._source_patterns["CUSTOM"]
        assert first is second

    def test_override_missing_named_group_raises(self, harness):
        paths_yml, sources_dir = harness
        bad = textwrap.dedent("""
            schema_version: 1
            source: BADP
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            filename_patterns:
              - name: bad
                pattern: 'nogroups'
                direction: input
            """).strip()
        self._write_overlay(sources_dir, "BADP", bad)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="missing named groups"):
            resolver.classify_filename("whatever", source="BADP")

    def test_override_invalid_direction_raises(self, harness):
        paths_yml, sources_dir = harness
        bad = textwrap.dedent("""
            schema_version: 1
            source: BADD
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            filename_patterns:
              - name: bad
                pattern: '^(?P<source>X)_(?P<file_type>Y)$'
                direction: sideways
            """).strip()
        self._write_overlay(sources_dir, "BADD", bad)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="direction must be one of"):
            resolver.classify_filename("X_Y", source="BADD")

    def test_override_empty_list_raises(self, harness):
        paths_yml, sources_dir = harness
        bad = textwrap.dedent("""
            schema_version: 1
            source: EMPTYP
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            filename_patterns: []
            """).strip()
        self._write_overlay(sources_dir, "EMPTYP", bad)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="non-empty list"):
            resolver.classify_filename("anything", source="EMPTYP")


# --------------------------------------------------------------------------- #
# Per-source trigger_file override (R-07)
# --------------------------------------------------------------------------- #


class TestTriggerConfig:
    """R-07: trigger suffix / data_file_line, global default + per-source."""

    def _write_overlay(self, sources_dir: Path, name: str, body: str) -> None:
        (sources_dir / f"{name}.yml").write_text(body, encoding="utf-8")

    def test_global_defaults_when_no_trigger_block(self, harness):
        # paths.yml in this fixture has no trigger_file block → hard defaults.
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        cfg = resolver.trigger_config()
        assert cfg == {"suffix": ".trigger", "data_file_line": 1}

    def test_source_without_override_inherits_global(self, harness):
        paths_yml, sources_dir = harness
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.trigger_config("SRC_A") == {
            "suffix": ".trigger",
            "data_file_line": 1,
        }

    def test_source_override_suffix_and_line(self, harness):
        paths_yml, sources_dir = harness
        overlay = textwrap.dedent("""
            schema_version: 1
            source: TRIG
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            trigger_file:
              suffix: ".done"
              data_file_line: 2
            """).strip()
        self._write_overlay(sources_dir, "TRIG", overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.trigger_config("TRIG") == {
            "suffix": ".done",
            "data_file_line": 2,
        }

    def test_source_partial_override_falls_back_per_key(self, harness):
        # Only suffix overridden → data_file_line falls back to global default.
        paths_yml, sources_dir = harness
        overlay = textwrap.dedent("""
            schema_version: 1
            source: PARTIAL
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            trigger_file:
              suffix: ".ok"
            """).strip()
        self._write_overlay(sources_dir, "PARTIAL", overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        assert resolver.trigger_config("PARTIAL") == {
            "suffix": ".ok",
            "data_file_line": 1,
        }

    def test_bad_data_file_line_raises(self, harness):
        paths_yml, sources_dir = harness
        overlay = textwrap.dedent("""
            schema_version: 1
            source: BADL
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            trigger_file:
              data_file_line: 0
            """).strip()
        self._write_overlay(sources_dir, "BADL", overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="data_file_line"):
            resolver.trigger_config("BADL")

    def test_bad_suffix_type_raises(self, harness):
        paths_yml, sources_dir = harness
        overlay = textwrap.dedent("""
            schema_version: 1
            source: BADS
            release_tag: "x"
            staging_tables: []
            input_files: []
            output_files: []
            gates: {}
            trigger_file:
              suffix: 123
            """).strip()
        self._write_overlay(sources_dir, "BADS", overlay)
        resolver = PathResolver.from_files(paths_yml, sources_dir)
        with pytest.raises(PathResolverError, match="suffix"):
            resolver.trigger_config("BADS")
