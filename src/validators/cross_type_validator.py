"""Cross-record-type validation engine — issues #161/#162.

Validates rules that span multiple record type groups within a single file.

Supported check types:

- ``required_companion``     -- if when_type present, requires_type must also exist
- ``header_trailer_count``   -- trailer count field must match actual detail row count
- ``header_trailer_sum``     -- trailer sum field must match sum of detail field values
- ``header_detail_consistent`` -- header field value must match all detail field values
- ``header_trailer_match``   -- header and trailer fields must have equal values
- ``type_sequence``          -- record types must appear in declared order
- ``expect_count``           -- record type group must have exactly N rows
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.config.multi_record_config import CrossTypeRule
from src.validators.rule_engine import RuleViolation

_logger = logging.getLogger(__name__)

# Issue code prefix for cross-type violations.
_ISSUE_PREFIX = "CT"


class CrossTypeValidator:
    """Validate rules that span multiple record-type groups.

    Each check method accepts the grouped rows dict and a rule, and returns a
    list of :class:`~src.validators.rule_engine.RuleViolation` objects.

    The entry point is :meth:`validate`, which dispatches to the correct check
    method based on ``rule.check``.
    """

    _DISPATCH: dict[str, str] = {
        "required_companion": "_check_required_companion",
        "header_trailer_count": "_check_header_trailer_count",
        "header_trailer_sum": "_check_header_trailer_sum",
        "header_detail_consistent": "_check_header_detail_consistent",
        "header_trailer_match": "_check_header_trailer_match",
        "type_sequence": "_check_type_sequence",
        "expect_count": "_check_expect_count",
    }

    def validate(
        self,
        groups: Dict[str, List[Any]],
        rules: List[CrossTypeRule],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Run all cross-type rules and return accumulated violations.

        Args:
            groups: Dict mapping record type name to list of row dicts.
            rules: List of :class:`~src.config.multi_record_config.CrossTypeRule`
                to evaluate.
            all_rows_types: Ordered list of record type names, one per row
                (used only by ``type_sequence``).

        Returns:
            List of :class:`~src.validators.rule_engine.RuleViolation` objects.
            Empty list when all rules pass.

        Raises:
            ValueError: When ``rule.check`` is not a recognised check type.
        """
        violations: List[RuleViolation] = []
        for rule in rules:
            method_name = self._DISPATCH.get(rule.check)
            if method_name is None:
                raise ValueError(
                    f"Unknown cross_type check: '{rule.check}'. "
                    f"Supported: {sorted(self._DISPATCH)}"
                )
            method = getattr(self, method_name)
            try:
                result = method(rule, groups, all_rows_types)
                violations.extend(result)
            except Exception as exc:
                _logger.warning(
                    "cross_type check '%s' raised an exception: %s", rule.check, exc
                )
        return violations

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_violation(
        self,
        rule: CrossTypeRule,
        field: str,
        value: Any,
        message: str,
        row_number: int = 0,
    ) -> RuleViolation:
        """Build a :class:`~src.validators.rule_engine.RuleViolation`.

        Args:
            rule: The cross-type rule being evaluated.
            field: Field name(s) for display.
            value: Observed value for display.
            message: Human-readable violation message.
            row_number: 1-indexed row number (0 = file-level, not row-specific).

        Returns:
            Populated :class:`~src.validators.rule_engine.RuleViolation`.
        """
        check_upper = rule.check.upper().replace("_", "-")
        issue_code = f"{_ISSUE_PREFIX}_{check_upper}"
        display_message = rule.message if rule.message else message
        return RuleViolation(
            rule_id=f"CT_{rule.check}",
            rule_name=rule.check,
            severity=rule.severity,
            row_number=row_number,
            field=field,
            value=value,
            message=display_message,
            issue_code=issue_code,
        )

    @staticmethod
    def _get_field(row: Any, field_name: str) -> Any:
        """Extract a field value from a row (dict) by name.

        Args:
            row: A row dict.
            field_name: The field key to extract.

        Returns:
            The field value, or None if the field is absent or row is not a dict.
        """
        if isinstance(row, dict):
            return row.get(field_name)
        return None

    # ------------------------------------------------------------------
    # Check implementations
    # ------------------------------------------------------------------

    def _check_required_companion(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when when_type is present but requires_type is absent.

        Args:
            rule: Must have ``when_type`` and ``requires_type`` populated.
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            List with one violation if the companion is missing, else empty list.
        """
        when_rows = groups.get(rule.when_type, [])
        requires_rows = groups.get(rule.requires_type, [])

        if when_rows and not requires_rows:
            msg = (
                rule.message
                or f"Record type '{rule.when_type}' is present but "
                f"required companion '{rule.requires_type}' is absent."
            )
            return [
                self._make_violation(
                    rule,
                    field=rule.requires_type,
                    value=None,
                    message=msg,
                )
            ]
        return []

    def _check_header_trailer_count(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when the count field in the record_type group does not
        match the actual number of rows in the count_of group.

        Args:
            rule: Must have ``record_type``, ``trailer_field``, and ``count_of``.
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            List of violations (one per mismatched trailer row).
        """
        type_rows = groups.get(rule.record_type, [])
        count_of_rows = groups.get(rule.count_of, [])
        actual_count = len(count_of_rows)
        violations: List[RuleViolation] = []

        for row in type_rows:
            declared = self._get_field(row, rule.trailer_field)
            if declared is None:
                continue
            try:
                declared_int = int(str(declared).strip())
            except (ValueError, TypeError):
                continue
            if declared_int != actual_count:
                msg = (
                    rule.message
                    or f"'{rule.record_type}' field '{rule.trailer_field}' declares "
                    f"{declared_int} rows but '{rule.count_of}' has {actual_count}."
                )
                violations.append(
                    self._make_violation(
                        rule,
                        field=rule.trailer_field,
                        value=declared,
                        message=msg,
                    )
                )
        return violations

    def _check_header_trailer_sum(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when the sum field in the record_type group does not
        match the computed sum of detail field(s) in the count_of group.

        Args:
            rule: Must have ``record_type``, ``trailer_field``, ``sum_of``, and
                ``count_of``.
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            List of violations (one per mismatched sum row).
        """
        type_rows = groups.get(rule.record_type, [])
        count_of_rows = groups.get(rule.count_of, [])
        violations: List[RuleViolation] = []

        # Compute actual sum across all sum_of fields in count_of rows.
        fields_to_sum = rule.sum_of or ([rule.sum_field] if rule.sum_field else [])
        actual_sum: float = 0.0
        for row in count_of_rows:
            for field_name in fields_to_sum:
                raw = self._get_field(row, field_name)
                if raw is None:
                    continue
                try:
                    actual_sum += float(str(raw).strip())
                except (ValueError, TypeError):
                    pass

        for row in type_rows:
            declared = self._get_field(row, rule.trailer_field)
            if declared is None:
                continue
            try:
                declared_float = float(str(declared).strip())
            except (ValueError, TypeError):
                continue
            if abs(declared_float - actual_sum) > 1e-9:
                msg = (
                    rule.message
                    or f"'{rule.record_type}' field '{rule.trailer_field}' declares "
                    f"sum={declared_float} but computed sum={actual_sum} "
                    f"from '{rule.count_of}' fields {fields_to_sum}."
                )
                violations.append(
                    self._make_violation(
                        rule,
                        field=rule.trailer_field,
                        value=declared,
                        message=msg,
                    )
                )
        return violations

    def _check_header_detail_consistent(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return violations when any detail row's field value differs from the header.

        Args:
            rule: Must have ``header_field`` and ``detail_field``.
                ``when_type`` defaults to ``"header"``; ``count_of`` defaults to
                ``"detail"`` if not set.
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            One violation per inconsistent detail row.
        """
        header_type = rule.when_type or "header"
        detail_type = rule.count_of or "detail"
        header_rows = groups.get(header_type, [])
        detail_rows = groups.get(detail_type, [])
        violations: List[RuleViolation] = []

        if not header_rows:
            return violations

        header_value = self._get_field(header_rows[0], rule.header_field)
        if header_value is None:
            return violations
        header_str = str(header_value).strip()

        for idx, row in enumerate(detail_rows):
            detail_value = self._get_field(row, rule.detail_field)
            if detail_value is None:
                continue
            if str(detail_value).strip() != header_str:
                msg = (
                    rule.message
                    or f"Detail row {idx + 1} field '{rule.detail_field}' value "
                    f"'{detail_value}' does not match header field "
                    f"'{rule.header_field}' value '{header_value}'."
                )
                violations.append(
                    self._make_violation(
                        rule,
                        field=rule.detail_field,
                        value=detail_value,
                        message=msg,
                        row_number=idx + 1,
                    )
                )
        return violations

    def _check_header_trailer_match(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when header and trailer field values do not match.

        Args:
            rule: Must have ``header_field`` and ``trailer_field``.
                ``when_type`` defaults to ``"header"``; ``record_type`` defaults to
                ``"trailer"``.
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            List with one violation if values differ, else empty list.
        """
        header_type = rule.when_type or "header"
        trailer_type = rule.record_type or "trailer"
        header_rows = groups.get(header_type, [])
        trailer_rows = groups.get(trailer_type, [])
        violations: List[RuleViolation] = []

        if not header_rows or not trailer_rows:
            return violations

        header_value = self._get_field(header_rows[0], rule.header_field)
        trailer_value = self._get_field(trailer_rows[0], rule.trailer_field)

        if header_value is None or trailer_value is None:
            return violations

        if str(header_value).strip() != str(trailer_value).strip():
            msg = (
                rule.message
                or f"Header '{rule.header_field}'='{header_value}' does not match "
                f"trailer '{rule.trailer_field}'='{trailer_value}'."
            )
            violations.append(
                self._make_violation(
                    rule,
                    field=f"{rule.header_field}/{rule.trailer_field}",
                    value=f"{header_value} vs {trailer_value}",
                    message=msg,
                )
            )
        return violations

    def _check_type_sequence(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when record types do not appear in expected order.

        Order is determined by the position of the *first* occurrence of each
        type in ``all_rows_types``.

        Args:
            rule: Must have ``expected_order`` (list of record type names).
            groups: Not used directly by this check.
            all_rows_types: Ordered list of record type names (one per row).

        Returns:
            List with one violation if order is wrong, else empty list.
        """
        expected = rule.expected_order
        if not expected or not all_rows_types:
            return []

        # Build the observed first-occurrence order, skipping None/unknown.
        seen_order: list[str] = []
        seen_set: set[str] = set()
        for t in all_rows_types:
            if t and t not in seen_set and t in expected:
                seen_order.append(t)
                seen_set.add(t)

        # Compute the expected subsequence (only types that actually appear).
        expected_filtered = [t for t in expected if t in seen_set]

        if seen_order != expected_filtered:
            msg = (
                rule.message
                or f"Record type sequence {seen_order} does not match "
                f"expected order {expected_filtered}."
            )
            return [
                self._make_violation(
                    rule,
                    field="record_type_sequence",
                    value=str(seen_order),
                    message=msg,
                )
            ]
        return []

    def _check_expect_count(
        self,
        rule: CrossTypeRule,
        groups: Dict[str, List[Any]],
        all_rows_types: List[str],
    ) -> List[RuleViolation]:
        """Return a violation when a group's row count does not match ``exactly``.

        Args:
            rule: Must have ``record_type`` and ``exactly`` (>= 0).
            groups: Record type groups dict.
            all_rows_types: Not used by this check.

        Returns:
            List with one violation if count mismatches, else empty list.
        """
        if rule.exactly < 0:
            return []

        actual = len(groups.get(rule.record_type, []))
        if actual != rule.exactly:
            msg = (
                rule.message
                or f"Expected exactly {rule.exactly} row(s) for record type "
                f"'{rule.record_type}' but found {actual}."
            )
            return [
                self._make_violation(
                    rule,
                    field="row_count",
                    value=actual,
                    message=msg,
                )
            ]
        return []
