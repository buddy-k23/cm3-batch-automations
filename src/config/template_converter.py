"""Convert Excel/CSV templates to universal mapping format."""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class TemplateConverter:
    """Convert Excel/CSV templates to universal mapping JSON."""
    
    # Expected column names in template
    REQUIRED_COLUMNS = ['Field Name', 'Data Type']
    OPTIONAL_COLUMNS = [
        'Position', 'Length', 'Format', 'Required',
        'Description', 'Default Value', 'Target Name', 'Valid Values'
    ]
    
    def __init__(self):
        """Initialize converter."""
        self.mapping = None
    
    def from_excel(self, excel_path: str, sheet_name: str = None, 
                   mapping_name: str = None, file_format: str = None) -> dict:
        """
        Convert Excel template to universal mapping.
        
        Args:
            excel_path: Path to Excel file
            sheet_name: Sheet name (uses first sheet if not specified)
            mapping_name: Name for the mapping (derived from filename if not specified)
            file_format: File format (auto-detected if not specified)
            
        Returns:
            Universal mapping dictionary
        """
        # Read Excel file
        if sheet_name:
            df = pd.read_excel(excel_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(excel_path, sheet_name=0)
        
        return self._convert_dataframe(df, excel_path, mapping_name, file_format)
    
    def from_csv(self, csv_path: str, mapping_name: str = None, 
                 file_format: str = None) -> dict:
        """
        Convert CSV template to universal mapping.
        
        Args:
            csv_path: Path to CSV file
            mapping_name: Name for the mapping
            file_format: File format (auto-detected if not specified)
            
        Returns:
            Universal mapping dictionary
        """
        df = pd.read_csv(csv_path)
        return self._convert_dataframe(df, csv_path, mapping_name, file_format)
    
    def _convert_dataframe(self, df: pd.DataFrame, template_path: str,
                          mapping_name: str = None, file_format: str = None) -> dict:
        """Convert DataFrame to universal mapping."""
        
        # Clean column names
        df.columns = df.columns.str.strip()

        # Normalize snake_case columns to Title Case (e.g. field_name → Field Name)
        col_map = {
            'field_name': 'Field Name', 'data_type': 'Data Type',
            'position': 'Position', 'length': 'Length', 'format': 'Format',
            'required': 'Required', 'description': 'Description',
            'default_value': 'Default Value', 'target_name': 'Target Name',
            'valid_values': 'Valid Values', 'transformation': 'Transformation',
        }
        df.columns = [col_map.get(c.lower().replace(' ', '_'), c) for c in df.columns]

        # Validate required columns
        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        # Auto-detect format if not specified
        if not file_format:
            file_format = self._detect_format(df)
        
        # Generate mapping name if not specified
        if not mapping_name:
            mapping_name = Path(template_path).stem
        
        # Build mapping structure
        mapping = {
            "mapping_name": mapping_name,
            "version": "1.0.0",
            "description": f"Generated from template: {Path(template_path).name}",
            "source": {
                "type": "file",
                "format": file_format,
                "encoding": "UTF-8"
            },
            "target": {
                "type": "database"
            },
            "fields": [],
            "key_columns": [],
            "metadata": {
                "created_by": "template_converter",
                "created_date": datetime.utcnow().isoformat() + "Z",
                "last_modified": datetime.utcnow().isoformat() + "Z",
                "source_template": str(template_path)
            }
        }
        
        # Add delimiter for delimited formats
        if file_format in ['pipe_delimited', 'csv', 'tsv']:
            if file_format == 'pipe_delimited':
                mapping['source']['delimiter'] = '|'
            elif file_format == 'csv':
                mapping['source']['delimiter'] = ','
            elif file_format == 'tsv':
                mapping['source']['delimiter'] = '\t'
        
        # Convert each row to field specification
        for idx, row in df.iterrows():
            field = self._convert_row_to_field(row, file_format)
            mapping['fields'].append(field)
            
            # Add to key columns if marked as required
            if field.get('required') and not mapping['key_columns']:
                mapping['key_columns'].append(field['name'])
        
        # Calculate total record length for fixed-width
        if file_format == 'fixed_width':
            total_length = sum(f.get('length', 0) for f in mapping['fields'])
            mapping['total_record_length'] = total_length
        
        self.mapping = mapping
        return mapping
    
    def _detect_format(self, df: pd.DataFrame) -> str:
        """Auto-detect file format from template columns."""
        has_position = 'Position' in df.columns
        has_length = 'Length' in df.columns
        
        if has_position and has_length:
            return 'fixed_width'
        else:
            return 'pipe_delimited'  # Default to pipe-delimited
    
    def _convert_row_to_field(self, row: pd.Series, file_format: str) -> Dict[str, Any]:
        """Convert template row to field specification."""
        
        field = {
            "name": str(row['Field Name']).strip(),
            "data_type": self._normalize_data_type(str(row['Data Type']).strip())
        }
        
        # Add target name if specified
        if 'Target Name' in row and pd.notna(row['Target Name']):
            field['target_name'] = str(row['Target Name']).strip()
        
        # Add position and length for fixed-width
        if file_format == 'fixed_width':
            if 'Position' in row and pd.notna(row['Position']):
                field['position'] = int(row['Position'])
            if 'Length' in row and pd.notna(row['Length']):
                field['length'] = int(row['Length'])
        
        # Add format if specified
        if 'Format' in row and pd.notna(row['Format']):
            field['format'] = str(row['Format']).strip()
        
        # Add required flag
        if 'Required' in row and pd.notna(row['Required']):
            required_val = str(row['Required']).strip().upper()
            field['required'] = required_val in ['Y', 'YES', 'TRUE', '1']
        else:
            field['required'] = False
        
        # Add description
        if 'Description' in row and pd.notna(row['Description']):
            field['description'] = str(row['Description']).strip()
        
        # Add default value
        if 'Default Value' in row and pd.notna(row['Default Value']):
            field['default_value'] = row['Default Value']
        
        # Add basic transformations
        field['transformations'] = [{"type": "trim"}]
        
        # Add basic validations
        field['validation_rules'] = []
        if field['required']:
            field['validation_rules'].append({"type": "not_null"})

        # Add allowed-values rule when provided
        if 'Valid Values' in row and pd.notna(row['Valid Values']):
            values_str = str(row['Valid Values']).strip()
            if values_str:
                # Support comma- or pipe-separated lists
                delimiter = '|' if '|' in values_str else ','
                valid_values = [v.strip() for v in values_str.split(delimiter) if v.strip()]
                if valid_values:
                    field['valid_values'] = valid_values
                    field['validation_rules'].append({
                        "type": "in_list",
                        "parameters": {"values": valid_values}
                    })

        return field
    
    def _normalize_data_type(self, data_type: str) -> str:
        """Normalize data type to standard values."""
        data_type_lower = data_type.lower()
        
        if data_type_lower in ['string', 'str', 'text', 'varchar', 'char']:
            return 'string'
        elif data_type_lower in ['number', 'numeric', 'num', 'decimal', 'float']:
            return 'decimal'
        elif data_type_lower in ['integer', 'int']:
            return 'integer'
        elif data_type_lower in ['date', 'datetime', 'timestamp']:
            return 'date'
        elif data_type_lower in ['boolean', 'bool']:
            return 'boolean'
        else:
            return 'string'  # Default to string
    
    def save(self, output_path: str):
        """Save mapping to JSON file."""
        if not self.mapping:
            raise ValueError("No mapping to save. Convert a template first.")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.mapping, f, indent=2)
        
        print(f"Mapping saved to: {output_path}")
    
    def print_summary(self):
        """Print summary of converted mapping."""
        if not self.mapping:
            print("No mapping loaded")
            return
        
        print(f"\nMapping Summary:")
        print(f"  Name: {self.mapping['mapping_name']}")
        print(f"  Format: {self.mapping['source']['format']}")
        print(f"  Fields: {len(self.mapping['fields'])}")
        print(f"  Key columns: {self.mapping.get('key_columns', [])}")
        
        if self.mapping['source']['format'] == 'fixed_width':
            print(f"  Total record length: {self.mapping.get('total_record_length', 0)} characters")
        
        print(f"\nFirst 5 fields:")
        for field in self.mapping['fields'][:5]:
            print(f"    {field['name']} ({field['data_type']})")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python template_converter.py <template_file> <output_json> [mapping_name] [format]")
        print("\nExamples:")
        print("  python template_converter.py template.xlsx mapping.json")
        print("  python template_converter.py template.csv mapping.json my_mapping fixed_width")
        sys.exit(1)
    
    template_file = sys.argv[1]
    output_file = sys.argv[2]
    mapping_name = sys.argv[3] if len(sys.argv) > 3 else None
    file_format = sys.argv[4] if len(sys.argv) > 4 else None
    
    converter = TemplateConverter()
    
    # Convert based on file extension
    if template_file.endswith('.xlsx') or template_file.endswith('.xls'):
        mapping = converter.from_excel(template_file, mapping_name=mapping_name, file_format=file_format)
    elif template_file.endswith('.csv'):
        mapping = converter.from_csv(template_file, mapping_name=mapping_name, file_format=file_format)
    else:
        print(f"Unsupported file format: {template_file}")
        sys.exit(1)
    
    # Print summary
    converter.print_summary()
    
    # Save to file
    converter.save(output_file)
    
    print(f"\n✓ Conversion complete!")
