"""Promote a signed-off run's outputs into the next baseline release.

This is the Python core invoked by ``scripts/promote_baseline.sh``.

What it does
------------
For each ``(env, source, file_type)`` requested:

  1. Validates ``--approved-by`` against the allowlist in
     ``config/e2e/promotion_policy.yml`` (ADR 0015).
  2. Resolves the source mapping JSON to discover its current
     ``mapping_version`` (defaulting to ``"unversioned"`` if absent).
  3. Locates the Java-generated output in the operator-supplied
     ``--run-output-dir`` using the per-source ``output_files[*].glob``.
  4. Runs a drift check: if a baseline file already exists for
     ``(env, source, file_type)`` and its bytes differ from the candidate,
     promotion is blocked unless ``--force`` is supplied (ADR 0015, Option A).
  5. Copies the file to
     ``baselines/{env}/{source}/{release_tag}/{file_type}.txt``.
  6. Appends a new entry to ``baselines/manifest.json`` recording the pin.

The script is **idempotent for the manifest** (will not insert a
duplicate ``(env, source, file_type, mapping_version, release_tag)``
tuple) but **refuses to overwrite an existing baseline file** unless
``--force`` is supplied. This is the safety net for the prompt's hard
rule that mapping changes and baseline changes must be atomic — the
operator must explicitly acknowledge they are rewriting history.

Approvals
---------
``--approved-by`` is required and must match an entry in
``config/e2e/promotion_policy.yml``. The value lands in the manifest entry.
The actual approval workflow lives in CODEOWNERS review on the resulting MR.

Force overrides
---------------
``--force`` allows overwriting an existing baseline file and bypasses the
drift guard. When ``--force`` is used, ``--reason`` is also required; the
reason is recorded separately in the manifest entry as ``force_reason``.

CLI
---
::

    python -m scripts.e2e_lib.promote_baseline \\
        --env sit --source SRC_A \\
        --release-tag R2026.06 \\
        --run-output-dir /data/sit/_reports/20260514_120000/SRC_A \\
        --approved-by qa-lead@example.com \\
        --comment "Refresh after mapping bump in MR !1234"
        [--file-type P327 P328 ...]   # default: all output_files for the source
        [--force --reason "Re-promote: prior baseline had encoding defect"]
        [--dry-run]                    # print actions; do not write
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

# Make the repo importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.baseline_resolver import (  # noqa: E402
    BaselineResolver,
    BaselineResolverError,
)
from scripts.e2e_lib.path_resolver import (  # noqa: E402
    PathResolver,
    PathResolverError,
)


EXIT_OK = 0
EXIT_USER_ERROR = 2
EXIT_INFRA_ERROR = 3


class PromoteBaselineError(RuntimeError):
    """Raised for promote-time errors that should yield a non-zero exit."""


# --------------------------------------------------------------------------- #
# Policy loading
# --------------------------------------------------------------------------- #

_DEFAULT_POLICY_PATH = Path("config/e2e/promotion_policy.yml")


def load_promotion_policy(policy_path: Path) -> Dict[str, Any]:
    """Load and minimally validate the promotion policy YAML.

    Args:
        policy_path: Path to ``config/e2e/promotion_policy.yml``.

    Returns:
        Parsed policy dict with at least an ``approvers`` list.

    Raises:
        PromoteBaselineError: If the file is missing, unparseable, or invalid.
    """
    if not policy_path.is_file():
        raise PromoteBaselineError(
            f"promotion policy not found: {policy_path}. "
            "Create config/e2e/promotion_policy.yml (see ADR 0015)."
        )
    try:
        data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PromoteBaselineError(
            f"failed to parse {policy_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PromoteBaselineError(
            f"{policy_path} must contain a YAML mapping at the top level"
        )
    approvers = data.get("approvers")
    if not isinstance(approvers, list) or not approvers:
        raise PromoteBaselineError(
            f"{policy_path} must declare a non-empty 'approvers' list"
        )
    return data


def validate_approver(approved_by: str, policy: Dict[str, Any]) -> None:
    """Assert that ``approved_by`` is in the policy allowlist.

    Args:
        approved_by: The value supplied via ``--approved-by``.
        policy: Parsed promotion policy dict (from :func:`load_promotion_policy`).

    Raises:
        PromoteBaselineError: If ``approved_by`` is not in the allowlist.
    """
    approvers: List[str] = policy.get("approvers", [])
    if approved_by not in approvers:
        known = ", ".join(approvers) if approvers else "<none configured>"
        raise PromoteBaselineError(
            f"approver {approved_by!r} is not in the promotion allowlist. "
            f"Known approvers: {known}. "
            "Update config/e2e/promotion_policy.yml to add them (ADR 0015)."
        )


# --------------------------------------------------------------------------- #
# Drift check
# --------------------------------------------------------------------------- #


def files_are_identical(path_a: Path, path_b: Path) -> bool:
    """Return True iff both files exist and have identical byte content.

    Args:
        path_a: First file path.
        path_b: Second file path.

    Returns:
        True if both files exist and are byte-for-byte identical.
    """
    if not path_a.is_file() or not path_b.is_file():
        return False
    return path_a.read_bytes() == path_b.read_bytes()


def check_drift(
    candidate: Path,
    existing_baseline: Path,
    force: bool,
    reason: Optional[str],
) -> None:
    """Block promotion if the candidate differs from the existing baseline.

    If ``existing_baseline`` does not exist, there is nothing to compare and
    the check passes silently (first-time promotion).

    Args:
        candidate: The run-output file being promoted.
        existing_baseline: The current baseline file path (may not exist yet).
        force: If True, bypass the drift block (but ``reason`` must be set).
        reason: Required when ``force`` is True; recorded in the manifest.

    Raises:
        PromoteBaselineError: If drift is detected and ``force`` is False, or
            if ``force`` is True but ``reason`` is empty/None.
    """
    if not existing_baseline.is_file():
        # First-time promotion — no existing baseline to compare against.
        return

    if files_are_identical(candidate, existing_baseline):
        # No drift — identical content, promotion is a no-op re-pin.
        return

    # Drift detected.
    if not force:
        raise PromoteBaselineError(
            f"drift detected: candidate {candidate} differs from existing "
            f"baseline {existing_baseline}. "
            "Rerun with --force --reason '<explanation>' to override (ADR 0015)."
        )

    # force=True — require a reason.
    if not reason:
        raise PromoteBaselineError(
            "--force requires --reason '<explanation>' to be recorded in the "
            "manifest (ADR 0015)."
        )


# --------------------------------------------------------------------------- #
# Manifest IO
# --------------------------------------------------------------------------- #


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Load the manifest as a dict, or return a fresh skeleton if missing."""
    if not manifest_path.is_file():
        return {"schema_version": 1, "baselines": []}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromoteBaselineError(
            f"failed to parse {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PromoteBaselineError(
            f"{manifest_path} top level must be a JSON object"
        )
    data.setdefault("schema_version", 1)
    data.setdefault("baselines", [])
    return data


