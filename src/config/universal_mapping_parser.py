"""Universal mapping parser that works with all file formats."""

import json
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass


@dataclass
class FieldSpec:
    """Field specification from mapping."""
    name: str
    source_name: str
    target_name: str
    position: Optional[int]
    length: Optional[int]
    data_type: str
    format: Optional[str]
    required: bool
    default_value: Any
    transformations: List[Dict]
    validation_rules: List[Dict]
    description: Optional[str]


class UniversalMappingParser:
    """Parse universal mapping structure for any file format."""
    
    def __init__(self, mapping_path: str = None, mapping_dict: dict = None):
        """
        Initialize parser with mapping file or dictionary.
        
        Args:
            mapping_path: Path to mapping JSON file
            mapping_dict: Mapping dictionary (if not loading from file)
        """
        if mapping_path:
            with open(mapping_path, 'r') as f:
                self.mapping = json.load(f)
        elif mapping_dict:
            self.mapping = mapping_dict
        else:
            raise ValueError("Either mapping_path or mapping_dict must be provided")
        
        self.fields = self._parse_fields()
    
    def _parse_fields(self) -> List[FieldSpec]:
        """Parse fields from mapping into FieldSpec objects."""
        fields = []
        
        for field_dict in self.mapping.get('fields', []):
            field = FieldSpec(
                name=field_dict['name'],
                source_name=field_dict.get('source_name', field_dict['name']),
                target_name=field_dict.get('target_name', field_dict['name']),
                position=field_dict.get('position'),
                length=field_dict.get('length'),
                data_type=field_dict['data_type'],
                format=field_dict.get('format'),
                required=field_dict.get('required', False),
                default_value=field_dict.get('default_value'),
                transformations=field_dict.get('transformations', []),
                validation_rules=field_dict.get('validation_rules', []),
                description=field_dict.get('description')
            )
            fields.append(field)
        
        return fields
    
    def get_format(self) -> str:
        """Get source file format."""
        return self.mapping['source']['format']
    
    def get_delimiter(self) -> str:
        """Get delimiter for delimited formats."""
        return self.mapping['source'].get('delimiter', '|')
    
    def has_header(self) -> bool:
        """Check if file has header row."""
        return self.mapping['source'].get('has_header', False)
    
    def get_field_positions(self) -> List[Tuple[str, int, int]]:
        """
        Get field positions for fixed-width parsing.
        
        Returns:
            List of (field_name, start_pos, end_pos) tuples
        """
        if self.get_format() != 'fixed_width':
            raise ValueError("Field positions only available for fixed-width format")
        
        positions = []
        current_pos = 0
        
        for field in self.fields:
            if field.position is None or field.length is None:
                raise ValueError(f"Field {field.name} missing position or length")
            
            start = current_pos
            end = current_pos + field.length
            positions.append((field.name, start, end))
            current_pos = end
        
        return positions
    
    def get_column_names(self) -> List[str]:
        """
        Get column names for delimited file parsing.
        
        Returns:
            List of column names
        """
        return [field.source_name for field in self.fields]
    
    def get_target_column_names(self) -> List[str]:
        """
        Get target column names (for database mapping).
        
        Returns:
            List of target column names
        """
        return [field.target_name for field in self.fields]
    
    def get_column_mapping(self) -> Dict[str, str]:
        """
        Get source-to-target column mapping.
        
        Returns:
            Dictionary mapping source names to target names
        """
        return {field.source_name: field.target_name for field in self.fields}
    
    def get_required_fields(self) -> List[str]:
        """Get list of required field names."""
        return [field.name for field in self.fields if field.required]
    
    def get_key_columns(self) -> List[str]:
        """Get key columns for row identification."""
        return self.mapping.get('key_columns', [])
    
    def get_field_spec(self, field_name: str) -> Optional[FieldSpec]:
        """Get field specification by name."""
        for field in self.fields:
            if field.name == field_name:
                return field
        return None
    
    def get_transformations(self, field_name: str) -> List[Dict]:
        """Get transformations for a specific field."""
        field = self.get_field_spec(field_name)
        return field.transformations if field else []
    
    def get_validations(self, field_name: str) -> List[Dict]:
        """Get validation rules for a specific field."""
        field = self.get_field_spec(field_name)
        return field.validation_rules if field else []
    
    def get_total_record_length(self) -> int:
        """Get total record length for fixed-width format."""
        if self.get_format() != 'fixed_width':
            raise ValueError("Record length only available for fixed-width format")
        
        return sum(field.length for field in self.fields if field.length)
    
    def validate_schema(self) -> Dict[str, Any]:
        """
        Validate mapping against schema requirements.
        
        Returns:
            Dictionary with validation results
        """
        errors = []
        warnings = []
        
        # Check required top-level fields
        required_fields = ['mapping_name', 'version', 'source', 'fields']
        for field in required_fields:
            if field not in self.mapping:
                errors.append(f"Missing required field: {field}")
        
        # Validate source format
        format_type = self.get_format()
        if format_type == 'fixed_width':
            # Check all fields have position and length
            for field in self.fields:
                if field.position is None:
                    errors.append(f"Field {field.name} missing position for fixed-width format")
                if field.length is None:
                    errors.append(f"Field {field.name} missing length for fixed-width format")
        
        # Check for duplicate field names
        field_names = [f.name for f in self.fields]
        duplicates = set([name for name in field_names if field_names.count(name) > 1])
        if duplicates:
            errors.append(f"Duplicate field names: {duplicates}")
        
        # Check key columns exist
        key_cols = self.get_key_columns()
        for key_col in key_cols:
            if key_col not in field_names:
                errors.append(f"Key column '{key_col}' not found in fields")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def to_dict(self) -> dict:
        """Return mapping as dictionary."""
        return self.mapping
    
    def save(self, output_path: str):
        """Save mapping to file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.mapping, f, indent=2)
    
    def __repr__(self):
        """String representation."""
        return (f"UniversalMappingParser(name={self.mapping.get('mapping_name')}, "
                f"format={self.get_format()}, fields={len(self.fields)})")


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) > 1:
        mapping_path = sys.argv[1]
        parser = UniversalMappingParser(mapping_path=mapping_path)
        
        print(f"Mapping: {parser.mapping.get('mapping_name')}")
        print(f"Format: {parser.get_format()}")
        print(f"Fields: {len(parser.fields)}")
        print(f"Key columns: {parser.get_key_columns()}")
        
        # Validate
        validation = parser.validate_schema()
        if validation['valid']:
            print("✓ Mapping is valid")
        else:
            print("✗ Mapping validation failed:")
            for error in validation['errors']:
                print(f"  - {error}")
        
        # Show field details
        if parser.get_format() == 'fixed_width':
            print(f"\nTotal record length: {parser.get_total_record_length()} characters")
            print("\nField positions:")
            for name, start, end in parser.get_field_positions():
                print(f"  {name}: {start}-{end} ({end-start} chars)")
        else:
            print(f"\nDelimiter: '{parser.get_delimiter()}'")
            print("\nColumn names:")
            for name in parser.get_column_names():
                print(f"  - {name}")
    else:
        print("Usage: python universal_mapping_parser.py <mapping_file.json>")
