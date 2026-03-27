"""Convert BA-friendly rules template to engine JSON configuration."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

import pandas as pd


class BARulesTemplateConverter:
    """Converts BA-friendly rules CSV/XLSX templates into rule-engine JSON."""

    REQUIRED_COLUMNS = [
        'Rule ID', 'Rule Name', 'Field', 'Rule Type', 'Severity',
        'Expected / Values', 'Enabled'
    ]

    RULE_TYPE_MAP = {
        'required': ('field_validation', 'not_null'),
        'allowed values': ('field_validation', 'in'),
        'range': ('field_validation', 'range'),
        'length': ('field_validation', 'length'),
        'regex': ('field_validation', 'regex'),
        'date format': ('field_validation', 'regex'),
        'compare fields': ('cross_field', None),
        # Valdo native rule types (passthrough — stored as-is in JSON)
        'not_empty': ('field_validation', 'not_empty'),
        'numeric': ('field_validation', 'numeric'),
        'date_format': ('field_validation', 'date_format'),
        'valid_values': ('field_validation', 'valid_values'),
        'min_value': ('field_validation', 'min_value'),
        'max_value': ('field_validation', 'max_value'),
        'exact_length': ('field_validation', 'exact_length'),
        'min_length': ('field_validation', 'min_length'),
        'cross_field': ('cross_field', None),
        'cross_row:unique': ('cross_row', 'unique'),
        'cross_row:unique_composite': ('cross_row', 'unique_composite'),
        'cross_row:consistent': ('cross_row', 'consistent'),
        'cross_row:sequential': ('cross_row', 'sequential'),
        'cross_row:group_count': ('cross_row', 'group_count'),
        'cross_row:group_sum': ('cross_row', 'group_sum'),
    }

    def __init__(self):
        self.rules_config: Dict[str, Any] | None = None

    def from_csv(self, csv_path: str) -> Dict[str, Any]:
        df = pd.read_csv(csv_path)
        return self._convert_dataframe(df, csv_path)

    def from_excel(self, excel_path: str, sheet_name: str | None = None) -> Dict[str, Any]:
        df = pd.read_excel(excel_path, sheet_name=sheet_name or 0)
        return self._convert_dataframe(df, excel_path)

    def _convert_dataframe(self, df: pd.DataFrame, template_path: str) -> Dict[str, Any]:
        df.columns = df.columns.str.strip()

        # Normalize alternate column names to expected names
        col_aliases = {
            'type': 'Rule Type',
            'value': 'Expected / Values',
            'message': 'Message',
            'enabled': 'Enabled',
            'rule id': 'Rule ID',
            'rule name': 'Rule Name',
            'field': 'Field',
            'severity': 'Severity',
            'rule_id': 'Rule ID',
            'rule_name': 'Rule Name',
            'rule_type': 'Rule Type',
            'expected_values': 'Expected / Values',
            'expected / values': 'Expected / Values',
        }
        df.columns = [col_aliases.get(c.lower(), c) for c in df.columns]

        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        rules = []
        for _, row in df.iterrows():
            if pd.isna(row.get('Rule ID')):
                continue
            rules.append(self._convert_row(row))

        self.rules_config = {
            'metadata': {
                'name': Path(template_path).stem,
                'description': f"Generated from BA-friendly template: {Path(template_path).name}",
                'created_by': 'ba_rules_template_converter',
                'created_date': datetime.utcnow().isoformat() + 'Z',
                'template_path': str(template_path),
                'template_type': 'ba_friendly',
            },
            'rules': rules,
        }
        return self.rules_config

    def _convert_row(self, row: pd.Series) -> Dict[str, Any]:
        rule_id = str(row['Rule ID']).strip()
        rule_name = str(row['Rule Name']).strip()
        field = str(row['Field']).strip()
        rule_type_text = str(row['Rule Type']).strip().lower()
        severity = str(row['Severity']).strip().lower()
        expected = '' if pd.isna(row.get('Expected / Values')) else str(row.get('Expected / Values')).strip()
        condition = '' if pd.isna(row.get('Condition (optional)')) else str(row.get('Condition (optional)')).strip()
        notes = '' if pd.isna(row.get('Notes')) else str(row.get('Notes')).strip()
        enabled_text = str(row.get('Enabled', 'Y')).strip().upper()
        enabled = enabled_text in {'Y', 'YES', 'TRUE', '1'}

        if rule_type_text not in self.RULE_TYPE_MAP:
            raise ValueError(f"Unsupported Rule Type '{rule_type_text}' for rule {rule_id}")

        mapped_type, operator = self.RULE_TYPE_MAP[rule_type_text]

        rule: Dict[str, Any] = {
            'id': rule_id,
            'name': rule_name,
            'description': notes or rule_name,
            'type': mapped_type,
            'severity': severity,
            'enabled': enabled,
            'source_template_rule_type': rule_type_text,
        }

        if condition:
            # Stored for future conditional execution support.
            rule['when'] = condition

        if mapped_type == 'cross_field':
            # Expected format: ">= FIELD_NAME"
            parts = expected.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"Rule {rule_id}: Compare Fields expected value must be '<op> <FIELD>'")
            op, right_field = parts[0], parts[1].strip()
            rule.update({
                'operator': op,
                'left_field': field,
                'right_field': right_field,
            })
            return rule

        # field_validation mappings
        rule['field'] = field
        rule['operator'] = operator

        if rule_type_text == 'required':
            return rule

        if rule_type_text == 'allowed values':
            delim = '|' if '|' in expected else ','
            values = [v.strip() for v in expected.split(delim) if v.strip()]
            rule['values'] = values
            return rule

        if rule_type_text in {'range', 'length'}:
            # expected format: min..max
            if '..' not in expected:
                raise ValueError(f"Rule {rule_id}: Expected / Values must be in 'min..max' format")
            left, right = [x.strip() for x in expected.split('..', 1)]
            if rule_type_text == 'range':
                if left:
                    rule['min'] = float(left)
                if right:
                    rule['max'] = float(right)
            else:
                if left:
                    rule['min_length'] = int(left)
                if right:
                    rule['max_length'] = int(right)
            return rule

        if rule_type_text == 'regex':
            rule['pattern'] = expected
            return rule

        if rule_type_text == 'date format':
            # map common date formats to regex
            fmt = expected.upper()
            fmt_regex = {
                'CCYYMMDD': r'^\d{8}$',
                'YYYYMMDD': r'^\d{8}$',
                'MMDDYYYY': r'^\d{8}$',
            }
            rule['pattern'] = fmt_regex.get(fmt, r'^\d+$')
            rule['expected_format'] = fmt
            return rule

        # Valdo native rule types — passthrough with value mapping
        native_types = {
            'not_empty', 'numeric', 'date_format', 'valid_values',
            'min_value', 'max_value', 'exact_length', 'min_length',
        }
        if rule_type_text in native_types:
            if expected:
                if rule_type_text == 'valid_values':
                    delim = '|' if '|' in expected else ','
                    rule['values'] = [v.strip() for v in expected.split(delim) if v.strip()]
                elif rule_type_text in ('min_value', 'max_value', 'exact_length', 'min_length'):
                    try:
                        rule['value'] = float(expected) if '.' in expected else int(expected)
                    except ValueError:
                        rule['value'] = expected
                elif rule_type_text == 'date_format':
                    rule['format'] = expected
                else:
                    rule['value'] = expected
            if 'Message' in row.index and pd.notna(row.get('Message')):
                rule['message'] = str(row['Message']).strip()
            return rule

        # Cross-row rule types
        if rule_type_text.startswith('cross_row:'):
            check = rule_type_text.split(':', 1)[1]
            rule['check'] = check
            # Parse field syntax: KEY>TARGET or FIELD1|FIELD2
            if '>' in field:
                parts = field.split('>', 1)
                rule['key_field'] = parts[0].strip()
                rule['target_field'] = parts[1].strip()
            elif '|' in field:
                rule['fields'] = [f.strip() for f in field.split('|')]
            else:
                rule['field'] = field
            if expected:
                try:
                    rule['value'] = float(expected) if '.' in expected else int(expected)
                except ValueError:
                    rule['value'] = expected
            if 'Message' in row.index and pd.notna(row.get('Message')):
                rule['message'] = str(row['Message']).strip()
            return rule

        return rule

    def save(self, output_path: str):
        if not self.rules_config:
            raise ValueError('No rules configuration to save. Run from_csv() or from_excel() first.')

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(self.rules_config, f, indent=2)
