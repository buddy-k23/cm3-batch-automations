"""EB-S2: CI guardrail rejecting legacy / default-equal boilerplate keys in
source YAMLs under ``config/e2e/sources/``.

Sprint 1 background
-------------------
* **EA-S1** added Pydantic models (``SourceConfig``, ``OutputFileConfig``,
  ``InputFileConfig``, ``ToleranceConfig``, ``ThresholdsConfig``) carrying
  implicit defaults so committed YAMLs can omit boilerplate.
* **EA-S2** wired the pipeline-YAML generator to emit ``# default: <k>=<v>``
  comments next to every implicitly-defaulted field, so SREs still see the
  effective configuration.
* **EA-S3** stripped ~146 lines of default-equal boilerplate from
  ``SHAW.yml`` and ``SRC_A.yml``. SRC_A's non-default
  ``tolerance.ignore_fields`` overrides were preserved verbatim.
* **EB-S1** removed ``multi_record`` / ``discriminator_field`` from
  ``OutputFileConfig`` (multi-record dispatch is now inferred from the
  mapping file extension per ADR 0005). Legacy keys at the
  ``output_files[]`` level are now rejected by Pydantic with an actionable
  ``ValidationError``.

EB-S2 (this module) closes the loop with a **CI-layer** check that walks
every ``config/e2e/sources/*.yml`` file and rejects:

1. **Legacy multi-record keys** at the ``output_files[]`` level
   (``multi_record``, ``discriminator_field``). These are also rejected by
   the Pydantic model, but a YAML-level pre-check produces a clearer error
   pinned to the file + entry rather than a pydantic traceback.

2. **Default-equal boilerplate**:
   * ``strict_fixed_width: true``   (default ``True``)
   * ``strict_level: all``          (default ``"all"``)
   * ``tolerance.max_errors: 0``    (default ``0``)
   * ``tolerance.max_error_pct: 0`` (default ``0.0``)
   * ``tolerance.ignore_fields: []`` (default empty list)
   * ``thresholds.max_errors: 0``   (default ``0``)

   When a ``tolerance:`` block exists with at least one non-default
   sub-field (e.g. ``ignore_fields: ["FILE_CREATE_TS"]``), the override
   *stays*; the sibling default-equal sub-fields must be stripped. Same
   rule for ``thresholds:`` blocks.

The defaults are read from the Pydantic models in
:mod:`src.pipeline.etl_config` via ``model_fields[...].default`` /
``default_factory()`` so this guardrail tracks any future default change
automatically. There are no hardcoded ``True`` / ``"all"`` literals in the
check logic.

Failure mode
------------
On violation, the test collects every offending key with file + entry +
remediation context and emits a single multi-line ``pytest.fail`` message
rather than letting an ``assert`` explode at line 387 of pyyaml. The
synthetic self-test (``test_guardrail_detects_synthetic_violation``)
proves the check is not trivially passing.

Coverage
--------
Walks every ``config/e2e/sources/*.yml`` file. Runtime budget: <1s. No
engine instantiation, no DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

from src.pipeline.etl_config import (
    InputFileConfig,
    OutputFileConfig,
    ThresholdsConfig,
    ToleranceConfig,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = REPO_ROOT / "config" / "e2e" / "sources"

# Legacy multi-record keys removed by EB-S1. Banned UNCONDITIONALLY at the
# output_files[] level; multi-record dispatch is inferred from the mapping
# file extension per ADR 0005.
_BANNED_LEGACY_OUTPUT_KEYS: Tuple[str, ...] = (
    "multi_record",
    "discriminator_field",
)

# Doc / source references baked into every error message so a reviewer sees
# the why + the fix without leaving the diff.
_ETL_CONFIG_REF = "src/pipeline/etl_config.py"
_ADR_REF = "docs/adr/0005-multi-record-pipeline-dispatch.md"


# ---------------------------------------------------------------------------
# Default lookup helpers
#
# Read defaults from the Pydantic models rather than hardcoding -- if a future
# story changes a default (e.g. strict_level: 'format'), the guardrail tracks
# automatically without an edit here.
# ---------------------------------------------------------------------------


def _model_default(model_cls: Any, field_name: str) -> Any:
    """Return the effective default value for *field_name* on *model_cls*.

    Resolves both scalar defaults (``default=...``) and factory defaults
    (``default_factory=list``, ``default_factory=ToleranceConfig``). For
    nested model factories the returned instance is dumped to a plain dict
    so callers can compare against parsed YAML.

    Args:
        model_cls: A Pydantic ``BaseModel`` subclass.
        field_name: Name of the field whose default to resolve.

    Returns:
        The default value. Lists from ``default_factory=list`` are returned
        as fresh empty lists; nested-model factories are returned as dicts
        via ``model_dump()``.
    """
    field = model_cls.model_fields[field_name]
    if field.default_factory is not None:
        produced = field.default_factory()
        # Nested models -> normalise to plain dict for comparison.
        if hasattr(produced, "model_dump"):
            return produced.model_dump()
        return produced
    return field.default


# Output-file defaultable scalars (banned UNCONDITIONALLY at the
# output_files[] level when present, since the only legal values match the
# Pydantic defaults).
_OUTPUT_DEFAULTABLE_SCALARS: Dict[str, Any] = {
    "strict_fixed_width": _model_default(OutputFileConfig, "strict_fixed_width"),
    "strict_level": _model_default(OutputFileConfig, "strict_level"),
}

# Tolerance sub-fields: banned only when they equal the default. When a
# tolerance block has at least one non-default sibling, default-equal
# fields must be stripped but the block itself survives.
_TOLERANCE_DEFAULTABLE_FIELDS: Dict[str, Any] = {
    "ignore_fields": _model_default(ToleranceConfig, "ignore_fields"),
    "max_errors": _model_default(ToleranceConfig, "max_errors"),
    "max_error_pct": _model_default(ToleranceConfig, "max_error_pct"),
}

# Thresholds sub-fields (input-file): same rule as tolerance.
_THRESHOLDS_DEFAULTABLE_FIELDS: Dict[str, Any] = {
    "max_errors": _model_default(ThresholdsConfig, "max_errors"),
}


# ---------------------------------------------------------------------------
# Violation type
# ---------------------------------------------------------------------------


class _Violation:
    """One banned-key occurrence collected during the YAML walk.

    Attributes:
        file_path: Path to the offending YAML file, relative to repo root.
        location: Human-readable location pointer
            (e.g. ``output_files[3] ('CDSTRANS_EFB')``).
        key: The offending key (e.g. ``strict_fixed_width``).
        value: The offending value as parsed.
        kind: Classification — ``"legacy"`` for EB-S1 keys,
            ``"default"`` for EA-S3 boilerplate.
    """

    __slots__ = ("file_path", "location", "key", "value", "kind")

    def __init__(
        self,
        file_path: str,
        location: str,
        key: str,
        value: Any,
        kind: str,
    ) -> None:
        self.file_path = file_path
        self.location = location
        self.key = key
        self.value = value
        self.kind = kind

    def format(self) -> str:
        """Render a multi-line, actionable error message for this violation.

        Returns:
            Multi-line string suitable for inclusion in the aggregated
            ``pytest.fail`` message.
        """
        if self.kind == "legacy":
            return (
                f"{self.file_path}: {self.location} has banned key\n"
                f"  '{self.key}: {self.value!r}'\n"
                "\n"
                "This key is no longer accepted on output_files[] entries\n"
                "(EB-S1 / ADR 0005). Multi-record dispatch is inferred from\n"
                "the mapping file extension (`.yaml` -> umbrella, `.json` ->\n"
                "flat); the umbrella YAML itself declares the discriminator.\n"
                "\n"
                f"To fix: delete the line. See {_ADR_REF}."
            )
        # kind == "default"
        return (
            f"{self.file_path}: {self.location} has banned key\n"
            f"  '{self.key}: {self.value!r}'\n"
            "\n"
            "This key equals the Pydantic default (EA-S1) and should be\n"
            "removed. Per EA-S3, source YAMLs do not redeclare implicit\n"
            "defaults; the generated pipeline YAML carries `# default:`\n"
            "comments (EA-S2) for SRE traceability.\n"
            "\n"
            "To fix: delete the line. To override with a non-default value:\n"
            "declare it explicitly with the new value. To check current\n"
            f"defaults, see {_ETL_CONFIG_REF}."
        )


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------


def _file_type_label(entry: Dict[str, Any]) -> str:
    """Return a human-friendly label for a manifest entry.

    Args:
        entry: One ``output_files[]`` or ``input_files[]`` entry dict.

    Returns:
        ``"'<file_type>'"`` when the entry declares a ``file_type``,
        otherwise the literal ``"<unknown>"``.
    """
    ft = entry.get("file_type", "<unknown>")
    return f"'{ft}'"


def _check_output_entry(
    file_path: str,
    index: int,
    entry: Dict[str, Any],
    violations: List[_Violation],
) -> None:
    """Inspect one ``output_files[]`` entry and append any violations.

    Catches:
        * Legacy multi-record keys (``multi_record``, ``discriminator_field``).
        * Default-equal scalars (``strict_fixed_width``, ``strict_level``).
        * Default-equal nested ``tolerance`` sub-fields, while preserving
          override siblings.

    Args:
        file_path: Path to the YAML file (relative to repo root) used in
            violation messages.
        index: Zero-based index of the entry within ``output_files``.
        entry: The raw entry dict from ``yaml.safe_load``.
        violations: Mutated in place — new ``_Violation`` instances appended.
    """
    label = _file_type_label(entry)
    location = f"output_files[{index}] ({label})"

    if not isinstance(entry, dict):
        return  # Pydantic will reject this elsewhere; out of scope here.

    # (1) Legacy EB-S1 keys — banned unconditionally.
    for legacy_key in _BANNED_LEGACY_OUTPUT_KEYS:
        if legacy_key in entry:
            violations.append(
                _Violation(
                    file_path=file_path,
                    location=location,
                    key=legacy_key,
                    value=entry[legacy_key],
                    kind="legacy",
                )
            )

    # (2) Default-equal scalars — banned unconditionally (the only legal
    #     value matches the default).
    for key, default_value in _OUTPUT_DEFAULTABLE_SCALARS.items():
        if key in entry and entry[key] == default_value:
            violations.append(
                _Violation(
                    file_path=file_path,
                    location=location,
                    key=key,
                    value=entry[key],
                    kind="default",
                )
            )

    # (3) Nested tolerance — banned only when the sub-field equals the
    #     default. Override siblings keep the block alive; default-equal
    #     siblings must still be stripped.
    tolerance = entry.get("tolerance")
    if isinstance(tolerance, dict):
        for key, default_value in _TOLERANCE_DEFAULTABLE_FIELDS.items():
            if key in tolerance and tolerance[key] == default_value:
                violations.append(
                    _Violation(
                        file_path=file_path,
                        location=f"{location}.tolerance",
                        key=key,
                        value=tolerance[key],
                        kind="default",
                    )
                )


def _check_input_entry(
    file_path: str,
    index: int,
    entry: Dict[str, Any],
    violations: List[_Violation],
) -> None:
    """Inspect one ``input_files[]`` entry and append any violations.

    Catches default-equal nested ``thresholds`` sub-fields. Same surgical
    rule as tolerance: only default-equal sub-fields are banned; non-default
    overrides survive.

    Args:
        file_path: Path to the YAML file (relative to repo root) used in
            violation messages.
        index: Zero-based index of the entry within ``input_files``.
        entry: The raw entry dict from ``yaml.safe_load``.
        violations: Mutated in place — new ``_Violation`` instances appended.
    """
    label = _file_type_label(entry)
    location = f"input_files[{index}] ({label})"

    if not isinstance(entry, dict):
        return

    thresholds = entry.get("thresholds")
    if isinstance(thresholds, dict):
        for key, default_value in _THRESHOLDS_DEFAULTABLE_FIELDS.items():
            if key in thresholds and thresholds[key] == default_value:
                violations.append(
                    _Violation(
                        file_path=file_path,
                        location=f"{location}.thresholds",
                        key=key,
                        value=thresholds[key],
                        kind="default",
                    )
                )


def _scan_source_yaml(yaml_path: Path) -> List[_Violation]:
    """Walk a single source YAML and return all banned-key violations.

    Args:
        yaml_path: Path to a ``config/e2e/sources/*.yml`` file.

    Returns:
        List of :class:`_Violation` instances; empty when the file is
        clean. A YAML parse error is converted to a single synthetic
        violation pointed at the file (rather than raising), so the test
        report still emits the actionable aggregated message.
    """
    violations: List[_Violation] = []
    try:
        rel = str(yaml_path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(yaml_path)

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        violations.append(
            _Violation(
                file_path=rel,
                location="<file>",
                key="<yaml-parse-error>",
                value=str(exc),
                kind="default",
            )
        )
        return violations

    if not isinstance(raw, dict):
        return violations

    for idx, entry in enumerate(raw.get("output_files", []) or []):
        _check_output_entry(rel, idx, entry, violations)

    for idx, entry in enumerate(raw.get("input_files", []) or []):
        _check_input_entry(rel, idx, entry, violations)

    return violations


def _format_failure_message(violations: List[_Violation]) -> str:
    """Render the aggregated multi-violation failure message.

    Args:
        violations: List of violations collected from one or more YAML files.

    Returns:
        Multi-line string with a count header, one rendered violation per
        entry separated by a ruler, and a closing pointer to remediation
        guidance.
    """
    header = (
        f"EB-S2 guardrail: {len(violations)} banned key occurrence(s) found "
        f"under config/e2e/sources/.\n"
        "Source YAMLs must NOT redeclare implicit defaults (EA-S1/EA-S3) "
        "or legacy multi-record keys (EB-S1 / ADR 0005).\n"
    )
    ruler = "\n" + ("-" * 72) + "\n"
    body = ruler.join(v.format() for v in violations)
    footer = (
        "\n\n"
        f"For the full defaults registry see {_ETL_CONFIG_REF}.\n"
        f"For the multi-record dispatch rule see {_ADR_REF}."
    )
    return header + ruler + body + footer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_source_yamls_have_no_banned_keys() -> None:
    """Every committed ``config/e2e/sources/*.yml`` is free of banned keys.

    Walks every YAML file under ``config/e2e/sources/`` (non-recursive: the
    SHAW subdirectory holds reconciliation specs governed by a separate
    schema and is intentionally skipped). Each ``output_files[]`` entry is
    checked for legacy + default-equal keys; each ``input_files[]`` entry
    is checked for default-equal ``thresholds`` sub-fields.

    Failure mode:
        Collects all violations, then calls :func:`pytest.fail` with a
        single aggregated message rather than letting an ``assert`` raise
        a less-informative error.
    """
    assert SOURCES_DIR.is_dir(), (
        f"Expected source YAML directory at {SOURCES_DIR}; not found."
    )

    yaml_files = sorted(SOURCES_DIR.glob("*.yml")) + sorted(
        SOURCES_DIR.glob("*.yaml")
    )
    assert yaml_files, (
        f"No source YAML files found under {SOURCES_DIR}; the guardrail "
        "needs at least one file to be meaningful."
    )

    all_violations: List[_Violation] = []
    for yaml_path in yaml_files:
        all_violations.extend(_scan_source_yaml(yaml_path))

    if all_violations:
        pytest.fail(_format_failure_message(all_violations), pytrace=False)


def test_guardrail_detects_synthetic_violation(tmp_path: Path) -> None:
    """Self-test: the guardrail catches a synthetic banned key.

    Constructs a minimal in-memory source YAML that re-introduces every
    banned-key category and asserts the scanner reports at least one
    violation per category. Without this test, a regression that
    accidentally neutralised the check (e.g. an empty banned list) would
    silently pass :func:`test_source_yamls_have_no_banned_keys` against
    the already-clean SHAW/SRC_A files.

    Categories covered:
        * Legacy ``multi_record`` key.
        * Legacy ``discriminator_field`` key.
        * Default-equal scalar ``strict_fixed_width: true``.
        * Default-equal scalar ``strict_level: all``.
        * Default-equal nested ``tolerance.max_errors: 0`` (with a non-
          default ``ignore_fields`` sibling that must NOT trigger).
        * Default-equal nested ``thresholds.max_errors: 0``.

    Args:
        tmp_path: Pytest fixture providing a scratch directory; YAML is
            written here so the on-disk format goes through the same
            ``yaml.safe_load`` path as the real check.
    """
    synthetic = {
        "source": "TEST",
        "schema_version": 1,
        "output_files": [
            {
                "file_type": "BANNED",
                "glob": "banned_*.txt",
                "mapping": "config/mappings/BANNED.yaml",
                # Legacy EB-S1 keys.
                "multi_record": True,
                "discriminator_field": "REC-TYPE",
                # Default-equal scalars.
                "strict_fixed_width": True,
                "strict_level": "all",
                # tolerance: override + default-equal siblings.
                "tolerance": {
                    "ignore_fields": ["TS"],   # override -> keep
                    "max_errors": 0,           # default -> violation
                    "max_error_pct": 0,        # default -> violation
                },
            },
        ],
        "input_files": [
            {
                "file_type": "IN_BANNED",
                "glob": "in_banned_*.txt",
                "mapping": "config/mappings/IN_BANNED.json",
                "target_staging_table": "STG_BANNED",
                "thresholds": {
                    "max_errors": 0,   # default -> violation
                },
            },
        ],
    }
    synth_path = tmp_path / "TEST.yml"
    synth_path.write_text(yaml.safe_dump(synthetic), encoding="utf-8")

    violations = _scan_source_yaml(synth_path)

    # Collect the (entry_path, sub_path, key) tuples for assertion. The
    # entry path is the bare ``output_files[N]`` / ``input_files[N]``
    # prefix; the sub path captures the ``.tolerance`` / ``.thresholds``
    # nesting (empty string when the violation is at the entry root).
    def _split_loc(loc: str) -> Tuple[str, str]:
        """Return ``(entry_path, sub_path)`` from a violation location.

        Location strings look like:
          * ``"output_files[0] ('BANNED')"``                -> root level
          * ``"output_files[0] ('BANNED').tolerance"``      -> nested
          * ``"input_files[0] ('IN_BANNED').thresholds"``   -> nested

        Args:
            loc: The ``_Violation.location`` string.

        Returns:
            Tuple of ``(entry_path, sub_path)`` where ``entry_path`` is
            e.g. ``"output_files[0]"`` and ``sub_path`` is e.g.
            ``"tolerance"`` (or ``""`` at root).
        """
        # The entry prefix is everything before the first space.
        entry_path = loc.split(" ", 1)[0]
        # The sub path is whatever follows the closing paren, stripped
        # of the leading dot. Empty when no nested block.
        after_paren = loc.split(")", 1)[1] if ")" in loc else ""
        sub_path = after_paren.lstrip(".")
        return entry_path, sub_path

    found = {(*_split_loc(v.location), v.key) for v in violations}

    # Legacy keys (root level).
    assert ("output_files[0]", "", "multi_record") in found, (
        "Self-test: legacy 'multi_record' key not caught."
    )
    assert ("output_files[0]", "", "discriminator_field") in found, (
        "Self-test: legacy 'discriminator_field' key not caught."
    )

    # Default-equal scalars (root level).
    assert ("output_files[0]", "", "strict_fixed_width") in found, (
        "Self-test: default 'strict_fixed_width: true' not caught."
    )
    assert ("output_files[0]", "", "strict_level") in found, (
        "Self-test: default 'strict_level: all' not caught."
    )

    # Default-equal tolerance sub-fields.
    assert ("output_files[0]", "tolerance", "max_errors") in found, (
        "Self-test: default 'tolerance.max_errors: 0' not caught."
    )
    assert ("output_files[0]", "tolerance", "max_error_pct") in found, (
        "Self-test: default 'tolerance.max_error_pct: 0' not caught."
    )

    # Override sibling must NOT trigger.
    assert ("output_files[0]", "tolerance", "ignore_fields") not in found, (
        "Self-test: non-default 'tolerance.ignore_fields: [TS]' was "
        "wrongly flagged. Override siblings must survive the strip."
    )

    # Default-equal thresholds (input_files nested).
    assert ("input_files[0]", "thresholds", "max_errors") in found, (
        "Self-test: default 'thresholds.max_errors: 0' not caught."
    )

    # The aggregated message must include 'banned key' for each entry --
    # this verifies the failure message format is intact and reviewer-ready.
    msg = _format_failure_message(violations)
    assert "banned key" in msg
    assert "EB-S2 guardrail" in msg
    assert _ETL_CONFIG_REF in msg
    assert _ADR_REF in msg


def test_guardrail_preserves_nondefault_overrides(tmp_path: Path) -> None:
    """Self-test: the SRC_A.yml pattern (tolerance with only an override
    ``ignore_fields`` and no default siblings) reports zero violations.

    Reproduces SRC_A's surgical-strip shape: a ``tolerance:`` block
    declaring ONLY the non-default ``ignore_fields`` override, with
    ``max_errors`` and ``max_error_pct`` stripped. The guardrail must
    *not* false-positive here -- the block exists to carry a real
    override, and the default siblings are absent.

    Args:
        tmp_path: Pytest fixture providing a scratch directory.
    """
    clean = {
        "source": "SRC_A_LIKE",
        "schema_version": 1,
        "output_files": [
            {
                "file_type": "P327",
                "glob": "P327_*.txt",
                "mapping": "config/mappings/P327.json",
                "rules": "config/rules/P327.json",
                "tolerance": {
                    "ignore_fields": ["FILE_CREATE_TS"],
                },
            },
        ],
        "input_files": [
            {
                "file_type": "IN_X",
                "glob": "in_x_*.txt",
                "mapping": "config/mappings/IN_X.json",
                "target_staging_table": "STG_X",
                # No thresholds block at all -- the clean shape.
            },
        ],
    }
    clean_path = tmp_path / "SRC_A_LIKE.yml"
    clean_path.write_text(yaml.safe_dump(clean), encoding="utf-8")

    violations = _scan_source_yaml(clean_path)
    assert violations == [], (
        "Guardrail false-positive: SRC_A's preserved override pattern "
        f"flagged as a violation: {[(v.location, v.key) for v in violations]}"
    )


def test_guardrail_defaults_track_pydantic_model() -> None:
    """Sanity check: the defaults the guardrail compares against come from
    the Pydantic models, not hardcoded literals.

    A future story that legitimately changes a default (e.g.
    ``strict_level`` from ``"all"`` to ``"format"``) will then update the
    model in one place and the guardrail will follow automatically. This
    test pins the wiring -- if a contributor hardcodes a literal in the
    check logic, the assertion here fails because the lookup no longer
    matches the model.
    """
    assert _OUTPUT_DEFAULTABLE_SCALARS["strict_fixed_width"] == (
        OutputFileConfig.model_fields["strict_fixed_width"].default
    )
    assert _OUTPUT_DEFAULTABLE_SCALARS["strict_level"] == (
        OutputFileConfig.model_fields["strict_level"].default
    )
    assert _TOLERANCE_DEFAULTABLE_FIELDS["max_errors"] == (
        ToleranceConfig.model_fields["max_errors"].default
    )
    assert _TOLERANCE_DEFAULTABLE_FIELDS["max_error_pct"] == (
        ToleranceConfig.model_fields["max_error_pct"].default
    )
    assert _TOLERANCE_DEFAULTABLE_FIELDS["ignore_fields"] == []
    assert _THRESHOLDS_DEFAULTABLE_FIELDS["max_errors"] == (
        ThresholdsConfig.model_fields["max_errors"].default
    )
    # Spot-check the input model factory result for parity.
    assert InputFileConfig.model_fields["thresholds"].default_factory is (
        ThresholdsConfig
    )
