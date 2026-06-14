"""Mapping document parser and processor."""

import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import pandas as pd
import re


@dataclass
class ColumnMapping:
    """Represents a single column mapping."""
    source_column: str
    target_column: str
    data_type: str
    required: bool
    transformations: List[Dict[str, Any]]
    validation_rules: List[Dict[str, Any]]
    format: Optional[str] = None


@dataclass
class MappingDocument:
    """Represents a complete mapping document."""
    mapping_name: str
    version: str
    description: str
    source: Dict[str, Any]
    target: Dict[str, Any]
    mappings: List[ColumnMapping]
    key_columns: List[str]
    metadata: Dict[str, Any]

    def get_column_mapping(self) -> Dict[str, str]:
        """Get simple source->target column mapping.
        
        Returns:
            Dictionary mapping source columns to target columns
        """
        return {m.source_column: m.target_column for m in self.mappings}

    def get_required_columns(self) -> List[str]:
        """Get list of required source columns.
        
        Returns:
            List of required column names
        """
        return [m.source_column for m in self.mappings if m.required]


class MappingParser:
    """Parses and validates mapping documents."""

    def parse(self, mapping_dict: Dict[str, Any]) -> MappingDocument:
        """Parse mapping dictionary into MappingDocument.
        
        Args:
            mapping_dict: Dictionary from JSON mapping file
            
        Returns:
            MappingDocument instance
        """
        # Validate required fields
        self._validate_structure(mapping_dict)

        # Parse column mappings
        column_mappings = [
            ColumnMapping(
                source_column=m['source_column'],
                target_column=m['target_column'],
                data_type=m['data_type'],
                required=m.get('required', False),
                transformations=m.get('transformations', []),
                validation_rules=m.get('validation_rules', []),
                format=m.get('format')
            )
            for m in mapping_dict['mappings']
        ]

        return MappingDocument(
            mapping_name=mapping_dict['mapping_name'],
            version=mapping_dict['version'],
            description=mapping_dict['description'],
            source=mapping_dict['source'],
            target=mapping_dict['target'],
            mappings=column_mappings,
            key_columns=mapping_dict['key_columns'],
            metadata=mapping_dict.get('metadata', {})
        )

    def _validate_structure(self, mapping_dict: Dict[str, Any]) -> None:
        """Validate mapping document structure.
        
        Args:
            mapping_dict: Mapping dictionary to validate
            
        Raises:
            ValueError: If structure is invalid
        """
        required_fields = ['mapping_name', 'version', 'source', 'target', 'mappings', 'key_columns']
        
        for field in required_fields:
            if field not in mapping_dict:
                raise ValueError(f"Missing required field: {field}")

        if not isinstance(mapping_dict['mappings'], list):
            raise ValueError("'mappings' must be a list")

        if not mapping_dict['mappings']:
            raise ValueError("'mappings' cannot be empty")


class MappingProcessor:
    """Processes data according to mapping document."""

    def __init__(self, mapping: MappingDocument):
        """Initialize processor.
        
        Args:
            mapping: MappingDocument instance
        """
        self.mapping = mapping

    def apply_transformations(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply transformations to DataFrame.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Transformed DataFrame
        """
        result_df = df.copy()

        for col_mapping in self.mapping.mappings:
            if col_mapping.source_column not in result_df.columns:
                continue

            col = col_mapping.source_column
            
            for transformation in col_mapping.transformations:
                trans_type = transformation['type']
                params = transformation.get('parameters', {})
                
                result_df[col] = self._apply_transformation(
                    result_df[col], trans_type, params
                )

        return result_df

    def _apply_transformation(self, series: pd.Series, trans_type: str, 
                            params: Dict[str, Any]) -> pd.Series:
        """Apply single transformation to series.
        
        Args:
            series: Pandas Series
            trans_type: Transformation type
            params: Transformation parameters
            
        Returns:
            Transformed Series
        """
        if trans_type == 'trim':
            return series.str.strip()
        elif trans_type == 'upper':
            return series.str.upper()
        elif trans_type == 'lower':
            return series.str.lower()
        elif trans_type == 'substring':
            start = params.get('start', 0)
            length = params.get('length')
            if length:
                return series.str[start:start+length]
            return series.str[start:]
        elif trans_type == 'replace':
            old = params.get('old', '')
            new = params.get('new', '')
            return series.str.replace(old, new, regex=False)
        elif trans_type == 'cast':
            to_type = params.get('to_type', 'string')
            if to_type == 'number':
                return pd.to_numeric(series, errors='coerce')
            elif to_type == 'date':
                return pd.to_datetime(series, errors='coerce')
            return series.astype(str)
        else:
            return series

    def validate_data(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate DataFrame against mapping rules.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Validation results
        """
        errors = []
        warnings = []

        for col_mapping in self.mapping.mappings:
            col = col_mapping.source_column
            
            if col not in df.columns:
                if col_mapping.required:
                    errors.append(f"Required column missing: {col}")
                else:
                    warnings.append(f"Optional column missing: {col}")
                continue

            # Apply validation rules
            for rule in col_mapping.validation_rules:
                rule_type = rule['type']
                params = rule.get('parameters', {})
                
                violations = self._validate_rule(df[col], rule_type, params)
                if violations:
                    errors.append(
                        f"Column '{col}' validation failed ({rule_type}): "
                        f"{violations} violations"
                    )

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
        }

    def _validate_rule(self, series: pd.Series, rule_type: str, 
                      params: Dict[str, Any]) -> int:
        """Validate series against rule.
        
        Args:
            series: Pandas Series
            rule_type: Validation rule type
            params: Rule parameters
            
        Returns:
            Number of violations
        """
        if rule_type == 'not_null':
            return series.isnull().sum()
        elif rule_type == 'min_length':
            min_len = params.get('length', 0)
            return (series.str.len() < min_len).sum()
        elif rule_type == 'max_length':
            max_len = params.get('length', 999999)
            return (series.str.len() > max_len).sum()
        elif rule_type == 'regex':
            pattern = params.get('pattern', '')
            return (~series.str.match(pattern, na=False)).sum()
        elif rule_type == 'range':
            min_val = params.get('min', float('-inf'))
            max_val = params.get('max', float('inf'))
            numeric_series = pd.to_numeric(series, errors='coerce')
            return ((numeric_series < min_val) | (numeric_series > max_val)).sum()
        return 0

    def transform_and_map(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply transformations and rename columns.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Transformed and mapped DataFrame
        """
        # Apply transformations
        transformed_df = self.apply_transformations(df)
        
        # Rename columns
        column_mapping = self.mapping.get_column_mapping()
        mapped_df = transformed_df.rename(columns=column_mapping)
        
        # Select only mapped columns
        target_columns = list(column_mapping.values())
        return mapped_df[target_columns]
