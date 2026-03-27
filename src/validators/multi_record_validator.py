"""Multi-record-type file validation — issues #161/#162.

Validates files that contain multiple record types identified by a discriminator
field at a fixed byte position (e.g. header/detail/trailer batch files).

Workflow:
1. Read the file line by line.
2. Extract the discriminator value from each line.
3. Identify which record type each line belongs to (by value, or by position).
4. Group rows by record type.
5. Enforce ``expect`` cardinality constraints on each type.
6. Optionally run per-type validation via :func:`~src.services.validate_service.run_validate_service`.
7. Run cross-type rules via :class:`~src.validators.cross_type_validator.CrossTypeValidator`.
8. Return an aggregate result dict.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config.multi_record_config import (
    CrossTypeRule,
    DiscriminatorConfig,
    MultiRecordConfig,
    RecordTypeConfig,
)
from src.validators.cross_type_validator import CrossTypeValidator
from src.validators.rule_engine import RuleViolation

_logger = logging.getLogger(__name__)

# Sentinel for rows that couldn't be identified.
_UNKNOWN_TYPE = "__unknown__"


class MultiRecordValidator:
    """Validate a file containing multiple record types.

    Usage::

        from src.config.multi_record_config import MultiRecordConfig
        from src.validators.multi_record_validator import MultiRecordValidator

        config = MultiRecordConfig(...)
        result = MultiRecordValidator().validate("path/to/file.txt", config)
    """

    def validate(self, file_path: str, config: MultiRecordConfig) -> Dict[str, Any]:
        """Validate a multi-record file against the given configuration.

        Steps:
            1. Read and group all rows by record type.
            2. Emit violations for unknown rows according to ``default_action``.
            3. Enforce ``expect`` cardinality on each configured record type.
            4. Run per-type schema/rules validation when a mapping path is given.
            5. Run cross-type rules.
            6. Return an aggregate result dict.

        Args:
            file_path: Path to the multi-record data file.
            config: :class:`~src.config.multi_record_config.MultiRecordConfig`
                describing the file structure.

        Returns:
            Dict with keys:
              - ``total_rows`` (int): Total non-empty lines in the file.
              - ``record_type_results`` (dict): Per-type validation results.
              - ``cross_type_violations`` (list[dict]): Cross-type rule violations.
              - ``valid`` (bool): True when no error-severity violations exist.
        """
        groups, all_rows_types, total_rows = self._read_and_group(file_path, config)

        cross_type_violations: List[RuleViolation] = []

        # --- Handle unknown rows ---
        unknown_rows = groups.pop(_UNKNOWN_TYPE, [])
        if unknown_rows:
            cross_type_violations.extend(
                self._handle_unknown_rows(unknown_rows, config)
            )

        # --- Enforce expect cardinality ---
        cross_type_violations.extend(
            self._enforce_expect(groups, config)
        )

        # --- Per-type validation ---
        record_type_results: Dict[str, Any] = {}
        for type_name, type_config in config.record_types.items():
            rows = groups.get(type_name, [])
            if rows and type_config.mapping:
                try:
                    result = self._validate_record_group(
                        type_name, rows, type_config
                    )
                    record_type_results[type_name] = result
                except Exception as exc:
                    _logger.warning(
                        "Per-type validation for '%s' failed: %s", type_name, exc
                    )
                    record_type_results[type_name] = {
                        "error": str(exc),
                        "valid": False,
                    }
            else:
                record_type_results[type_name] = {
                    "row_count": len(rows),
                    "valid": True,
                    "skipped": "no_mapping" if not type_config.mapping else "no_rows",
                }

        # --- Cross-type rules ---
        cross_type_validator = CrossTypeValidator()
        cross_type_violations.extend(
            cross_type_validator.validate(groups, config.cross_type_rules, all_rows_types)
        )

        # Determine overall validity: any error-severity violation makes it invalid.
        has_errors = any(
            v.severity == "error" for v in cross_type_violations
        )
        # Also propagate per-type errors.
        for type_result in record_type_results.values():
            if not type_result.get("valid", True):
                has_errors = True
                break

        return {
            "total_rows": total_rows,
            "record_type_results": record_type_results,
            "cross_type_violations": [v.to_dict() for v in cross_type_violations],
            "valid": not has_errors,
        }

    # ------------------------------------------------------------------
    # Public helpers (used by tests)
    # ------------------------------------------------------------------

    def _group_rows(
        self, file_path: str, config: MultiRecordConfig
    ) -> Dict[str, List[str]]:
        """Group raw lines by record type name.

        Exposed for unit testing the grouping logic independently.

        Args:
            file_path: Path to the data file.
            config: Multi-record configuration.

        Returns:
            Dict mapping type name (or ``_UNKNOWN_TYPE``) to list of raw line strings.
        """
        groups, _, _ = self._read_and_group(file_path, config)
        return groups

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _read_and_group(
        self, file_path: str, config: MultiRecordConfig
    ) -> Tuple[Dict[str, List[str]], List[str], int]:
        """Read the file and group lines by record type.

        Args:
            file_path: Path to the data file.
            config: Multi-record configuration.

        Returns:
            Tuple of:
              - groups: Dict mapping type name to list of line strings.
              - all_rows_types: Ordered list of type names (one per line).
              - total_rows: Total number of non-empty lines.
        """
        lines: List[str] = []
        try:
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                lines = [line.rstrip("\n\r") for line in fh if line.strip()]
        except OSError as exc:
            _logger.error("Cannot open file '%s': %s", file_path, exc)
            return {}, [], 0

        total_rows = len(lines)

        groups: Dict[str, List[str]] = {name: [] for name in config.record_types}
        groups[_UNKNOWN_TYPE] = []
        all_rows_types: List[str] = []

        for idx, line in enumerate(lines):
            disc_value = self._extract_discriminator(line, config.discriminator)
            type_name = self._identify_record_type(
                disc_value, idx, total_rows, config
            )
            if type_name is None:
                groups[_UNKNOWN_TYPE].append(line)
                all_rows_types.append(None)
            else:
                groups[type_name].append(line)
                all_rows_types.append(type_name)

        return groups, all_rows_types, total_rows

    def _extract_discriminator(
        self, line: str, disc: DiscriminatorConfig
    ) -> str:
        """Extract the discriminator value from a line using position/length.

        The position is 1-indexed. For example, ``position=1, length=3`` extracts
        characters 0-2 (0-indexed).

        Args:
            line: A single raw line from the file.
            disc: Discriminator configuration specifying position and length.

        Returns:
            Stripped substring, or empty string if the line is too short.
        """
        start = disc.position - 1  # Convert to 0-indexed
        end = start + disc.length
        if len(line) < start + 1:
            return ""
        return line[start:end].strip()

    def _identify_record_type(
        self,
        value: str,
        row_index: int,
        total_rows: int,
        config: MultiRecordConfig,
    ) -> Optional[str]:
        """Match a discriminator value to a configured record type name.

        Matching priority:
        1. If ``row_index == 0`` and any type has ``position="first"``, that type wins.
        2. If ``row_index == total_rows - 1`` and any type has ``position="last"``, that type wins.
        3. Otherwise, the first type whose ``match`` equals ``value`` wins.

        Args:
            value: Extracted discriminator string.
            row_index: 0-indexed row position in the file.
            total_rows: Total number of non-empty rows.
            config: Multi-record configuration.

        Returns:
            Record type name string, or None if no type matches.
        """
        # Check positional overrides first.
        if row_index == 0:
            for type_name, type_config in config.record_types.items():
                if type_config.position == "first":
                    return type_name

        if total_rows > 0 and row_index == total_rows - 1:
            for type_name, type_config in config.record_types.items():
                if type_config.position == "last":
                    return type_name

        # Fall back to value matching.
        for type_name, type_config in config.record_types.items():
            if type_config.match and type_config.match == value:
                return type_name

        return None

    def _handle_unknown_rows(
        self, unknown_rows: List[str], config: MultiRecordConfig
    ) -> List[RuleViolation]:
        """Generate violations for unrecognized record type lines.

        Args:
            unknown_rows: List of raw lines that did not match any configured type.
            config: Multi-record configuration (used for ``default_action``).

        Returns:
            List of :class:`~src.validators.rule_engine.RuleViolation` objects,
            or empty list when ``default_action="skip"``.
        """
        if config.default_action == "skip":
            return []

        severity = "error" if config.default_action == "error" else "warning"
        violations: List[RuleViolation] = []

        for idx, line in enumerate(unknown_rows):
            disc_value = self._extract_discriminator(line, config.discriminator)
            violations.append(
                RuleViolation(
                    rule_id="CT_UNKNOWN_RECORD_TYPE",
                    rule_name="unknown_record_type",
                    severity=severity,
                    row_number=idx + 1,
                    field=config.discriminator.field,
                    value=disc_value,
                    message=(
                        f"Unrecognized record type: discriminator value "
                        f"'{disc_value}' does not match any configured type."
                    ),
                    issue_code="CT_UNKNOWN",
                )
            )
        return violations

    def _enforce_expect(
        self,
        groups: Dict[str, List[str]],
        config: MultiRecordConfig,
    ) -> List[RuleViolation]:
        """Enforce ``expect`` cardinality constraints on each record type group.

        Args:
            groups: Dict mapping type name to list of grouped lines.
            config: Multi-record configuration.

        Returns:
            List of :class:`~src.validators.rule_engine.RuleViolation` objects.
        """
        violations: List[RuleViolation] = []

        for type_name, type_config in config.record_types.items():
            count = len(groups.get(type_name, []))
            expect = type_config.expect

            if expect == "exactly_one" and count != 1:
                violations.append(
                    RuleViolation(
                        rule_id="CT_EXPECT",
                        rule_name="expect_cardinality",
                        severity="error",
                        row_number=0,
                        field=type_name,
                        value=count,
                        message=(
                            f"Expected exactly 1 row of type '{type_name}' "
                            f"but found {count}."
                        ),
                        issue_code="CT_EXPECT_EXACTLY_ONE",
                    )
                )
            elif expect == "at_least_one" and count < 1:
                violations.append(
                    RuleViolation(
                        rule_id="CT_EXPECT",
                        rule_name="expect_cardinality",
                        severity="error",
                        row_number=0,
                        field=type_name,
                        value=count,
                        message=(
                            f"Expected at least 1 row of type '{type_name}' "
                            f"but found {count}."
                        ),
                        issue_code="CT_EXPECT_AT_LEAST_ONE",
                    )
                )

        return violations

    def _validate_record_group(
        self,
        record_type: str,
        rows: List[str],
        type_config: RecordTypeConfig,
    ) -> Dict[str, Any]:
        """Write grouped rows to a temp file and run per-type validation.

        Args:
            record_type: Logical record type name (for logging).
            rows: List of raw line strings for this record type.
            type_config: :class:`~src.config.multi_record_config.RecordTypeConfig`
                containing the mapping and rules paths.

        Returns:
            Validation result dict from
            :func:`~src.services.validate_service.run_validate_service`.
        """
        from src.services.validate_service import run_validate_service

        # Write rows to a temp file for the validate service.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
            prefix=f"valdo_mr_{record_type}_",
        ) as tmp:
            tmp.write("\n".join(rows))
            tmp_path = tmp.name

        try:
            result = run_validate_service(
                file=tmp_path,
                mapping=type_config.mapping or None,
                rules=type_config.rules or None,
            )
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        return result
