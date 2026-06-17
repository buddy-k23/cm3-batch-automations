"""Business rule validation engine."""

import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
import pandas as pd
import re
from datetime import datetime

_logger = logging.getLogger(__name__)


@dataclass
class RuleViolation:
    """Represents a single rule violation."""
    rule_id: str
    rule_name: str
    severity: str  # error, warning, info
    row_number: int
    field: str
    value: Any
    message: str
    expected: Optional[Any] = None
    issue_code: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class RuleEngine:
    """Execute business rules against DataFrame."""
    
    def __init__(self, rules_config: Dict):
        """
        Initialize rule engine with configuration.
        
        Args:
            rules_config: Dictionary containing rules configuration
        """
        self.rules_config = rules_config
        self.rules = rules_config.get('rules', [])
        self.enabled_rules = [r for r in self.rules if r.get('enabled', True)]
        self.violations = []
        self.statistics = {
            'total_rules': len(self.rules),
            'enabled_rules': len(self.enabled_rules),
            'executed_rules': 0,
            'total_violations': 0,
            'violations_by_severity': {'error': 0, 'warning': 0, 'info': 0},
            'violations_by_rule': {}
        }
    
    def validate(self, df: pd.DataFrame) -> List[RuleViolation]:
        """
        Execute all enabled rules and return violations.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            List of RuleViolation objects
        """
        self.violations = []
        
        for rule in self.enabled_rules:
            try:
                rule_violations = self._execute_rule(rule, df)
                self.violations.extend(rule_violations)
                
                # Update statistics
                self.statistics['executed_rules'] += 1
                violation_count = len(rule_violations)
                self.statistics['violations_by_rule'][rule['id']] = violation_count
                
            except Exception as e:
                # Log error but continue with other rules
                print(f"Error executing rule {rule.get('id', 'unknown')}: {e}")
        
        # Update total statistics
        self.statistics['total_violations'] = len(self.violations)
        for violation in self.violations:
            severity = violation.severity
            self.statistics['violations_by_severity'][severity] = \
                self.statistics['violations_by_severity'].get(severity, 0) + 1
        
        return self.violations
    
    def _apply_condition(self, rule: Dict, df: pd.DataFrame) -> pd.DataFrame:
        """Apply optional `when` condition and return filtered DataFrame.

        Supported formats:
        - "field = VALUE"
        - "field != VALUE"
        - "field > VALUE", "field >= VALUE", "field < VALUE", "field <= VALUE"
        - "field in (A,B,C)"
        """
        condition = rule.get('when')
        if not condition:
            return df

        cond = condition.strip()

        m_in = re.match(r'^([A-Za-z0-9_\-]+)\s+in\s*\((.+)\)$', cond, flags=re.IGNORECASE)
        if m_in:
            field = m_in.group(1)
            raw_vals = m_in.group(2)
            values = [v.strip().strip('"\'') for v in raw_vals.split(',') if v.strip()]
            if field not in df.columns:
                return df.iloc[0:0]
            return df[df[field].astype(str).isin(values)]

        m_cmp = re.match(r'^([A-Za-z0-9_\-]+)\s*(=|!=|>=|<=|>|<)\s*(.+)$', cond)
        if not m_cmp:
            return df

        field, op, rhs = m_cmp.group(1), m_cmp.group(2), m_cmp.group(3).strip()
        rhs = rhs.strip('"\'')

        if field not in df.columns:
            return df.iloc[0:0]

        series = df[field]

        # Try numeric compare first for numeric ops
        if op in {'>', '>=', '<', '<='}:
            left = pd.to_numeric(series, errors='coerce')
            right = pd.to_numeric(pd.Series([rhs]), errors='coerce').iloc[0]
            if pd.isna(right):
                return df.iloc[0:0]
            if op == '>':
                return df[left > right]
            if op == '>=':
                return df[left >= right]
            if op == '<':
                return df[left < right]
            return df[left <= right]

        # string equality/inequality
        left_str = series.astype(str)
        if op == '=':
            return df[left_str == rhs]
        if op == '!=':
            return df[left_str != rhs]

        return df

    def _execute_rule(self, rule: Dict, df: pd.DataFrame) -> List[RuleViolation]:
        """Execute a single rule."""
        rule_type = rule.get('type')
        scoped_df = self._apply_condition(rule, df)

        if scoped_df.empty:
            return []

        if rule_type == 'field_validation':
            return self._validate_field(rule, scoped_df)
        elif rule_type == 'cross_field':
            return self._validate_cross_field(rule, scoped_df)
        elif rule_type == 'cross_row':
            return self._validate_cross_row(rule, scoped_df)
        else:
            raise ValueError(f"Unknown rule type: {rule_type}")
    
    def _build_issue_code(self, rule: Dict, category: str) -> str:
        rid = str(rule.get('id', 'UNKNOWN')).upper().replace(' ', '_')
        cat = category.upper().replace(' ', '_')
        return f"BR_{rid}_{cat}"

    def _validate_field(self, rule: Dict, df: pd.DataFrame) -> List[RuleViolation]:
        """
        Validate individual field values.
        
        Args:
            rule: Rule configuration
            df: DataFrame to validate
            
        Returns:
            List of violations
        """
        from src.validators.field_validator import FieldValidator
        
        validator = FieldValidator()
        field = rule['field']
        operator = rule.get('operator')
        
        # Check if field exists
        if field not in df.columns:
            return []
        
        # Get violation mask based on operator
        if operator in ['>', '<', '>=', '<=', '==', '!=']:
            value = rule.get('value')
            mask = validator.validate_numeric(df, field, operator, value)
        elif operator in ['in', 'not_in']:
            values = rule.get('values', [])
            if isinstance(values, str):
                values = [v.strip() for v in values.split(',')]
            mask = validator.validate_list(df, field, operator, values)
        elif operator == 'regex':
            pattern = rule.get('pattern')
            mask = validator.validate_regex(df, field, pattern)
        elif operator == 'range':
            min_val = rule.get('min')
            max_val = rule.get('max')
            mask = validator.validate_range(df, field, min_val, max_val)
        elif operator == 'not_null':
            mask = validator.validate_not_null(df, field)
        elif operator == 'length':
            min_len = rule.get('min_length')
            max_len = rule.get('max_length')
            mask = validator.validate_length(df, field, min_len, max_len)
        # ----- ADR 0006 / issue #13 — BA-friendly "native" operators -----
        # BARulesTemplateConverter.RULE_TYPE_MAP emits these operator names
        # for the rule types documented in prompts/generate-rules-csv.md.
        # Before this branch existed every BA-friendly JSON in the repo
        # crashed every rule with "Unknown operator: <name>". The legacy
        # operator branches above are unchanged.
        elif operator == 'not_empty':
            mask = validator.validate_not_empty(df, field)
        elif operator == 'numeric':
            mask = validator.validate_numeric_format(df, field)
        elif operator == 'date_format':
            fmt = rule.get('format', '')
            mask = validator.validate_date_format(df, field, fmt)
        elif operator == 'valid_values':
            values = rule.get('values', [])
            if isinstance(values, str):
                values = [v.strip() for v in values.split(',')]
            mask = validator.validate_valid_values(df, field, values)
        elif operator == 'min_value':
            mask = validator.validate_min_value(df, field, rule.get('value'))
        elif operator == 'max_value':
            mask = validator.validate_max_value(df, field, rule.get('value'))
        elif operator == 'exact_length':
            mask = validator.validate_exact_length(df, field, int(rule.get('value', 0)))
        elif operator == 'min_length':
            mask = validator.validate_min_length(df, field, int(rule.get('value', 0)))
        # ----- ADR 0018 / issue #395 — JSON (NDJSON) operators -----
        # JsonParser flattens NDJSON records; ``json_array_length`` runs
        # against a ``<field>_count`` column and ``nested_required`` flags an
        # absent (pd.NA) JSON path. See FieldValidator for the predicates.
        elif operator == 'json_array_length':
            min_len = int(rule.get('min_len', 0))
            max_len = rule.get('max_len')
            max_len = int(max_len) if max_len is not None else None
            mask = validator.validate_json_array_length(df, field, min_len, max_len)
        # ----- ADR 0019 / issue #396 — XML operator -----
        # XmlParser flattens repeated record elements; ``xml_array_length`` runs
        # against a ``<field>_count`` column. ``nested_required`` (above) is
        # reused unchanged for absent XML paths. See FieldValidator.
        elif operator == 'xml_array_length':
            min_len = int(rule.get('min_len', 0))
            max_len = rule.get('max_len')
            max_len = int(max_len) if max_len is not None else None
            mask = validator.validate_xml_array_length(df, field, min_len, max_len)
        elif operator == 'nested_required':
            mask = validator.validate_nested_required(df, field)
        else:
            raise ValueError(f"Unknown operator: {operator}")
        
        # Create violations for rows that failed validation
        violations = []
        for idx in df[mask].index:
            violations.append(RuleViolation(
                rule_id=rule['id'],
                rule_name=rule['name'],
                severity=rule.get('severity', 'error'),
                row_number=int(idx) + 1,  # 1-indexed for user display
                field=field,
                value=df.loc[idx, field],
                message=self._format_message(rule, df.loc[idx, field]),
                expected=rule.get('value') or rule.get('values') or rule.get('pattern'),
                issue_code=self._build_issue_code(rule, 'FIELD')
            ))
        
        return violations
    
    def _validate_cross_row(self, rule: Dict, df: pd.DataFrame) -> List[RuleViolation]:
        """Delegate cross-row validation to CrossRowValidator.

        Args:
            rule: Rule configuration dict.  Must include a ``check`` key that
                names one of the supported cross-row check types.
            df: DataFrame filtered by any ``when`` condition.

        Returns:
            List of :class:`RuleViolation` objects produced by
            :class:`~src.validators.cross_row_validator.CrossRowValidator`.
        """
        from src.validators.cross_row_validator import CrossRowValidator
        validator = CrossRowValidator()
        return validator.validate(rule, df)

    def _validate_cross_field(self, rule: Dict, df: pd.DataFrame) -> List[RuleViolation]:
        """
        Validate relationships between fields.
        
        Args:
            rule: Rule configuration
            df: DataFrame to validate
            
        Returns:
            List of violations
        """
        from src.validators.cross_field_validator import CrossFieldValidator
        
        validator = CrossFieldValidator()
        operator = rule.get('operator')
        left_field = rule.get('left_field')
        right_field = rule.get('right_field')
        
        # Check if fields exist
        if left_field not in df.columns or right_field not in df.columns:
            return []
        
        # Get violation mask
        mask = validator.validate_field_comparison(df, left_field, operator, right_field)
        
        # Create violations
        violations = []
        for idx in df[mask].index:
            violations.append(RuleViolation(
                rule_id=rule['id'],
                rule_name=rule['name'],
                severity=rule.get('severity', 'error'),
                row_number=int(idx) + 1,
                field=f"{left_field}, {right_field}",
                value=f"{df.loc[idx, left_field]} vs {df.loc[idx, right_field]}",
                message=self._format_cross_field_message(rule, df.loc[idx, left_field], 
                                                         df.loc[idx, right_field]),
                expected=f"{left_field} {operator} {right_field}",
                issue_code=self._build_issue_code(rule, 'CROSS')
            ))
        
        return violations
    
    def _format_message(self, rule: Dict, value: Any) -> str:
        """Format violation message."""
        description = rule.get('description', rule.get('name'))
        return f"{description} (got: {value})"
    
    def _format_cross_field_message(self, rule: Dict, left_value: Any, 
                                    right_value: Any) -> str:
        """Format cross-field violation message."""
        description = rule.get('description', rule.get('name'))
        return f"{description} (got: {left_value} vs {right_value})"
    
    def get_statistics(self) -> Dict:
        """
        Get rule execution statistics.
        
        Returns:
            Dictionary with statistics
        """
        # Calculate compliance rate
        total_rows = self.statistics.get('total_rows', 0)
        if total_rows > 0:
            affected_rows = len(set(v.row_number for v in self.violations))
            compliance_rate = ((total_rows - affected_rows) / total_rows) * 100
        else:
            compliance_rate = 100.0
        
        return {
            **self.statistics,
            'compliance_rate': round(compliance_rate, 2)
        }
    
    def set_total_rows(self, total_rows: int):
        """Set total rows for compliance calculation."""
        self.statistics['total_rows'] = total_rows

    @property
    def cross_row_rules(self) -> List[Dict]:
        """Return enabled rules with type == 'cross_row'.

        Used by the chunked validator to separate cross-row rules (handled via
        map-reduce across chunks) from per-chunk field/cross-field rules.

        Returns:
            List of enabled rule dicts whose ``type`` is ``'cross_row'``.
        """
        return [r for r in self.enabled_rules if r.get("type") == "cross_row"]

    def validate_non_cross_row(self, df: pd.DataFrame) -> List[RuleViolation]:
        """Execute all enabled rules EXCEPT cross_row type rules.

        Used by the chunked validator to run field-level and cross-field rules
        per chunk, while cross-row rules are handled separately via map-reduce
        to correctly detect violations that span chunk boundaries.

        Args:
            df: DataFrame chunk to validate.

        Returns:
            List of :class:`RuleViolation` objects from field and cross-field
            rules only.  Cross-row rules are excluded entirely.
        """
        violations: List[RuleViolation] = []
        for rule in self.enabled_rules:
            if rule.get("type") == "cross_row":
                continue
            try:
                violations.extend(self._execute_rule(rule, df))
            except Exception as exc:
                _logger.warning(
                    "Error executing rule %s: %s", rule.get("id"), exc
                )
        return violations
