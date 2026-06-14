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

    # 2. Mapping artefacts.
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

    return plans


# ---------------------------------------------------------------------------
# Equivalence checking (for ``--check`` mode).
# ---------------------------------------------------------------------------


def _strip_metadata(data: Any) -> Any:
    """Return ``data`` with the converter-generated ``metadata`` block dropped.

    Pre-EC-S10 fallback used when ``--check`` cannot auto-extract
    timestamps from the committed artefact (e.g. committed file missing
    the ``metadata`` key entirely, or operator passed an explicit
    ``--frozen-timestamp`` that happens to mismatch every committed
    value). EC-S10 prefers :func:`_normalise_metadata_for_compare`
    which preserves the metadata block while neutralising only the
    irreducibly-variable fields.

    Args:
        data: A dict loaded from a converter-produced JSON/YAML
            artefact, or any other value (passed through unchanged).

    Returns:
        ``data`` with the top-level ``metadata`` key removed when
        ``data`` is a dict; otherwise returned as-is.
    """
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "metadata"}
    return data


def _normalise_metadata_for_compare(
    emitted: Any, committed: Any
) -> tuple[Any, Any]:
    """Normalise both sides' ``metadata`` blocks so byte equality is meaningful (EC-S10).

    Two converter-embedded fields are irreducibly variable between the
    emitter and the on-disk committed file:

      * ``created_date`` / ``last_modified`` -- the converter calls
        ``datetime.utcnow()`` on every run unless ``frozen_timestamp``
        was set. The committed JSON carries whatever timestamp the
        operator who first generated it had on their workstation; the
        emitter (without a timestamp explicitly threaded) has the
        current time. Both are equally "correct" -- semantically these
        fields are just an audit hint, not part of the schema.

      * ``source_template`` / ``template_path`` -- the committed JSONs
        carry Windows-style absolute paths from the historical
        CSV-driven authoring workflow (e.g.
        ``mappings\\csv\\shaw_tranert\\SHAW_TRANERT_BATCH_HEADER_mapping.csv``).
        The workbook-driven emitter passes a synthetic POSIX-style
        basename (``SHAW_TRANERT_BATCH_HEADER_mapping.csv``). The two
        paths point at the same logical template -- the comparison
        should ignore the directory part and treat ``\\`` / ``/``
        identically. We coerce both sides to the trailing component
        only (``Path.name``).

    The function returns two copies (emitted and committed) with both
    fields rewritten so a deep dict equality check is meaningful for
    every other field (i.e. real schema drift still surfaces as
    drift).

    Args:
        emitted: The dict produced by the emitter (or ``None`` /
            non-dict; passed through).
        committed: The dict loaded from disk (or ``None`` / non-dict;
            passed through).

    Returns:
        Tuple ``(emitted_normalised, committed_normalised)``. Both
        sides are deep-copied (the metadata block is rewritten in
        place on the copy) so callers can mutate without affecting
        the originals.
    """
    if not (isinstance(emitted, dict) and isinstance(committed, dict)):
        return emitted, committed

    emitted_copy = {**emitted}
    committed_copy = {**committed}

    emitted_meta = emitted_copy.get("metadata")
    committed_meta = committed_copy.get("metadata")
    if isinstance(emitted_meta, dict) and isinstance(committed_meta, dict):
        emitted_meta = {**emitted_meta}
        committed_meta = {**committed_meta}

        # EC-S10: auto-extract -- substitute committed timestamps into
        # the emitted block. If the committed block lacks a particular
        # timestamp key, the emitted value is dropped too (symmetric).
        for ts_key in ("created_date", "last_modified"):
            if ts_key in committed_meta:
                emitted_meta[ts_key] = committed_meta[ts_key]
            elif ts_key in emitted_meta:
                # Committed has none; drop the emitter-side so the
                # comparison does not surface this as drift.
                emitted_meta.pop(ts_key, None)

        # Basename-only comparison for the two path fields. The
        # converter stores ``source_template`` (mapping converter) and
        # ``template_path`` (rules converter); both are file paths to
        # the originating CSV/XLSX template.
        for path_key in ("source_template", "template_path"):
            if path_key in committed_meta:
                committed_meta[path_key] = _path_basename(
                    committed_meta[path_key]
                )
            if path_key in emitted_meta:
                emitted_meta[path_key] = _path_basename(
                    emitted_meta[path_key]
                )

        emitted_copy["metadata"] = emitted_meta
        committed_copy["metadata"] = committed_meta

    return emitted_copy, committed_copy


def _path_basename(value: Any) -> Any:
    """Return ``value``'s trailing path component, treating ``\\`` like ``/``.

    Used by :func:`_normalise_metadata_for_compare` so the Windows-style
    historical paths in the committed JSONs compare equal to the
    POSIX-style synthetic basenames the emitter produces. Non-string
    inputs are passed through unchanged for safety.

    Args:
        value: A string filesystem path (Windows or POSIX) or any
            other value.

    Returns:
        The trailing path component when ``value`` is a string, or
        ``value`` unchanged otherwise.
    """
    if not isinstance(value, str):
        return value
    # Coerce Windows separators to POSIX so Path() behaves identically
    # regardless of which OS authored the committed file.
    return Path(value.replace("\\", "/")).name


