"""Cross-row validation engine — issue #149.

Validates rules that span multiple rows grouped by a key column.
Supported check types:

- ``unique``            -- no duplicate values in a single field
- ``unique_composite``  -- no duplicate combinations across a list of fields
- ``consistent``        -- all rows sharing a key must have the same target value
- ``sequential``        -- sequence_field must be 1,2,3,... within each key group
- ``group_count``       -- actual row count per key must match a declared count field
- ``group_sum``         -- sum of a numeric field per key must fall within bounds
"""

from __future__ import annotations

import logging
import warnings
from typing import List

import pandas as pd

from src.validators.rule_engine import RuleViolation

_logger = logging.getLogger(__name__)


class CrossRowValidator:
    """Validate rules that span multiple rows grouped by key columns.

    Each public ``_check_*`` method accepts a rule dict and a (possibly
    pre-filtered) DataFrame and returns a list of :class:`~src.validators.rule_engine.RuleViolation`
    objects — one per offending row.

    The entry point is :meth:`validate`, which dispatches to the correct
    check method based on ``rule['check']``.
    """

    # Map check-type strings to handler method names.
    _DISPATCH: dict[str, str] = {
        "unique":            "_check_unique",
        "unique_composite":  "_check_unique_composite",
        "consistent":        "_check_consistent",
        "sequential":        "_check_sequential",
        "group_count":       "_check_group_count",
        "group_sum":         "_check_group_sum",
    }

    def validate(self, rule: dict, df: pd.DataFrame) -> List[RuleViolation]:
        """Route to the correct check method based on ``rule['check']``.

        Args:
            rule: Rule configuration dict.  Must contain at least ``id``,
                ``name``, ``severity``, and ``check`` keys.
            df: DataFrame to validate (already filtered by any ``when``
                condition applied by :class:`~src.validators.rule_engine.RuleEngine`).

        Returns:
            List of :class:`~src.validators.rule_engine.RuleViolation` objects.
            Empty list when the DataFrame is empty or all rows pass.

        Raises:
            ValueError: When ``rule['check']`` is not a recognised check type.
        """
        if df.empty:
            return []

        check = rule.get("check", "")
        method_name = self._DISPATCH.get(check)
        if method_name is None:
            raise ValueError(
                f"Unknown cross_row check type: '{check}'. "
                f"Supported types: {sorted(self._DISPATCH)}"
            )
        return getattr(self, method_name)(rule, df)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_violation(
        self,
        rule: dict,
        idx: int,
        field: str,
        value: object,
        message: str,
    ) -> RuleViolation:
        """Build a :class:`~src.validators.rule_engine.RuleViolation` for *idx*.

        Args:
            rule: Rule configuration dict.
            idx: DataFrame index label for the offending row.
            field: Field name(s) for display.
            value: Observed value(s) for display.
            message: Human-readable violation message.

        Returns:
            Populated :class:`~src.validators.rule_engine.RuleViolation`.
        """
        rid = str(rule.get("id", "UNKNOWN")).upper().replace(" ", "_")
        issue_code = f"BR_{rid}_CROSS"
        return RuleViolation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule.get("severity", "error"),
            row_number=int(idx) + 1,  # 1-indexed for user display
            field=field,
            value=value,
            message=message,
            issue_code=issue_code,
        )

    def _warn_missing(self, rule: dict, *columns: str) -> None:
        """Emit a warning when expected columns are absent from the DataFrame.

        Args:
            rule: Rule configuration dict (used for context in the message).
            *columns: Column names that are missing.
        """
        for col in columns:
            warnings.warn(
                f"cross_row rule '{rule.get('id')}' (check={rule.get('check')}): "
                f"column '{col}' not found in DataFrame — rule skipped.",
                stacklevel=3,
            )

    # ------------------------------------------------------------------
    # Check implementations
    # ------------------------------------------------------------------

    def _check_unique(self, rule: dict, df: pd.DataFrame) -> List[RuleViolation]:
        """Return violations for duplicate values in ``rule['field']``.

        NaN values are excluded from duplicate detection (a NaN is not
        considered a duplicate of another NaN).

        Args:
            rule: Must contain ``field`` (str).
            df: DataFrame to inspect.

        Returns:
            One violation per row that participates in a duplicate group.
        """
        field = rule.get("field", "")
        if field not in df.columns:
            self._warn_missing(rule, field)
            return []

        series = df[field]
        # keep_False marks ALL occurrences of duplicated values
        dup_mask = series.duplicated(keep=False) & series.notna()
        violations: List[RuleViolation] = []
        description = rule.get("description", rule.get("name", ""))

        for idx in df[dup_mask].index:
            val = df.loc[idx, field]
            violations.append(
                self._make_violation(
                    rule,
                    idx,
                    field,
                    val,
                    f"{description}: duplicate value '{val}' in field '{field}'",
                )
            )
        return violations

    def _check_unique_composite(
        self, rule: dict, df: pd.DataFrame
    ) -> List[RuleViolation]:
        """Return violations for duplicate row combinations across ``rule['fields']``.

        Args:
            rule: Must contain ``fields`` (list of str).
            df: DataFrame to inspect.

        Returns:
            One violation per row that participates in a duplicate combination.
        """
        fields: list[str] = rule.get("fields", [])
        missing = [f for f in fields if f not in df.columns]
        if missing:
            self._warn_missing(rule, *missing)
            return []

        dup_mask = df.duplicated(subset=fields, keep=False)
        violations: List[RuleViolation] = []
        field_label = ", ".join(fields)
        description = rule.get("description", rule.get("name", ""))

        for idx in df[dup_mask].index:
            combo = tuple(df.loc[idx, f] for f in fields)
            violations.append(
                self._make_violation(
                    rule,
                    idx,
                    field_label,
                    str(combo),
                    f"{description}: duplicate combination {dict(zip(fields, combo))}",
                )
            )
        return violations

    def _check_consistent(self, rule: dict, df: pd.DataFrame) -> List[RuleViolation]:
        """Return violations where rows sharing a key have different target values.

        Groups by ``rule['key_field']`` and checks that ``rule['target_field']``
        has exactly one distinct non-null value within each group.

        Args:
            rule: Must contain ``key_field`` and ``target_field`` (str).
            df: DataFrame to inspect.

        Returns:
            One violation per row in any inconsistent group.
        """
        key_field = rule.get("key_field", "")
        target_field = rule.get("target_field", "")
        missing = [c for c in (key_field, target_field) if c not in df.columns]
        if missing:
            self._warn_missing(rule, *missing)
            return []

        description = rule.get("description", rule.get("name", ""))
        violations: List[RuleViolation] = []

        # Identify keys where the target_field has more than one distinct value.
        inconsistent_keys = (
            df.dropna(subset=[target_field])
            .groupby(key_field)[target_field]
            .nunique()
        )
        bad_keys = inconsistent_keys[inconsistent_keys > 1].index

        if len(bad_keys) == 0:
            return []

        bad_rows = df[df[key_field].isin(bad_keys)]
        for idx in bad_rows.index:
            key_val = df.loc[idx, key_field]
            target_val = df.loc[idx, target_field]
            violations.append(
                self._make_violation(
                    rule,
                    idx,
                    target_field,
                    target_val,
                    f"{description}: '{target_field}' is inconsistent within "
                    f"'{key_field}'='{key_val}' group",
                )
            )
        return violations

    def _check_sequential(
        self, rule: dict, df: pd.DataFrame
    ) -> List[RuleViolation]:
        """Return violations where ``sequence_field`` is not the expected run.

        The check is order-independent: it verifies that the *set* of integer
        values for each key group equals the contiguous run
        ``{start, start + step, …}`` of length ``N`` (the number of rows in the
        group).

        Two optional rule keys parameterise the run (both default to the
        classic ``1, 2, 3, …`` behaviour, so existing rules are unaffected):

        * ``start`` (int, default ``1``) — the first value in the run.
        * ``step``  (int, default ``1``) — the increment between consecutive
          values. A negative step expresses a descending run, e.g.
          ``start=998, step=-1`` requires ``998, 997, 996, …`` per group
          (one value per row), which is how SHAW TRANERT numbers the
          ``CIF-REF-NUM-CUS`` contact ordinal.

        Note: like the original implementation this asserts the *multiset* of
        values, not their physical row order — a group whose values are the
        right set but shuffled passes. ``step`` must be a non-zero integer.

        Args:
            rule: Must contain ``key_field`` and ``sequence_field`` (str);
                may contain ``start`` and ``step`` (int).
            df: DataFrame to inspect.

        Returns:
            One violation per row in any non-conforming group.

        Raises:
            ValueError: If ``step`` is supplied as ``0`` or a non-integer, or
                ``start`` is a non-integer.
        """
        key_field = rule.get("key_field", "")
        seq_field = rule.get("sequence_field", "")
        missing = [c for c in (key_field, seq_field) if c not in df.columns]
        if missing:
            self._warn_missing(rule, *missing)
            return []

        try:
            start = int(rule.get("start", 1))
            step = int(rule.get("step", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"cross_row rule '{rule.get('id')}' (check=sequential): "
                f"'start'/'step' must be integers, got "
                f"start={rule.get('start')!r}, step={rule.get('step')!r}"
            ) from exc
        if step == 0:
            raise ValueError(
                f"cross_row rule '{rule.get('id')}' (check=sequential): "
                f"'step' must be a non-zero integer"
            )

        description = rule.get("description", rule.get("name", ""))
        violations: List[RuleViolation] = []

        # Work with a numeric copy of the sequence field.
        numeric_seq = pd.to_numeric(df[seq_field], errors="coerce")
        bad_index_labels: set[int] = set()

        for key_val, group in df.groupby(key_field):
            n = len(group)
            seq_vals = numeric_seq.loc[group.index]
            expected = {start + step * i for i in range(n)}
            actual = set(seq_vals.dropna().astype(int))
            if actual != expected:
                bad_index_labels.update(group.index)

        if start == 1 and step == 1:
            run_desc = "sequential"
        else:
            run_desc = f"the run starting {start} step {step}"
        for idx in bad_index_labels:
            seq_val = df.loc[idx, seq_field]
            key_val = df.loc[idx, key_field]
            violations.append(
                self._make_violation(
                    rule,
                    idx,
                    seq_field,
                    seq_val,
                    f"{description}: '{seq_field}' is not {run_desc} "
                    f"within '{key_field}'='{key_val}' group",
                )
            )
        return violations

    def _check_group_count(
        self, rule: dict, df: pd.DataFrame
    ) -> List[RuleViolation]:
        """Return violations where actual row count per key mismatches declared count.

        Each row in a mismatched group is reported as a violation because any
        row in the group carries the incorrect header count.

        Args:
            rule: Must contain ``key_field`` and ``count_field`` (str).
                ``count_field`` holds the declared expected row count.
            df: DataFrame to inspect.

        Returns:
            One violation per row in any mismatched group.
        """
        key_field = rule.get("key_field", "")
        count_field = rule.get("count_field", "")
        missing = [c for c in (key_field, count_field) if c not in df.columns]
        if missing:
            self._warn_missing(rule, *missing)
            return []

        description = rule.get("description", rule.get("name", ""))
        violations: List[RuleViolation] = []

        for key_val, group in df.groupby(key_field):
            actual_count = len(group)
            declared_counts = pd.to_numeric(
                group[count_field], errors="coerce"
            ).dropna()
            if declared_counts.empty:
                continue
            # Use the first non-null declared count in the group.
            declared = int(declared_counts.iloc[0])
            if actual_count != declared:
                for idx in group.index:
                    violations.append(
                        self._make_violation(
                            rule,
                            idx,
                            count_field,
                            df.loc[idx, count_field],
                            f"{description}: expected {declared} rows for "
                            f"'{key_field}'='{key_val}' but found {actual_count}",
                        )
                    )
        return violations

    def _check_group_sum(
        self, rule: dict, df: pd.DataFrame
    ) -> List[RuleViolation]:
        """Return violations where the sum of sum_field per key is out of bounds.

        Both ``min_value`` and ``max_value`` are optional.  If absent the
        corresponding bound is not enforced.

        Args:
            rule: Must contain ``key_field`` and ``sum_field`` (str).
                Optional: ``min_value`` (numeric), ``max_value`` (numeric).
            df: DataFrame to inspect.

        Returns:
            One violation per row in any out-of-bounds group.
        """
        key_field = rule.get("key_field", "")
        sum_field = rule.get("sum_field", "")
        missing = [c for c in (key_field, sum_field) if c not in df.columns]
        if missing:
            self._warn_missing(rule, *missing)
            return []

        min_value = rule.get("min_value")
        max_value = rule.get("max_value")
        description = rule.get("description", rule.get("name", ""))
        violations: List[RuleViolation] = []

        numeric_sum = pd.to_numeric(df[sum_field], errors="coerce")

        for key_val, group in df.groupby(key_field):
            group_sum = numeric_sum.loc[group.index].sum()
            out_of_bounds = False
            if min_value is not None and group_sum < min_value:
                out_of_bounds = True
            if max_value is not None and group_sum > max_value:
                out_of_bounds = True
            if out_of_bounds:
                for idx in group.index:
                    violations.append(
                        self._make_violation(
                            rule,
                            idx,
                            sum_field,
                            numeric_sum.loc[idx],
                            f"{description}: sum of '{sum_field}' for "
                            f"'{key_field}'='{key_val}' is {group_sum} "
                            f"(allowed: {min_value} to {max_value})",
                        )
                    )
        return violations

    # ------------------------------------------------------------------
    # Map-reduce API for chunked validation (issue #358)
    # ------------------------------------------------------------------

    def collect_partial_state(self, rule: dict, df: pd.DataFrame) -> dict:
        """Collect partial cross-row state from one chunk for later merging.

        This is the *map* step of the map-reduce approach used by
        :class:`~src.parsers.chunked_validator.ChunkedFileValidator` to detect
        cross-row violations that span multiple chunks.  Call once per chunk
        and pass all returned dicts to :meth:`merge_partial_states`.

        Args:
            rule: Rule configuration dict with at least ``id`` and ``check``.
            df: The chunk DataFrame (should already be scoped by any ``when``
                condition before calling this method).

        Returns:
            Partial state dict keyed by rule check type.  Structure varies by
            check:

            - ``unique`` / ``unique_composite``:
              ``{"seen": {value_or_tuple_key: [row_idx, ...]}}``
            - ``consistent``:
              ``{"groups": {key: [distinct_target_vals]},
              "rows": {key: [(idx, val)]}}``
            - ``sequential``:
              ``{"groups": {key: [seq_int_vals]},
              "rows": {key: [idx]}}``
            - ``group_count``:
              ``{"counts": {key: int}, "declared": {key: int},
              "rows": {key: [idx]}}``
            - ``group_sum``:
              ``{"sums": {key: float}, "rows": {key: [(idx, val)]}}``

            Returns ``{}`` when the DataFrame is empty or required columns are
            missing.
        """
        check = rule.get("check", "")
        if df.empty:
            return {}

        if check == "unique":
            field = rule.get("field", "")
            if field not in df.columns:
                return {}
            seen: dict = {}
            for idx, val in df[field].items():
                if pd.isna(val):
                    continue
                key = str(val)
                seen.setdefault(key, []).append(int(idx))
            return {"seen": seen}

        if check == "unique_composite":
            fields = rule.get("fields", [])
            if any(f not in df.columns for f in fields):
                return {}
            seen = {}
            for idx, row in df[fields].iterrows():
                key = str(tuple(str(v) for v in row))
                seen.setdefault(key, []).append(int(idx))
            return {"seen": seen}

        if check == "consistent":
            key_field = rule.get("key_field", "")
            target_field = rule.get("target_field", "")
            if key_field not in df.columns or target_field not in df.columns:
                return {}
            groups: dict = {}
            rows: dict = {}
            for idx, row in df[[key_field, target_field]].iterrows():
                k = str(row[key_field])
                v = str(row[target_field]) if not pd.isna(row[target_field]) else None
                if v is not None:
                    groups.setdefault(k, set()).add(v)
                rows.setdefault(k, []).append((int(idx), str(row[target_field])))
            return {"groups": {k: list(v) for k, v in groups.items()}, "rows": rows}

        if check == "sequential":
            key_field = rule.get("key_field", "")
            seq_field = rule.get("sequence_field", "")
            if key_field not in df.columns or seq_field not in df.columns:
                return {}
            groups: dict = {}
            rows: dict = {}
            numeric_seq = pd.to_numeric(df[seq_field], errors="coerce")
            for idx, row in df[[key_field]].iterrows():
                k = str(row[key_field])
                sv = numeric_seq.loc[idx]
                if not pd.isna(sv):
                    groups.setdefault(k, set()).add(int(sv))
                rows.setdefault(k, []).append(int(idx))
            return {"groups": {k: list(v) for k, v in groups.items()}, "rows": rows}

        if check == "group_count":
            key_field = rule.get("key_field", "")
            count_field = rule.get("count_field", "")
            if key_field not in df.columns or count_field not in df.columns:
                return {}
            counts: dict = {}
            declared: dict = {}
            rows: dict = {}
            for idx, row in df[[key_field, count_field]].iterrows():
                k = str(row[key_field])
                counts[k] = counts.get(k, 0) + 1
                rows.setdefault(k, []).append(int(idx))
                dc = pd.to_numeric(
                    pd.Series([row[count_field]]), errors="coerce"
                ).iloc[0]
                if not pd.isna(dc) and k not in declared:
                    declared[k] = int(dc)
            return {"counts": counts, "declared": declared, "rows": rows}

        if check == "group_sum":
            key_field = rule.get("key_field", "")
            sum_field = rule.get("sum_field", "")
            if key_field not in df.columns or sum_field not in df.columns:
                return {}
            sums: dict = {}
            rows: dict = {}
            numeric_sum = pd.to_numeric(df[sum_field], errors="coerce")
            for idx, row in df[[key_field]].iterrows():
                k = str(row[key_field])
                v = numeric_sum.loc[idx]
                if not pd.isna(v):
                    sums[k] = sums.get(k, 0.0) + float(v)
                rows.setdefault(k, []).append(
                    (int(idx), float(v) if not pd.isna(v) else 0.0)
                )
            return {"sums": sums, "rows": rows}

        return {}

    def merge_partial_states(self, rule: dict, states: list) -> dict:
        """Merge partial states from multiple chunks into a single merged state.

        This is the *reduce* step of the map-reduce approach.  Pass the list
        of dicts returned by :meth:`collect_partial_state` for each chunk.

        Args:
            rule: Rule configuration dict (same dict used in
                :meth:`collect_partial_state`).
            states: List of partial state dicts produced by
                :meth:`collect_partial_state`.  Empty dicts are ignored.

        Returns:
            Merged state dict in the same format as a single partial state
            (see :meth:`collect_partial_state` for structure documentation).
            Returns ``{}`` when all input states are empty.
        """
        check = rule.get("check", "")
        non_empty = [s for s in states if s]
        if not non_empty:
            return {}

        if check in {"unique", "unique_composite"}:
            merged_seen: dict = {}
            for state in non_empty:
                for key, idxs in state.get("seen", {}).items():
                    merged_seen.setdefault(key, []).extend(idxs)
            return {"seen": merged_seen}

        if check == "consistent":
            merged_groups: dict = {}
            merged_rows: dict = {}
            for state in non_empty:
                for k, vals in state.get("groups", {}).items():
                    merged_groups.setdefault(k, set()).update(vals)
                for k, row_list in state.get("rows", {}).items():
                    merged_rows.setdefault(k, []).extend(row_list)
            return {
                "groups": {k: list(v) for k, v in merged_groups.items()},
                "rows": merged_rows,
            }

        if check == "sequential":
            merged_groups: dict = {}
            merged_rows: dict = {}
            for state in non_empty:
                for k, vals in state.get("groups", {}).items():
                    merged_groups.setdefault(k, set()).update(vals)
                for k, idxs in state.get("rows", {}).items():
                    merged_rows.setdefault(k, []).extend(idxs)
            return {
                "groups": {k: list(v) for k, v in merged_groups.items()},
                "rows": merged_rows,
            }

        if check == "group_count":
            merged_counts: dict = {}
            merged_declared: dict = {}
            merged_rows: dict = {}
            for state in non_empty:
                for k, c in state.get("counts", {}).items():
                    merged_counts[k] = merged_counts.get(k, 0) + c
                for k, d in state.get("declared", {}).items():
                    if k not in merged_declared:
                        merged_declared[k] = d
                for k, idxs in state.get("rows", {}).items():
                    merged_rows.setdefault(k, []).extend(idxs)
            return {
                "counts": merged_counts,
                "declared": merged_declared,
                "rows": merged_rows,
            }

        if check == "group_sum":
            merged_sums: dict = {}
            merged_rows: dict = {}
            for state in non_empty:
                for k, s in state.get("sums", {}).items():
                    merged_sums[k] = merged_sums.get(k, 0.0) + s
                for k, row_list in state.get("rows", {}).items():
                    merged_rows.setdefault(k, []).extend(row_list)
            return {"sums": merged_sums, "rows": merged_rows}

        return {}

    def evaluate_merged_state(self, rule: dict, merged_state: dict) -> List[RuleViolation]:
        """Evaluate merged cross-chunk state and return violations.

        This is the *evaluate* step after :meth:`merge_partial_states`.  It
        applies the same business-logic checks as the per-chunk handlers but
        operates on the globally merged state, ensuring violations that span
        chunk boundaries are detected.

        Args:
            rule: Rule configuration dict (same dict used throughout the
                map-reduce pipeline).
            merged_state: Merged state dict from :meth:`merge_partial_states`.

        Returns:
            List of :class:`~src.validators.rule_engine.RuleViolation` objects.
            Empty list when no violations are found or ``merged_state`` is empty.
        """
        check = rule.get("check", "")
        description = rule.get("description", rule.get("name", ""))
        violations: List[RuleViolation] = []

        if not merged_state:
            return violations

        if check in {"unique", "unique_composite"}:
            field = rule.get("field", "") or ", ".join(rule.get("fields", []))
            for key, idxs in merged_state.get("seen", {}).items():
                if len(idxs) > 1:
                    for idx in idxs:
                        violations.append(
                            self._make_violation(
                                rule,
                                idx,
                                field,
                                key,
                                f"{description}: duplicate value '{key}' in field '{field}'",
                            )
                        )

        elif check == "consistent":
            key_field = rule.get("key_field", "")
            target_field = rule.get("target_field", "")
            for k, vals in merged_state.get("groups", {}).items():
                if len(vals) > 1:
                    for idx, val in merged_state.get("rows", {}).get(k, []):
                        violations.append(
                            self._make_violation(
                                rule,
                                idx,
                                target_field,
                                val,
                                f"{description}: '{target_field}' is inconsistent "
                                f"within '{key_field}'='{k}' group",
                            )
                        )

        elif check == "sequential":
            key_field = rule.get("key_field", "")
            seq_field = rule.get("sequence_field", "")
            for k, seq_vals in merged_state.get("groups", {}).items():
                n = len(merged_state.get("rows", {}).get(k, []))
                expected = set(range(1, n + 1))
                if set(seq_vals) != expected:
                    for idx in merged_state.get("rows", {}).get(k, []):
                        violations.append(
                            self._make_violation(
                                rule,
                                idx,
                                seq_field,
                                None,
                                f"{description}: '{seq_field}' is not sequential "
                                f"within '{key_field}'='{k}' group",
                            )
                        )

        elif check == "group_count":
            key_field = rule.get("key_field", "")
            count_field = rule.get("count_field", "")
            for k, actual in merged_state.get("counts", {}).items():
                declared = merged_state.get("declared", {}).get(k)
                if declared is not None and actual != declared:
                    for idx in merged_state.get("rows", {}).get(k, []):
                        violations.append(
                            self._make_violation(
                                rule,
                                idx,
                                count_field,
                                actual,
                                f"{description}: expected {declared} rows for "
                                f"'{key_field}'='{k}' but found {actual}",
                            )
                        )

        elif check == "group_sum":
            key_field = rule.get("key_field", "")
            sum_field = rule.get("sum_field", "")
            min_value = rule.get("min_value")
            max_value = rule.get("max_value")
            for k, total in merged_state.get("sums", {}).items():
                out_of_bounds = (
                    min_value is not None and total < min_value
                ) or (
                    max_value is not None and total > max_value
                )
                if out_of_bounds:
                    for idx, val in merged_state.get("rows", {}).get(k, []):
                        violations.append(
                            self._make_violation(
                                rule,
                                idx,
                                sum_field,
                                val,
                                f"{description}: sum of '{sum_field}' for "
                                f"'{key_field}'='{k}' is {total} "
                                f"(allowed: {min_value} to {max_value})",
                            )
                        )

        return violations