def save_manifest(manifest_path: Path, data: Dict[str, Any]) -> None:
    """Pretty-print the manifest with stable formatting for diff review."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Promotion core
# --------------------------------------------------------------------------- #


def resolve_mapping_version(mapping_path: Path) -> str:
    """Read ``version`` (or ``mapping_version``) from a mapping JSON."""
    if not mapping_path.is_file():
        raise PromoteBaselineError(f"mapping not found: {mapping_path}")
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromoteBaselineError(
            f"failed to parse {mapping_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PromoteBaselineError(
            f"mapping {mapping_path} top level must be a JSON object"
        )
    version = data.get("version") or data.get("mapping_version")
    if not version:
        return "unversioned"
    return str(version)


def find_latest_matching_file(directory: Path, glob: str) -> Path:
    """Return the most-recently-modified file in ``directory`` matching ``glob``.

    Raises:
        PromoteBaselineError: If no file matches.
    """
    matches = sorted(directory.glob(glob), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise PromoteBaselineError(
            f"no file matching {glob!r} found under {directory}"
        )
    return matches[-1]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def promote_one(
    *,
    env: str,
    source: str,
    file_type: str,
    release_tag: str,
    output_entry: Dict[str, Any],
    run_output_dir: Path,
    baseline_root: Path,
    approved_by: str,
    comment: str,
    force: bool,
    reason: Optional[str],
    dry_run: bool,
) -> Dict[str, Any]:
    """Promote one (env, source, file_type) into a manifest-ready entry.

    Runs the drift check (ADR 0015, Option A) before copying. The function
    copies the file to its destination and returns the new manifest entry.
    Callers append the returned dict to ``manifest['baselines']`` exactly once
    per invocation.

    Args:
        env: Target environment (e.g. ``"sit"``).
        source: Source system name.
        file_type: File-type tag (e.g. ``"P327"``).
        release_tag: Release label (e.g. ``"R2026.06"``).
        output_entry: The ``output_files[*]`` config dict for this file_type.
        run_output_dir: Directory containing the run's output files.
        baseline_root: Resolved baseline root directory for this promotion.
        approved_by: Validated approver identifier.
        comment: Free-text comment recorded in the manifest.
        force: If True, bypass the drift guard (requires ``reason``).
        reason: Explanation for a forced promotion; recorded in the manifest.
        dry_run: If True, print actions but do not write anything.

    Returns:
        A manifest entry dict ready to append to ``manifest['baselines']``.

    Raises:
        PromoteBaselineError: On any configuration or drift-guard failure.
    """
    glob = output_entry.get("glob")
    mapping_path = output_entry.get("mapping")
    if not glob:
        raise PromoteBaselineError(
            f"source config for {source!r} output {file_type!r} is missing 'glob'"
        )
    if not mapping_path:
        raise PromoteBaselineError(
            f"source config for {source!r} output {file_type!r} is missing 'mapping'"
        )

    src_file = find_latest_matching_file(run_output_dir, glob)
    mapping_version = resolve_mapping_version(Path(mapping_path))

    dst_file = baseline_root / f"{file_type}.txt"

    # Drift guard (ADR 0015, Option A).
    check_drift(src_file, dst_file, force=force, reason=reason)

    if dst_file.exists() and not force:
        raise PromoteBaselineError(
            f"baseline already exists at {dst_file}; rerun with --force "
            "--reason '<explanation>' to overwrite. "
            "This is a safety net: mapping_version and baseline must change "
            "atomically. If this is a re-promotion, ensure your MR explains why."
        )

    if not dry_run:
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

    entry: Dict[str, Any] = {
        "env": env,
        "source": source,
        "file_type": file_type,
        "release_tag": release_tag,
        "mapping_version": mapping_version,
        "baseline_file": str(dst_file).replace("\\", "/"),
        "mapping_path": str(mapping_path).replace("\\", "/"),
        "approved_by": approved_by,
        "approved_at": _utc_now_iso(),
        "comment": comment,
    }
    if force and reason:
        entry["force_reason"] = reason
    return entry


def manifest_contains(
    manifest: Dict[str, Any], entry: Dict[str, Any]
) -> bool:
    """Idempotency check: is an equivalent pin already in the manifest?

    Equivalence keys: ``(env, source, file_type, mapping_version, release_tag)``.
    """
    key = (
        entry["env"], entry["source"], entry["file_type"],
        entry["mapping_version"], entry["release_tag"],
    )
    for existing in manifest.get("baselines", []):
        existing_key = (
            existing.get("env"), existing.get("source"),
            existing.get("file_type"), existing.get("mapping_version"),
            existing.get("release_tag"),
        )
        if existing_key == key:
            return True
    return False


# --------------------------------------------------------------------------- #
# Top-level driver
# --------------------------------------------------------------------------- #


def promote(
    *,
    env: str,
    source: str,
    release_tag: str,
    run_output_dir: Path,
    approved_by: str,
    comment: str = "",
    file_types: Optional[Sequence[str]] = None,
    paths_yaml: Path = Path("config/e2e/paths.yml"),
    sources_dir: Path = Path("config/e2e/sources"),
    manifest_path: Path = Path("baselines/manifest.json"),
    policy_path: Path = _DEFAULT_POLICY_PATH,
    force: bool = False,
    reason: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Promote one or more file_types for ``(env, source)``. Returns summary.

    Args:
        env: Target environment (must be declared in ``paths.yml``).
        source: Source system name.
        release_tag: Release label (e.g. ``"R2026.06"``).
        run_output_dir: Directory containing the run's output files.
        approved_by: Approver identifier; validated against the policy allowlist.
        comment: Free-text comment recorded in the manifest.
        file_types: File types to promote. Defaults to all declared output_files.
        paths_yaml: Path to ``config/e2e/paths.yml``.
        sources_dir: Directory containing per-source YAML overlays.
        manifest_path: Path to ``baselines/manifest.json``.
        policy_path: Path to ``config/e2e/promotion_policy.yml``.
        force: If True, bypass the drift guard (requires ``reason``).
        reason: Required when ``force`` is True; recorded in the manifest.
        dry_run: If True, print actions but do not write anything.

    Returns:
        Summary dict with ``promoted``, ``skipped``, ``dry_run``, etc.

    Raises:
        PromoteBaselineError: On any policy, configuration, or drift failure.
    """
    # --- Policy: load and validate approver -------------------------------- #
    policy = load_promotion_policy(policy_path)
    validate_approver(approved_by, policy)

    # --- force + reason consistency --------------------------------------- #
    if force and not reason:
        raise PromoteBaselineError(
            "--force requires --reason '<explanation>' (ADR 0015)."
        )

    # --- Path resolution -------------------------------------------------- #
    try:
        path_resolver = PathResolver.from_files(paths_yaml, sources_dir)
        src_cfg = path_resolver.source_config(source)
    except PathResolverError as exc:
        raise PromoteBaselineError(str(exc)) from exc

    if env not in path_resolver.known_envs():
        raise PromoteBaselineError(
            f"unknown env={env!r}; known envs: {path_resolver.known_envs()}"
        )

    baseline_root = Path(
        path_resolver.resolve(
            "baseline_root", env=env, source=source, release_tag=release_tag
        )
    )

    output_files = src_cfg.get("output_files") or []
    by_file_type = {entry.get("file_type"): entry for entry in output_files}
    requested = list(file_types) if file_types else list(by_file_type.keys())
    if not requested:
        raise PromoteBaselineError(
            f"source {source!r} declares no output_files"
        )

    unknown = [ft for ft in requested if ft not in by_file_type]
    if unknown:
        raise PromoteBaselineError(
            f"file types not declared in source {source!r}: {unknown}; "
            f"available: {sorted(by_file_type.keys())}"
        )

    manifest = load_manifest(manifest_path)
    promoted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for file_type in requested:
        entry = promote_one(
            env=env, source=source, file_type=file_type,
            release_tag=release_tag,
            output_entry=by_file_type[file_type],
            run_output_dir=run_output_dir,
            baseline_root=baseline_root,
            approved_by=approved_by, comment=comment,
            force=force, reason=reason,
            dry_run=dry_run,
        )
        if manifest_contains(manifest, entry):
            skipped.append(entry)
            continue
        promoted.append(entry)
        if not dry_run:
            manifest["baselines"].append(entry)

    if promoted and not dry_run:
        # Validate the new manifest round-trips through the resolver before
        # committing it to disk.
        try:
            BaselineResolver.from_dict(manifest)
        except BaselineResolverError as exc:
            raise PromoteBaselineError(
                f"refusing to write a manifest that fails validation: {exc}"
            ) from exc
        save_manifest(manifest_path, manifest)

    return {
        "env": env,
        "source": source,
        "release_tag": release_tag,
        "promoted": promoted,
        "skipped": skipped,
        "dry_run": dry_run,
        "manifest_path": str(manifest_path),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="promote_baseline",
        description=(
            "Promote a signed-off run's outputs to a new baseline release."
        ),
    )
    p.add_argument("--env", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--release-tag", required=True)
    p.add_argument(
        "--run-output-dir", required=True, type=Path,
        help=(
            "Directory containing the Java-generated outputs for this run. "
            "Each output_files[*].glob is resolved relative to this dir."
        ),
    )
    p.add_argument(
        "--approved-by", required=True,
        help=(
            "Email / LDAP DN of the approver. Must match an entry in "
            "config/e2e/promotion_policy.yml (ADR 0015)."
        ),
    )
    p.add_argument("--comment", default="")
    p.add_argument(
        "--file-type", action="append", default=[],
        help="File type(s) to promote. Defaults to every output_files[*].file_type.",
    )
    p.add_argument(
        "--paths-yaml", default="config/e2e/paths.yml", type=Path,
    )
    p.add_argument(
        "--sources-dir", default="config/e2e/sources", type=Path,
    )
    p.add_argument(
        "--manifest", default="baselines/manifest.json", type=Path,
    )
    p.add_argument(
        "--policy", default=str(_DEFAULT_POLICY_PATH), type=Path,
        help="Path to the promotion policy YAML (default: config/e2e/promotion_policy.yml).",
    )
    p.add_argument(
        "--force", action="store_true",
        help=(
            "Bypass the drift guard and allow overwriting an existing baseline. "
            "Requires --reason."
        ),
    )
    p.add_argument(
        "--reason", default=None,
        help="Required when --force is used. Recorded in the manifest entry.",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        summary = promote(
            env=args.env,
            source=args.source,
            release_tag=args.release_tag,
            run_output_dir=args.run_output_dir,
            approved_by=args.approved_by,
            comment=args.comment,
            file_types=args.file_type or None,
            paths_yaml=args.paths_yaml,
            sources_dir=args.sources_dir,
            manifest_path=args.manifest,
            policy_path=args.policy,
            force=args.force,
            reason=args.reason,
            dry_run=args.dry_run,
        )
    except PromoteBaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR

    print(json.dumps(summary, indent=2, default=str))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
