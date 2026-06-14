"""Cross-field validation for business rules."""

import pandas as pd
from typing import Any


class CrossFieldValidator:
    """Validate relationships between fields."""
    
    def validate_field_comparison(self, df: pd.DataFrame, left_field: str, 
                                  operator: str, right_field: str) -> pd.Series:
        """
        Compare two fields.
        
        Args:
            df: DataFrame
            left_field: Left field name
            operator: Comparison operator (>, <, >=, <=, ==, !=)
            right_field: Right field name
            
        Returns:
            Boolean mask where True indicates violation
        """
        left = df[left_field]
        right = df[right_field]
        
        # Try numeric comparison first
        try:
            left_num = pd.to_numeric(left, errors='coerce')
            right_num = pd.to_numeric(right, errors='coerce')
            
            # If both can be converted to numeric, use numeric comparison
            if left_num.notna().any() and right_num.notna().any():
                left = left_num
                right = right_num
        except:
            pass
        
        # Try datetime comparison for date fields
        try:
            if 'date' in left_field.lower() or 'date' in right_field.lower():
                left_dt = pd.to_datetime(left, errors='coerce')
                right_dt = pd.to_datetime(right, errors='coerce')
                
                if left_dt.notna().any() and right_dt.notna().any():
                    left = left_dt
                    right = right_dt
        except:
            pass
        
        # Perform comparison
        if operator == '>':
            return ~(left > right)
        elif operator == '<':
            return ~(left < right)
        elif operator == '>=':
            return ~(left >= right)
        elif operator == '<=':
            return ~(left <= right)
        elif operator == '==':
            return ~(left == right)
        elif operator == '!=':
            return ~(left != right)
        else:
            raise ValueError(f"Unknown comparison operator: {operator}")
    
    def validate_depends_on(self, df: pd.DataFrame, field: str, 
                           depends_on_field: str) -> pd.Series:
        """
        Validate that if depends_on_field has value, field must also have value.
        
        Args:
            df: DataFrame
            field: Field that depends on another
            depends_on_field: Field that triggers requirement
            
        Returns:
            Boolean mask where True indicates violation
        """
        # If depends_on_field has value, field must have value
        depends_has_value = df[depends_on_field].notna() & (df[depends_on_field].astype(str).str.strip() != '')
        field_has_value = df[field].notna() & (df[field].astype(str).str.strip() != '')
        
        # Violation: depends_on has value but field doesn't
        return depends_has_value & ~field_has_value
    
    def validate_mutually_exclusive(self, df: pd.DataFrame, 
                                   fields: list) -> pd.Series:
        """
        Validate that only one field in the list can have a value.
        
        Args:
            df: DataFrame
            fields: List of field names
            
        Returns:
            Boolean mask where True indicates violation
        """
        # Count how many fields have values
        has_values = pd.Series([False] * len(df), index=df.index)
        
        for field in fields:
            if field in df.columns:
                field_has_value = df[field].notna() & (df[field].astype(str).str.strip() != '')
                has_values = has_values.astype(int) + field_has_value.astype(int)
        
        # Violation if more than one field has value
        return has_values > 1
