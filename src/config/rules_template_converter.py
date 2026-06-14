"""Convert Excel/CSV templates to business rules JSON configuration."""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List


class RulesTemplateConverter:
    """Convert Excel/CSV templates to business rules JSON."""
    
    # Expected column names in template
    REQUIRED_COLUMNS = ['Rule ID', 'Rule Name', 'Description', 'Type', 'Severity', 'Operator']
    OPTIONAL_COLUMNS = [
        'Field', 'Value', 'Values', 'Pattern', 'Min', 'Max',
        'Left Field', 'Right Field', 'Enabled', 'Min Length', 'Max Length'
    ]
    
    # Valid rule types
    VALID_TYPES = ['field_validation', 'cross_field']
    
    # Valid severities
    VALID_SEVERITIES = ['error', 'warning', 'info']
    
    # Valid operators
    VALID_OPERATORS = [
        '>', '<', '>=', '<=', '==', '!=',  # Numeric
        'in', 'not_in',  # List
        'regex',  # Pattern
        'range',  # Range
        'not_null',  # Required
        'length'  # String length
    ]
    
    def __init__(self):
        """Initialize converter."""
        self.rules_config = None
    
    def from_excel(self, excel_path: str, sheet_name: str = None) -> dict:
        """
        Convert Excel template to rules JSON.
        
        Args:
            excel_path: Path to Excel file
            sheet_name: Sheet name (uses first sheet if not specified)
            
        Returns:
            Rules configuration dictionary
        """
        # Read Excel file
        if sheet_name:
            df = pd.read_excel(excel_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(excel_path, sheet_name=0)
        
        return self._convert_dataframe(df, excel_path)
    
    def from_csv(self, csv_path: str) -> dict:
        """
        Convert CSV template to rules JSON.
        
        Args:
            csv_path: Path to CSV file
            
        Returns:
            Rules configuration dictionary
        """
        df = pd.read_csv(csv_path)
        return self._convert_dataframe(df, csv_path)
    
    def _convert_dataframe(self, df: pd.DataFrame, template_path: str) -> dict:
        """Convert DataFrame to rules configuration."""
        
        # Clean column names
        df.columns = df.columns.str.strip()
        
        # Validate required columns
        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        # Build rules configuration
        self.rules_config = {
            "metadata": {
                "name": Path(template_path).stem,
                "description": f"Generated from template: {Path(template_path).name}",
                "created_by": "rules_template_converter",
                "created_date": datetime.utcnow().isoformat() + "Z",
                "template_path": str(template_path)
            },
            "rules": []
        }
        
        # Convert each row to a rule
        for idx, row in df.iterrows():
            try:
                rule = self._convert_row_to_rule(row, idx)
                if rule:
                    self.rules_config['rules'].append(rule)
            except Exception as e:
                print(f"Warning: Skipping row {idx + 1}: {e}")
        
        return self.rules_config
    
    def _convert_row_to_rule(self, row: pd.Series, row_idx: int) -> Optional[Dict]:
        """Convert a single row to a rule."""
        
        # Skip empty rows
        if pd.isna(row['Rule ID']):
            return None
        
        # Basic rule structure
        rule = {
            "id": str(row['Rule ID']).strip(),
            "name": str(row['Rule Name']).strip(),
            "description": str(row['Description']).strip(),
            "type": str(row['Type']).strip().lower(),
            "severity": str(row['Severity']).strip().lower(),
            "operator": str(row['Operator']).strip()
        }
        
        # Validate type
        if rule['type'] not in self.VALID_TYPES:
            raise ValueError(f"Invalid type '{rule['type']}'. Must be one of: {self.VALID_TYPES}")
        
        # Validate severity
        if rule['severity'] not in self.VALID_SEVERITIES:
            raise ValueError(f"Invalid severity '{rule['severity']}'. Must be one of: {self.VALID_SEVERITIES}")
        
        # Validate operator
        if rule['operator'] not in self.VALID_OPERATORS:
            raise ValueError(f"Invalid operator '{rule['operator']}'. Must be one of: {self.VALID_OPERATORS}")
        
        # Add type-specific fields
        if rule['type'] == 'field_validation':
            rule = self._add_field_validation_params(rule, row)
        elif rule['type'] == 'cross_field':
            rule = self._add_cross_field_params(rule, row)
        
        # Add enabled flag (default True)
        if 'Enabled' in row.index and not pd.isna(row['Enabled']):
            enabled_str = str(row['Enabled']).strip().upper()
            rule['enabled'] = enabled_str in ['TRUE', 'YES', '1', 'Y']
        else:
            rule['enabled'] = True
        
        return rule
    
    def _add_field_validation_params(self, rule: Dict, row: pd.Series) -> Dict:
        """Add parameters for field validation rules."""
        
        # Field is required for field validation
        if 'Field' not in row.index or pd.isna(row['Field']):
            raise ValueError("Field is required for field_validation rules")
        
        rule['field'] = str(row['Field']).strip()
        
        # Add operator-specific parameters
        operator = rule['operator']
        
        if operator in ['>', '<', '>=', '<=', '==', '!=']:
            # Numeric comparison - need Value
            if 'Value' in row.index and not pd.isna(row['Value']):
                rule['value'] = self._parse_value(row['Value'])
            else:
                raise ValueError(f"Value is required for operator '{operator}'")
        
        elif operator in ['in', 'not_in']:
            # List membership - need Values
            if 'Values' in row.index and not pd.isna(row['Values']):
                values_str = str(row['Values']).strip()
                rule['values'] = [v.strip() for v in values_str.split(',')]
            else:
                raise ValueError(f"Values is required for operator '{operator}'")
        
        elif operator == 'regex':
            # Pattern matching - need Pattern
            if 'Pattern' in row.index and not pd.isna(row['Pattern']):
                rule['pattern'] = str(row['Pattern']).strip()
            else:
                raise ValueError(f"Pattern is required for operator '{operator}'")
        
        elif operator == 'range':
            # Range validation - need Min and Max
            if 'Min' in row.index and not pd.isna(row['Min']):
                rule['min'] = self._parse_value(row['Min'])
            if 'Max' in row.index and not pd.isna(row['Max']):
                rule['max'] = self._parse_value(row['Max'])
            
            if 'min' not in rule and 'max' not in rule:
                raise ValueError("At least one of Min or Max is required for range operator")
        
        elif operator == 'length':
            # String length - need Min Length and/or Max Length
            if 'Min Length' in row.index and not pd.isna(row['Min Length']):
                rule['min_length'] = int(row['Min Length'])
            if 'Max Length' in row.index and not pd.isna(row['Max Length']):
                rule['max_length'] = int(row['Max Length'])
            
            if 'min_length' not in rule and 'max_length' not in rule:
                raise ValueError("At least one of Min Length or Max Length is required for length operator")
        
        # not_null doesn't need additional parameters
        
        return rule
    
    def _add_cross_field_params(self, rule: Dict, row: pd.Series) -> Dict:
        """Add parameters for cross-field validation rules."""
        
        # Left Field and Right Field are required
        if 'Left Field' not in row.index or pd.isna(row['Left Field']):
            raise ValueError("Left Field is required for cross_field rules")
        if 'Right Field' not in row.index or pd.isna(row['Right Field']):
            raise ValueError("Right Field is required for cross_field rules")
        
        rule['left_field'] = str(row['Left Field']).strip()
        rule['right_field'] = str(row['Right Field']).strip()
        
        return rule
    
    def _parse_value(self, value: Any) -> Any:
        """Parse value to appropriate type."""
        if pd.isna(value):
            return None
        
        # Try to convert to number
        try:
            if '.' in str(value):
                return float(value)
            else:
                return int(value)
        except (ValueError, TypeError):
            # Return as string
            return str(value).strip()
    
    def validate_template(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Validate template structure and content.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Validation result dictionary
        """
        errors = []
        warnings = []
        
        # Check required columns
        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            errors.append(f"Missing required columns: {missing_cols}")
        
        # Check for duplicate rule IDs
        if 'Rule ID' in df.columns:
            rule_ids = df['Rule ID'].dropna()
            duplicates = rule_ids[rule_ids.duplicated()].tolist()
            if duplicates:
                errors.append(f"Duplicate Rule IDs found: {duplicates}")
        
        # Check for empty required fields
        for col in self.REQUIRED_COLUMNS:
            if col in df.columns:
                empty_count = df[col].isna().sum()
                if empty_count > 0:
                    warnings.append(f"Column '{col}' has {empty_count} empty values")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def save(self, output_path: str):
        """
        Save rules configuration to JSON file.
        
        Args:
            output_path: Path to output JSON file
        """
        if not self.rules_config:
            raise ValueError("No rules configuration to save. Run from_excel() or from_csv() first.")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.rules_config, f, indent=2)
        
        print(f"Rules configuration saved to: {output_path}")
        print(f"Total rules: {len(self.rules_config['rules'])}")
