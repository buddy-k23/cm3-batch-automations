"""CLI command handler for ``valdo onboard-source`` (EC-S6).

Single-command BA workflow: load an onboarding workbook, run the
EC-S3 / EC-S4 / EC-S5 emitters, and write the full artefact tree to
disk (or preview / check for drift).

Modes:
    * **normal** (no flags) -- run all three emitters, write every
      artefact to its destination path, print a summary.
    * **--dry-run** -- run all emitters in memory, print the planned
      writes, touch no disk.
    * **--check** -- run all emitters in memory, compare each artefact
      to the corresponding committed file, exit non-zero on drift.
      Suitable as a CI guardrail.

Per the Sprint 2 design contract, this command never re-implements
emitter logic; it orchestrates calls to the three EC-S3/S4/S5
public-API helpers and centralises filesystem I/O.

Determinism (EC-S10):
    The ``--frozen-timestamp <value>`` flag plumbs a deterministic
    string through to the underlying converters'
    ``metadata.created_date`` / ``metadata.last_modified`` fields. Two
    back-to-back runs with the same flag value produce byte-identical
    JSON output, eliminating the historical mtime-touch on every run.

    ``--check`` mode auto-extracts each committed artefact's
    ``metadata.created_date`` and replays it through the converter
    PER-ARTEFACT so the metadata block matches the committed file
    verbatim (no metadata-strip hack required). Combined with the
    historical ``source_template`` / ``template_path`` basename-aware
    fallback, ``--check`` against the committed state now reports
    drift only when the rules / fields / structure themselves
    diverge -- timestamps no longer count as drift.

Equivalence contracts when running ``--check`` (post EC-S10):
    * Source YAML: semantic equality via ``yaml.safe_load`` (comment
      blocks / quoting style legitimately diverge from the emitter).
    * Mapping JSON / Umbrella YAML: full structural equality after
      auto-extracting the committed ``created_date`` /
      ``last_modified`` AND normalising the ``source_template`` /
      ``template_path`` fields to basename (the committed files use
      Windows-style paths from the historical CSV authoring
      environment, while the workbook-driven emitter uses synthetic
      POSIX-style basenames).
    * Rules JSON: same shape as mapping JSON.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

import click
import yaml

from src.onboarding.drift import (
    ArtefactStatus,
    compare_artefact_payload,
    load_committed_artefact as _shared_load_committed_artefact,
    normalise_metadata_for_compare as _shared_normalise_metadata_for_compare,
    normalise_sql_for_compare as _shared_normalise_sql_for_compare,
    parse_emitted_artefact as _shared_parse_emitted_artefact,
    path_basename as _shared_path_basename,
    strip_metadata as _shared_strip_metadata,
    summarise_dict_drift as _shared_summarise_dict_drift,
    extract_committed_timestamp as _shared_extract_committed_timestamp,
)
from src.onboarding.emitters import EmitterError
from src.onboarding.emitters.mapping_emitter import (
    EmittedMappingArtefact,
    emit_mapping_artefacts,
)
from src.onboarding.emitters.reconciliation_emitter import (
    EmittedReconciliationArtefact,
    emit_reconciliation_artefacts,
)
from src.onboarding.emitters.rules_emitter import (
    EmittedRulesArtefact,
    emit_rules_artefacts,
)
from src.onboarding.emitters.source_yaml_emitter import emit_source_yaml
from src.onboarding.emitters.sql_emitter import (
    EmittedSqlArtefact,
    emit_sql_artefacts,
)
from src.onboarding.models import OnboardingWorkbook, WorkbookReadError
from src.onboarding.workbook_reader import read_workbook
from src.onboarding.workbook_schema import WorkbookSchemaError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exit codes (documented in the CLI help text and the integration tests).
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_DRIFT_OR_WORKBOOK_ERROR = 1
EXIT_MISSING_WORKBOOK = 2  # Click default for missing path on click.Path(exists=True)
EXIT_DISK_IO_ERROR = 3


# ---------------------------------------------------------------------------
# Internal dataclasses for planned writes.
# ---------------------------------------------------------------------------


class _PlannedWrite:
    """One artefact + its resolved on-disk path.

    Lightweight container -- no dataclass decorator to keep the module
    boot fast and the dependency surface trivial.
    """

    __slots__ = ("path", "content", "category", "kind")

    def __init__(self, path: Path, content: str, category: str, kind: str):
        """Initialise a planned write.

        Args:
            path: Resolved on-disk path the artefact will be written to.
            content: The serialised artefact text.
            category: ``"source_yaml"`` / ``"mapping"`` / ``"rules"``.
            kind: The artefact's sub-kind (e.g. ``"flat_json"``).
        """
        self.path = path
        self.content = content
        self.category = category
        self.kind = kind


# ---------------------------------------------------------------------------
# Planning -- map emitter outputs onto resolved on-disk paths.
# ---------------------------------------------------------------------------


def _resolve_artefact_path(
    artefact_path: str,
    *,
    default_subdir: str,
    output_root: Path,
    override_dir: Path | None,
) -> Path:
    """Resolve an emitter-relative artefact path onto an output directory.

    Each emitter produces a repo-relative path like
    ``config/mappings/SHAW_TRANERT.yaml``. The CLI flag set offers three
    layers of override:

        1. ``--source-dir`` / ``--mapping-dir`` / ``--rules-dir``
           replace the artefact's leading directory entirely. The
           artefact's filename is preserved.
        2. ``--output-root`` shifts the artefact under a different
           project root but keeps the default
           ``config/<sources|mappings|rules>`` layout.
        3. With no flags, the artefact lands under
           ``<output-root>/<artefact_path>``.

    Args:
        artefact_path: The repo-relative path the emitter returned
            (e.g. ``"config/mappings/SHAW_TRANERT.yaml"``).
        default_subdir: The default subdirectory layer the emitter
            produces (``"config/e2e/sources"``,
            ``"config/mappings"``, ``"config/rules"``).
        output_root: The root that ``--output-root`` resolves to.
        override_dir: The directory the matching ``--*-dir`` flag
            resolves to, or ``None`` if the flag was not provided.

    Returns:
        The fully-resolved path.
    """
    filename = Path(artefact_path).name
    if override_dir is not None:
        return override_dir / filename
    # Strip the default_subdir prefix and join under output_root.
    relative = Path(artefact_path)
    # If the artefact starts with default_subdir, replace that prefix.
    try:
        suffix = relative.relative_to(default_subdir)
        return output_root / default_subdir / suffix
    except ValueError:
        # Artefact path doesn't start with default_subdir -- fall back
        # to joining as-is under output_root (defensive; emitters today
        # always honour the convention).
        return output_root / relative


def _plan_writes(
    workbook: OnboardingWorkbook,
    *,
    output_root: Path,
    source_dir: Path | None,
    mapping_dir: Path | None,
    rules_dir: Path | None,
    reconciliation_dir: Path | None = None,
    sql_dir: Path | None = None,
    frozen_timestamp: str | None = None,
) -> list[_PlannedWrite]:
    """Run all three emitters and resolve every artefact's destination path.

    Args:
        workbook: The parsed onboarding workbook.
        output_root: Project root used as the fallback prefix when the
            per-category override flags are not set.
        source_dir: Override for source YAML directory or ``None``.
        mapping_dir: Override for mapping directory or ``None``.
        rules_dir: Override for rules directory or ``None``.
        reconciliation_dir: Override for reconciliation YAML directory
            or ``None``. When provided, reconciliation artefacts are
            written under ``<reconciliation_dir>/<filetype>.yml``
            instead of the default
            ``config/e2e/sources/<SOURCE>/reconciliation/`` layout.
        frozen_timestamp: Optional EC-S10 deterministic timestamp
            plumbed through to the mapping + rules emitters. When set
            (either via the ``--frozen-timestamp`` flag or as part of
            an internal helper call), both emitters substitute this
            string into every ``metadata.created_date`` /
            ``metadata.last_modified`` field instead of calling
            ``datetime.utcnow()``. When ``None`` (default), emitters
            preserve their historical wall-clock behaviour.

    Returns:
        Ordered list of :class:`_PlannedWrite` -- source YAML first,
        then mapping artefacts in emitter order, then rules artefacts.

    Raises:
        EmitterError: Re-raised from any of the three emitters.
    """
    plans: list[_PlannedWrite] = []
    source_code = workbook.source.source_code

    # 1. Source YAML -- one artefact.
    source_yaml_text = emit_source_yaml(workbook)
    source_artefact_path = f"config/e2e/sources/{source_code}.yml"
    plans.append(
        _PlannedWrite(
            path=_resolve_artefact_path(
                source_artefact_path,
                default_subdir="config/e2e/sources",
                output_root=output_root,
                override_dir=source_dir,
            ),
            content=source_yaml_text,
            category="source_yaml",
            kind="source_yaml",
        )
    )

    # 2. Mapping artefacts. Retained as a local handle so ED-S2's SQL
    # emitter can consume them as input (the SQL emitter needs each
    # field's converter-resolved ``target_name`` / ``data_type`` /
    # ``format`` to pick the right Oracle wrapping per column).
    mapping_artefacts: list[EmittedMappingArtefact] = emit_mapping_artefacts(
        workbook, frozen_timestamp=frozen_timestamp
    )
    for artefact in mapping_artefacts:
        plans.append(
            _PlannedWrite(
                path=_resolve_artefact_path(
                    artefact.path,
                    default_subdir="config/mappings",
                    output_root=output_root,
                    override_dir=mapping_dir,
                ),
                content=artefact.content,
                category="mapping",
                kind=artefact.kind,
            )
        )

    # 3. Rules artefacts.
    rules_artefacts: list[EmittedRulesArtefact] = emit_rules_artefacts(
        workbook, frozen_timestamp=frozen_timestamp
    )
    for artefact in rules_artefacts:
        plans.append(
            _PlannedWrite(
                path=_resolve_artefact_path(
                    artefact.path,
                    default_subdir="config/rules",
                    output_root=output_root,
                    override_dir=rules_dir,
                ),
                content=artefact.content,
                category="rules",
                kind=artefact.kind,
            )
        )

    # 4. Reconciliation artefacts (ED-S1). Emitted per
    # ``Reconciliation_<FILETYPE>`` sheet under
    # ``config/e2e/sources/<SOURCE>/reconciliation/<filetype>.yml``.
    # The ``ED-S2`` SQL auto-derivation pass reads the marker token
    # ``expected_sql: auto`` to know which legs to fill in.
    reconciliation_artefacts: list[EmittedReconciliationArtefact] = (
        emit_reconciliation_artefacts(workbook)
    )
    for recon_artefact in reconciliation_artefacts:
        plans.append(
            _PlannedWrite(
                path=_resolve_artefact_path(
                    recon_artefact.path,
                    default_subdir="config/e2e/sources",
                    output_root=output_root,
                    override_dir=reconciliation_dir,
                ),
                content=recon_artefact.content,
                category="reconciliation",
                kind="reconciliation_yaml",
            )
        )

    # 5. Expected SQL artefacts (ED-S2). One per reconciliation row
    # WITHOUT an ``expected_sql_override`` (BA-authored SQL wins). The
    # SQL emitter consumes ``mapping_artefacts`` to look up each
    # field's ``target_name`` / ``data_type`` / ``format`` (Oracle
    # wrapping per column). For sources whose BA has populated overrides
    # for every record type (the EC-S9 SHAW state) this produces zero
    # artefacts.
    sql_artefacts: list[EmittedSqlArtefact] = emit_sql_artefacts(
        workbook, mapping_artefacts, source_code=source_code
    )
    for sql_artefact in sql_artefacts:
        plans.append(
            _PlannedWrite(
                path=_resolve_artefact_path(
                    sql_artefact.path,
                    default_subdir="config/e2e/sources",
                    output_root=output_root,
                    override_dir=sql_dir,
                ),
                content=sql_artefact.content,
                category="sql",
                kind="expected_sql",
            )
        )

    return plans


# ---------------------------------------------------------------------------
# Equivalence checking (for ``--check`` mode).
# ---------------------------------------------------------------------------


def _strip_metadata(data: Any) -> Any:
    """Deprecated alias — see :func:`src.onboarding.drift.strip_metadata`.

    Kept for backwards compatibility with any external import of this
    private helper. New code should call the shared helper directly.
    """
    return _shared_strip_metadata(data)


def _normalise_metadata_for_compare(
    emitted: Any, committed: Any
) -> tuple[Any, Any]:
    """Deprecated alias — see :func:`src.onboarding.drift.normalise_metadata_for_compare`.

    Kept for backwards compatibility. EE-S2 promoted this helper into
    :mod:`src.onboarding.drift` so both the CLI and the API layer can
    share one equivalence contract; this thin wrapper preserves the
    historical private import path.
    """
    return _shared_normalise_metadata_for_compare(emitted, committed)


def _normalise_sql_for_compare(text: str) -> str:
    """Deprecated alias — see :func:`src.onboarding.drift.normalise_sql_for_compare`."""
    return _shared_normalise_sql_for_compare(text)


def _path_basename(value: Any) -> Any:
    """Deprecated alias — see :func:`src.onboarding.drift.path_basename`."""
    return _shared_path_basename(value)


def _extract_committed_timestamp(data: Any) -> str | None:
    """Deprecated alias — see :func:`src.onboarding.drift.extract_committed_timestamp`."""
    return _shared_extract_committed_timestamp(data)


def _load_committed_artefact(
    path: Path, category: str
) -> tuple[Any, bool]:
    """Deprecated alias — see :func:`src.onboarding.drift.load_committed_artefact`."""
    return _shared_load_committed_artefact(path, category)


def _parse_emitted_artefact(plan: _PlannedWrite) -> Any:
    """Parse a planned artefact's content for ``--check`` comparison.

    Args:
        plan: The planned write whose content needs parsing.

    Returns:
        The parsed data (YAML for ``.yml``/``.yaml`` artefacts and the
        source YAML; JSON for everything else).
    """
    return _shared_parse_emitted_artefact(plan.content, plan.category, plan.path)


def _compare_artefact(
    plan: _PlannedWrite,
) -> tuple[bool, str | None]:
    """Compare one emitted artefact against its committed counterpart.

    Thin wrapper over :func:`src.onboarding.drift.compare_artefact_payload`
    that adapts the EE-S2 three-state status enum back to the historical
    ``(matches, drift_message)`` tuple shape the CLI's drift report
    consumes.

    Args:
        plan: The planned write to compare.

    Returns:
        ``(matches, drift_message)``. When ``matches=True`` the
        message is ``None``. Otherwise the message is a single-line
        explanation suitable for inclusion in the CLI drift report.
    """
    status, drift_msg = compare_artefact_payload(
        plan.path, plan.content, plan.category
    )
    if status == ArtefactStatus.UNCHANGED:
        return True, None
    if status == ArtefactStatus.NEW:
        return False, "committed file does not exist (would be created)"
    return False, drift_msg or "drift detected"


def _summarise_dict_drift(emitted: Any, committed: Any) -> str:
    """Deprecated alias — see :func:`src.onboarding.drift.summarise_dict_drift`."""
    return _shared_summarise_dict_drift(emitted, committed)


# ---------------------------------------------------------------------------
# Disk I/O.
# ---------------------------------------------------------------------------


def _write_artefact(plan: _PlannedWrite) -> None:
    """Write one planned artefact to disk, creating parents as needed.

    Args:
        plan: The planned write to persist.

    Raises:
        OSError: When the parent directory cannot be created or the
            write fails. The CLI catches this and surfaces a friendly
            error with exit code :data:`EXIT_DISK_IO_ERROR`.
    """
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_text(plan.content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Summary rendering.
# ---------------------------------------------------------------------------


def _category_summary(plans: Iterable[_PlannedWrite], category: str) -> tuple[int, int]:
    """Compute the ``(file_count, total_bytes)`` for one category.

    Args:
        plans: Iterable of planned writes.
        category: The category to filter on.

    Returns:
        ``(file_count, total_bytes)``.
    """
    matching = [p for p in plans if p.category == category]
    return len(matching), sum(len(p.content.encode("utf-8")) for p in matching)


def _render_summary_block(
    plans: list[_PlannedWrite],
    source_code: str,
    dry_run: bool,
) -> str:
    """Render the post-run summary block.

    Args:
        plans: The planned writes (or successful writes in normal mode).
        source_code: The source code from the workbook (e.g. ``"SHAW"``).
        dry_run: When ``True`` the summary header reflects "planned
            writes"; in normal mode it reflects "files written".

    Returns:
        A multi-line summary suitable for ``click.echo``.
    """
    source_count, source_bytes = _category_summary(plans, "source_yaml")
    mapping_count, mapping_bytes = _category_summary(plans, "mapping")
    rules_count, rules_bytes = _category_summary(plans, "rules")
    recon_count, recon_bytes = _category_summary(plans, "reconciliation")
    sql_count, sql_bytes = _category_summary(plans, "sql")
    total = source_count + mapping_count + rules_count + recon_count + sql_count

    if dry_run:
        verb = "would write"
    else:
        verb = "written"

    lines = [
        f"onboard-source: {source_code}",
        f"  source YAML              -> 1 file ({source_bytes} bytes)",
        f"  mapping artefacts        -> {mapping_count} files ({mapping_bytes} bytes)",
        f"  rules artefacts          -> {rules_count} files ({rules_bytes} bytes)",
        (
            f"  reconciliation artefacts -> "
            f"config/e2e/sources/{source_code}/reconciliation/* "
            f"({recon_count} files, {recon_bytes} bytes)"
        ),
        (
            f"  expected SQL artefacts   -> "
            f"config/e2e/sources/{source_code}/sql/* "
            f"({sql_count} files, {sql_bytes} bytes)"
        ),
        "  " + ("-" * 60),
        f"  {total} files {verb}.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode handlers.
# ---------------------------------------------------------------------------


def _run_normal_mode(
    plans: list[_PlannedWrite], source_code: str, quiet: bool
) -> int:
    """Write every planned artefact to disk and emit the summary.

    Args:
        plans: The full list of planned writes.
        source_code: For the summary header.
        quiet: When ``True`` suppress the summary block.

    Returns:
        :data:`EXIT_OK` on success, :data:`EXIT_DISK_IO_ERROR` on
        any disk I/O failure.
    """
    for plan in plans:
        try:
            _write_artefact(plan)
        except OSError as exc:
            click.echo(
                click.style(
                    f"Error writing {plan.path}: {exc}", fg="red"
                ),
                err=True,
            )
            return EXIT_DISK_IO_ERROR

    if not quiet:
        click.echo(_render_summary_block(plans, source_code, dry_run=False))
    return EXIT_OK


def _run_dry_run_mode(
    plans: list[_PlannedWrite], source_code: str, quiet: bool
) -> int:
    """Print each planned write without touching disk.

    Args:
        plans: The full list of planned writes.
        source_code: For the summary header.
        quiet: When ``True`` suppress the per-plan listing and the
            summary block (only category counts via the summary line
            survive).

    Returns:
        Always :data:`EXIT_OK` -- dry-run is informational.
    """
    if not quiet:
        click.echo(f"onboard-source --dry-run: {source_code}")
        for plan in plans:
            size_bytes = len(plan.content.encode("utf-8"))
            click.echo(
                f"  [{plan.category:<11}] {plan.path} ({size_bytes} bytes)"
            )
        click.echo("")
        click.echo(_render_summary_block(plans, source_code, dry_run=True))
    return EXIT_OK


def _run_check_mode(
    plans: list[_PlannedWrite], source_code: str, quiet: bool
) -> int:
    """Compare each planned artefact to its committed counterpart.

    Args:
        plans: The full list of planned writes.
        source_code: For the report header.
        quiet: When ``True`` suppress per-artefact OK lines; drift
            lines are always shown.

    Returns:
        :data:`EXIT_OK` when every artefact matches; otherwise
        :data:`EXIT_DRIFT_OR_WORKBOOK_ERROR`.
    """
    if not quiet:
        click.echo(f"onboard-source --check: {source_code}")

    matches = 0
    drifts: list[tuple[_PlannedWrite, str]] = []

    for plan in plans:
        ok, drift_msg = _compare_artefact(plan)
        if ok:
            matches += 1
            continue
        drifts.append((plan, drift_msg or "drift detected"))

    total = len(plans)
    if drifts:
        for plan, msg in drifts:
            click.echo(
                click.style(f"  drift {plan.path}: {msg}", fg="red"),
                err=True,
            )
        click.echo(
            click.style(
                f"  {matches} of {total} artefacts match.", fg="yellow"
            ),
            err=True,
        )
        return EXIT_DRIFT_OR_WORKBOOK_ERROR

    if not quiet:
        click.echo(
            click.style(
                f"  all {total} artefacts match committed state.", fg="green"
            )
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Top-level orchestration -- callable from main.py (no Click decoration
# here so the CLI registration stays in src/main.py per the convention).
# ---------------------------------------------------------------------------


def run_onboard_source(
    workbook_path: str,
    *,
    output_root: str | None = None,
    source_dir: str | None = None,
    mapping_dir: str | None = None,
    rules_dir: str | None = None,
    reconciliation_dir: str | None = None,
    sql_dir: str | None = None,
    dry_run: bool = False,
    check: bool = False,
    quiet: bool = False,
    frozen_timestamp: str | None = None,
) -> int:
    """Top-level dispatch for the ``valdo onboard-source`` command.

    Reads the workbook, runs every emitter, then dispatches to the
    appropriate mode handler. All error paths return an integer exit
    code rather than raising, so the CLI wrapper in :mod:`src.main`
    can ``sys.exit`` cleanly.

    Args:
        workbook_path: Filesystem path to the ``.xlsx`` workbook.
        output_root: Optional project-root override for default-layout
            writes. Defaults to the current working directory.
        source_dir: Optional override for the source-YAML directory.
        mapping_dir: Optional override for the mapping directory.
        rules_dir: Optional override for the rules directory.
        dry_run: When ``True``, print planned writes and exit 0.
            Mutually exclusive with ``check``.
        check: When ``True``, compare against committed files and
            exit non-zero on drift. Mutually exclusive with ``dry_run``.
        quiet: When ``True`` suppress non-essential output.
        frozen_timestamp: EC-S10 deterministic-timestamp override.
            When set (e.g. ``"GENERATED"`` for test fixtures, or any
            ISO 8601 string for stable artefacts), the underlying
            mapping + rules converters substitute the value into
            every ``metadata.created_date`` /
            ``metadata.last_modified`` field instead of calling
            ``datetime.utcnow()``. When ``None`` (default), emitters
            preserve wall-clock behaviour for full backwards
            compatibility. In ``--check`` mode the comparison ALWAYS
            auto-extracts the committed timestamps regardless of this
            flag (so the flag has no functional effect under
            ``--check`` -- the auto-extract dominates).

    Returns:
        :data:`EXIT_OK` on success; :data:`EXIT_DRIFT_OR_WORKBOOK_ERROR`
        on workbook/emitter errors or check-mode drift;
        :data:`EXIT_DISK_IO_ERROR` on disk write failure.
    """
    if dry_run and check:
        click.echo(
            click.style(
                "Error: --dry-run and --check are mutually exclusive.",
                fg="red",
            ),
            err=True,
        )
        return EXIT_DRIFT_OR_WORKBOOK_ERROR

    output_root_path = Path(output_root) if output_root else Path.cwd()
    source_dir_path = Path(source_dir) if source_dir else None
    mapping_dir_path = Path(mapping_dir) if mapping_dir else None
    rules_dir_path = Path(rules_dir) if rules_dir else None
    reconciliation_dir_path = (
        Path(reconciliation_dir) if reconciliation_dir else None
    )
    sql_dir_path = Path(sql_dir) if sql_dir else None

    # 1. Read the workbook.
    try:
        workbook = read_workbook(workbook_path)
    except WorkbookSchemaError as exc:
        click.echo(
            click.style(f"Workbook schema validation failed:\n{exc}", fg="red"),
            err=True,
        )
        return EXIT_DRIFT_OR_WORKBOOK_ERROR
    except WorkbookReadError as exc:
        click.echo(
            click.style(f"Workbook read error: {exc}", fg="red"),
            err=True,
        )
        return EXIT_DRIFT_OR_WORKBOOK_ERROR

    # 2. Run all emitters + resolve destination paths.
    try:
        plans = _plan_writes(
            workbook,
            output_root=output_root_path,
            source_dir=source_dir_path,
            mapping_dir=mapping_dir_path,
            rules_dir=rules_dir_path,
            reconciliation_dir=reconciliation_dir_path,
            sql_dir=sql_dir_path,
            frozen_timestamp=frozen_timestamp,
        )
    except EmitterError as exc:
        click.echo(
            click.style(f"Emitter error: {exc}", fg="red"),
            err=True,
        )
        return EXIT_DRIFT_OR_WORKBOOK_ERROR

    source_code = workbook.source.source_code

    # 3. Dispatch.
    if check:
        return _run_check_mode(plans, source_code, quiet)
    if dry_run:
        return _run_dry_run_mode(plans, source_code, quiet)
    return _run_normal_mode(plans, source_code, quiet)


# ---------------------------------------------------------------------------
# Click command -- the CLI registration site.
# ---------------------------------------------------------------------------


@click.command("onboard-source")
@click.argument(
    "workbook_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False),
    help="Project root for default-layout writes (default: current directory).",
)
@click.option(
    "--source-dir",
    default=None,
    type=click.Path(file_okay=False),
    help='Directory for the source YAML (default: "config/e2e/sources").',
)
@click.option(
    "--mapping-dir",
    default=None,
    type=click.Path(file_okay=False),
    help='Directory for mapping JSON/YAML artefacts (default: "config/mappings").',
)
@click.option(
    "--rules-dir",
    default=None,
    type=click.Path(file_okay=False),
    help='Directory for rules JSON artefacts (default: "config/rules").',
)
@click.option(
    "--reconciliation-dir",
    default=None,
    type=click.Path(file_okay=False),
    help=(
        "Directory for reconciliation YAML artefacts (default: "
        '"config/e2e/sources/<SOURCE>/reconciliation/"). When provided, '
        "every artefact is flattened into the supplied directory."
    ),
)
@click.option(
    "--sql-dir",
    default=None,
    type=click.Path(file_okay=False),
    help=(
        "Directory for emitted expected_*.sql artefacts (default: "
        '"config/e2e/sources/<SOURCE>/sql/<filetype>/20_query/"). When '
        "provided, every emitted SQL file is flattened into the supplied "
        "directory (ED-S2)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print planned writes without touching disk; exit 0.",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Compare against committed files; exit nonzero on drift. CI guardrail.",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress the summary table; only errors are printed.",
)
@click.option(
    "--frozen-timestamp",
    "frozen_timestamp",
    default=None,
    type=str,
    metavar="TEXT",
    help=(
        "EC-S10 deterministic timestamp. When set, replaces "
        "datetime.utcnow() in every emitted JSON's metadata "
        "(created_date / last_modified). Two consecutive runs with "
        'the same value (e.g. "GENERATED" or a fixed ISO timestamp) '
        "produce byte-identical artefacts. Ignored under --check "
        "(which auto-extracts the committed timestamp per-artefact)."
    ),
)
def onboard_source(
    workbook_path: str,
    output_root: str | None,
    source_dir: str | None,
    mapping_dir: str | None,
    rules_dir: str | None,
    reconciliation_dir: str | None,
    sql_dir: str | None,
    dry_run: bool,
    check: bool,
    quiet: bool,
    frozen_timestamp: str | None,
) -> None:
    """Onboard a new source from a single Excel workbook.

    Reads the workbook at WORKBOOK_PATH, runs the EC-S3 / EC-S4 / EC-S5
    emitters in memory, and writes the resulting source YAML + mapping
    JSON/YAML + rules JSON artefacts to disk. The BA's single-command
    path from workbook to engine-ready inputs.

    Example:

        valdo onboard-source templates/SHAW_onboarding.xlsx

    For CI guardrail use (verify the workbook still regenerates the
    committed state):

        valdo onboard-source templates/SHAW_onboarding.xlsx --check
    """
    rc = run_onboard_source(
        workbook_path=workbook_path,
        output_root=output_root,
        source_dir=source_dir,
        mapping_dir=mapping_dir,
        rules_dir=rules_dir,
        reconciliation_dir=reconciliation_dir,
        sql_dir=sql_dir,
        dry_run=dry_run,
        check=check,
        quiet=quiet,
        frozen_timestamp=frozen_timestamp,
    )
    if rc != EXIT_OK:
        sys.exit(rc)


__all__ = ["onboard_source", "run_onboard_source"]
