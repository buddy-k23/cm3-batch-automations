"""Unit tests for ``scripts.e2e_lib.promote_baseline`` — R-12 policy guards.

Covers:
  - load_promotion_policy: missing file, bad YAML, empty approvers list.
  - validate_approver: known approver passes; unknown approver raises.
  - files_are_identical: identical / different / missing files.
  - check_drift: no baseline (first-time), identical (no-op), drift blocked,
    drift + force + reason (allowed), force without reason (blocked).
  - promote() integration: unknown approver blocked; force without reason
    blocked; force_reason recorded in manifest entry; clean first-time
    promotion succeeds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.promote_baseline import (  # noqa: E402
    PromoteBaselineError,
    check_drift,
    files_are_identical,
    load_promotion_policy,
    validate_approver,
    promote,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def policy_file(tmp_path: Path) -> Path:
    """Write a minimal valid promotion_policy.yml and return its path."""
    p = tmp_path / "promotion_policy.yml"
    p.write_text(
        "schema_version: 1\napprovers:\n  - qa@example.com\n  - lead@example.com\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def candidate_file(tmp_path: Path) -> Path:
    """A candidate run-output file with known content."""
    f = tmp_path / "P327.txt"
    f.write_bytes(b"line1\nline2\n")
    return f


# --------------------------------------------------------------------------- #
# load_promotion_policy
# --------------------------------------------------------------------------- #


class TestLoadPromotionPolicy:
    def test_valid_policy_returns_dict(self, policy_file: Path) -> None:
        data = load_promotion_policy(policy_file)
        assert isinstance(data, dict)
        assert "qa@example.com" in data["approvers"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PromoteBaselineError, match="promotion policy not found"):
            load_promotion_policy(tmp_path / "nonexistent.yml")

    def test_bad_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yml"
        bad.write_text(":\n  - [unclosed", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="failed to parse"):
            load_promotion_policy(bad)

    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "list.yml"
        bad.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="must contain a YAML mapping"):
            load_promotion_policy(bad)

    def test_empty_approvers_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "empty.yml"
        bad.write_text("schema_version: 1\napprovers: []\n", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="non-empty 'approvers' list"):
            load_promotion_policy(bad)

    def test_missing_approvers_key_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "no_approvers.yml"
        bad.write_text("schema_version: 1\n", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="non-empty 'approvers' list"):
            load_promotion_policy(bad)


# --------------------------------------------------------------------------- #
# validate_approver
# --------------------------------------------------------------------------- #


class TestValidateApprover:
    _POLICY: Dict[str, Any] = {"approvers": ["qa@example.com", "lead@example.com"]}

    def test_known_approver_passes(self) -> None:
        # Should not raise.
        validate_approver("qa@example.com", self._POLICY)

    def test_unknown_approver_raises(self) -> None:
        with pytest.raises(PromoteBaselineError, match="not in the promotion allowlist"):
            validate_approver("rogue@example.com", self._POLICY)

    def test_error_message_lists_known_approvers(self) -> None:
        with pytest.raises(PromoteBaselineError, match="qa@example.com"):
            validate_approver("nobody@example.com", self._POLICY)

    def test_case_sensitive_match(self) -> None:
        with pytest.raises(PromoteBaselineError, match="not in the promotion allowlist"):
            validate_approver("QA@EXAMPLE.COM", self._POLICY)


# --------------------------------------------------------------------------- #
# files_are_identical
# --------------------------------------------------------------------------- #


class TestFilesAreIdentical:
    def test_identical_files_returns_true(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello")
        b.write_bytes(b"hello")
        assert files_are_identical(a, b) is True

    def test_different_files_returns_false(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello")
        b.write_bytes(b"world")
        assert files_are_identical(a, b) is False

    def test_missing_first_file_returns_false(self, tmp_path: Path) -> None:
        b = tmp_path / "b.txt"
        b.write_bytes(b"hello")
        assert files_are_identical(tmp_path / "missing.txt", b) is False

    def test_missing_second_file_returns_false(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        a.write_bytes(b"hello")
        assert files_are_identical(a, tmp_path / "missing.txt") is False

    def test_both_missing_returns_false(self, tmp_path: Path) -> None:
        assert files_are_identical(
            tmp_path / "x.txt", tmp_path / "y.txt"
        ) is False


# --------------------------------------------------------------------------- #
# check_drift
# --------------------------------------------------------------------------- #


class TestCheckDrift:
    def test_no_existing_baseline_passes(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        # First-time promotion — no baseline yet.
        check_drift(
            candidate_file,
            tmp_path / "nonexistent.txt",
            force=False,
            reason=None,
        )

    def test_identical_content_passes(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_bytes(candidate_file.read_bytes())
        check_drift(candidate_file, baseline, force=False, reason=None)

    def test_drift_without_force_raises(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_bytes(b"old content\n")
        with pytest.raises(PromoteBaselineError, match="drift detected"):
            check_drift(candidate_file, baseline, force=False, reason=None)

    def test_drift_with_force_and_reason_passes(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_bytes(b"old content\n")
        # Should not raise.
        check_drift(
            candidate_file, baseline, force=True, reason="intentional update"
        )

    def test_drift_with_force_but_no_reason_raises(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_bytes(b"old content\n")
        with pytest.raises(PromoteBaselineError, match="--force requires --reason"):
            check_drift(candidate_file, baseline, force=True, reason=None)

    def test_drift_with_force_and_empty_reason_raises(
        self, candidate_file: Path, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_bytes(b"old content\n")
        with pytest.raises(PromoteBaselineError, match="--force requires --reason"):
            check_drift(candidate_file, baseline, force=True, reason="")


# --------------------------------------------------------------------------- #
# promote() integration — policy guards
# --------------------------------------------------------------------------- #


def _make_promote_kwargs(
    tmp_path: Path,
    policy_file: Path,
    approved_by: str = "qa@example.com",
    force: bool = False,
    reason: str | None = None,
) -> dict:
    """Build the minimal kwargs needed to call promote() with mocked path resolution."""
    return dict(
        env="sit",
        source="SRC_A",
        release_tag="R2026.06",
        run_output_dir=tmp_path / "run_output",
        approved_by=approved_by,
        comment="test promotion",
        paths_yaml=tmp_path / "paths.yml",
        sources_dir=tmp_path / "sources",
        manifest_path=tmp_path / "manifest.json",
        policy_path=policy_file,
        force=force,
        reason=reason,
        dry_run=True,
    )


class TestPromotePolicyGuards:
    """Integration-level tests for the policy guards inside promote().

    These tests stub out PathResolver and the file-system operations so they
    focus purely on the guard logic, not on the full harness config.
    """

    def test_unknown_approver_is_blocked(
        self, tmp_path: Path, policy_file: Path
    ) -> None:
        kwargs = _make_promote_kwargs(
            tmp_path, policy_file, approved_by="rogue@example.com"
        )
        with pytest.raises(PromoteBaselineError, match="not in the promotion allowlist"):
            promote(**kwargs)

    def test_force_without_reason_is_blocked(
        self, tmp_path: Path, policy_file: Path
    ) -> None:
        kwargs = _make_promote_kwargs(
            tmp_path, policy_file, force=True, reason=None
        )
        with pytest.raises(PromoteBaselineError, match="--force requires --reason"):
            promote(**kwargs)

    def test_force_reason_recorded_in_manifest_entry(
        self, tmp_path: Path, policy_file: Path
    ) -> None:
        """force_reason appears in the manifest entry when --force + --reason used."""
        # Build a minimal source config and run-output dir.
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(
            json.dumps({"version": "v1"}), encoding="utf-8"
        )
        run_output = tmp_path / "run_output"
        run_output.mkdir()
        candidate = run_output / "SRC_A_P327_20260601.txt"
        candidate.write_bytes(b"new content\n")

        # Existing baseline with different content (triggers drift).
        baseline_dir = tmp_path / "baselines" / "sit" / "SRC_A" / "R2026.06"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "P327.txt").write_bytes(b"old content\n")

        paths_yaml = tmp_path / "paths.yml"
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()

        paths_yaml.write_text(
            "schema_version: 1\n"
            "envs:\n"
            "  sit:\n"
            f"    baseline_root: {str(baseline_dir.parent).replace(chr(92), '/')}"
            "/{release_tag}\n",
            encoding="utf-8",
        )

        source_yaml = sources_dir / "SRC_A.yml"
        source_yaml.write_text(
            "output_files:\n"
            "  - file_type: P327\n"
            "    glob: 'SRC_A_P327_*.txt'\n"
            f"    mapping: {str(mapping_file).replace(chr(92), '/')}\n",
            encoding="utf-8",
        )

        # Patch PathResolver to avoid full YAML validation complexity.
        mock_resolver = MagicMock()
        mock_resolver.known_envs.return_value = ["sit"]
        mock_resolver.resolve.return_value = str(baseline_dir)
        mock_resolver.source_config.return_value = {
            "output_files": [
                {
                    "file_type": "P327",
                    "glob": "SRC_A_P327_*.txt",
                    "mapping": str(mapping_file),
                }
            ]
        }

        with patch(
            "scripts.e2e_lib.promote_baseline.PathResolver.from_files",
            return_value=mock_resolver,
        ):
            result = promote(
                env="sit",
                source="SRC_A",
                release_tag="R2026.06",
                run_output_dir=run_output,
                approved_by="qa@example.com",
                comment="forced re-promotion",
                manifest_path=tmp_path / "manifest.json",
                policy_path=policy_file,
                force=True,
                reason="prior baseline had encoding defect",
                dry_run=True,
            )

        assert result["promoted"], "expected at least one promoted entry"
        entry = result["promoted"][0]
        assert entry.get("force_reason") == "prior baseline had encoding defect"
        assert entry["approved_by"] == "qa@example.com"

    def test_first_time_promotion_no_drift_check_needed(
        self, tmp_path: Path, policy_file: Path
    ) -> None:
        """First-time promotion (no existing baseline) succeeds without --force."""
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps({"version": "v1"}), encoding="utf-8")
        run_output = tmp_path / "run_output"
        run_output.mkdir()
        candidate = run_output / "SRC_A_P327_20260601.txt"
        candidate.write_bytes(b"fresh content\n")

        baseline_dir = tmp_path / "baselines" / "sit" / "SRC_A" / "R2026.06"
        # Do NOT create baseline_dir — simulates first-time promotion.

        mock_resolver = MagicMock()
        mock_resolver.known_envs.return_value = ["sit"]
        mock_resolver.resolve.return_value = str(baseline_dir)
        mock_resolver.source_config.return_value = {
            "output_files": [
                {
                    "file_type": "P327",
                    "glob": "SRC_A_P327_*.txt",
                    "mapping": str(mapping_file),
                }
            ]
        }

        with patch(
            "scripts.e2e_lib.promote_baseline.PathResolver.from_files",
            return_value=mock_resolver,
        ):
            result = promote(
                env="sit",
                source="SRC_A",
                release_tag="R2026.06",
                run_output_dir=run_output,
                approved_by="qa@example.com",
                comment="first promotion",
                manifest_path=tmp_path / "manifest.json",
                policy_path=policy_file,
                force=False,
                reason=None,
                dry_run=True,
            )

        assert result["promoted"], "expected a promoted entry for first-time promotion"
        entry = result["promoted"][0]
        assert "force_reason" not in entry