def _extract_committed_timestamp(data: Any) -> str | None:
    """Pull a deterministic timestamp out of a committed artefact's metadata.

    Used by the ``--check`` mode to auto-derive a per-artefact
    ``frozen_timestamp`` for the comparison. The returned string is
    fed back into the emitter on the comparison path (or more
    precisely, it's the value we substitute into the emitted artefact
    via :func:`_normalise_metadata_for_compare`).

    Args:
        data: A dict loaded from a committed JSON / YAML artefact, or
            any other value.

    Returns:
        The committed ``metadata.created_date`` string when present,
        else ``None``.
    """
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("created_date")
    if isinstance(value, str):
        return value
    return None


def _load_committed_artefact(
    path: Path, category: str
) -> tuple[Any, bool]:
    """Load a committed artefact for ``--check`` comparison.

    Args:
        path: On-disk path of the committed artefact.
        category: The artefact category (``"source_yaml"`` /
            ``"mapping"`` / ``"rules"``).

    Returns:
        A tuple ``(parsed_data, exists)``. When the file is absent
        ``exists=False`` and ``parsed_data`` is ``None``. When parse
        fails the exception propagates -- the CLI surfaces it as a
        drift entry rather than crashing the whole run.
    """
    if not path.exists():
        return None, False
    text = path.read_text(encoding="utf-8")
    if category == "source_yaml":
        return yaml.safe_load(text), True
    if path.suffix == ".yaml" or path.suffix == ".yml":
        return yaml.safe_load(text), True
    return json.loads(text), True


def _parse_emitted_artefact(plan: _PlannedWrite) -> Any:
    """Parse a planned artefact's content for ``--check`` comparison.

    Args:
        plan: The planned write whose content needs parsing.

    Returns:
        The parsed data (YAML for ``.yml``/``.yaml`` artefacts and the
        source YAML; JSON for everything else).
    """
    if plan.category == "source_yaml":
        return yaml.safe_load(plan.content)
    if plan.path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(plan.content)
    return json.loads(plan.content)


def _compare_artefact(
    plan: _PlannedWrite,
) -> tuple[bool, str | None]:
    """Compare one emitted artefact against its committed counterpart.

    Uses the equivalence contract appropriate for the category:

        * ``source_yaml`` -- semantic YAML equality (no metadata
          normalisation; YAML config has no converter-embedded
          timestamps).
        * ``mapping`` / ``rules`` -- post-EC-S10 normalisation: the
          emitted and committed ``metadata.created_date`` /
          ``metadata.last_modified`` timestamps are unified
          (committed wins) and the ``source_template`` /
          ``template_path`` fields are coerced to basename-only
          (Windows ``\\`` / POSIX ``/`` separators are normalised
          first). Every other field including the rest of the
          ``metadata`` block (``created_by``, ``name``, ``description``,
          ``template_type``) is compared verbatim, so real schema
          drift still surfaces as drift.

    Args:
        plan: The planned write to compare.

    Returns:
        ``(matches, drift_message)``. When ``matches=True`` the
        message is ``None``. Otherwise the message is a single-line
        explanation suitable for inclusion in the CLI drift report.
    """
    emitted_data = _parse_emitted_artefact(plan)
    committed_data, exists = _load_committed_artefact(plan.path, plan.category)

    if not exists:
        return False, f"committed file does not exist (would be created)"

    if plan.category in {"source_yaml", "reconciliation"}:
        # Source YAML and reconciliation YAML carry no converter-embedded
        # timestamps — direct semantic equality via ``yaml.safe_load`` is
        # the correct equivalence contract.
        if emitted_data == committed_data:
            return True, None
        return False, _summarise_dict_drift(emitted_data, committed_data)

    # mapping / rules -- EC-S10 metadata normalisation:
    #   * substitute committed timestamps into the emitted block;
    #   * coerce path-style metadata fields to basenames.
    emitted_norm, committed_norm = _normalise_metadata_for_compare(
        emitted_data, committed_data
    )
    if emitted_norm == committed_norm:
        return True, None
    return False, _summarise_dict_drift(emitted_norm, committed_norm)


def _summarise_dict_drift(emitted: Any, committed: Any) -> str:
    """Produce a short human-readable summary of structural drift.

    Best-effort: surfaces the first divergent top-level key when both
    sides are dicts, else falls back to a generic "values differ"
    message. Detailed diffs are out of scope -- operators can re-run
    the emitter and diff the artefact against the committed file by
    hand for forensic investigation.

    Args:
        emitted: The emitted (in-memory) artefact structure.
        committed: The committed (on-disk) artefact structure.

    Returns:
        A one-line drift summary suitable for the CLI report.
    """
    if isinstance(emitted, dict) and isinstance(committed, dict):
        emitted_keys = set(emitted.keys())
        committed_keys = set(committed.keys())
        only_emitted = sorted(emitted_keys - committed_keys)
        only_committed = sorted(committed_keys - emitted_keys)
        if only_emitted:
            return f"emitted has extra key(s): {only_emitted}"
        if only_committed:
            return f"committed has extra key(s): {only_committed}"
        # Same keys -- find the first one whose value differs.
        for key in emitted_keys:
            if emitted[key] != committed[key]:
                return f"value for key '{key}' differs"
    return "structural values differ"


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
    total = source_count + mapping_count + rules_count + recon_count

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
        dry_run=dry_run,
        check=check,
        quiet=quiet,
        frozen_timestamp=frozen_timestamp,
    )
    if rc != EXIT_OK:
        sys.exit(rc)


__all__ = ["onboard_source", "run_onboard_source"]
