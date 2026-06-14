"""Parse P327 Excel mapping template and generate configuration."""

import pandas as pd
import json
from pathlib import Path


def parse_p327_mapping(excel_path: str, output_json: str = None):
    """
    Parse P327 Excel mapping template and extract field specifications.
    
    Args:
        excel_path: Path to P327 Excel mapping file
        output_json: Optional path to save JSON configuration
        
    Returns:
        Dictionary with field specifications
    """
    # Read Excel file
    df = pd.read_excel(excel_path, sheet_name=0)
    
    # Clean column names
    df.columns = df.columns.str.strip()
    
    # Extract field specifications
    fields = []
    current_position = 1
    
    for idx, row in df.iterrows():
        field_spec = {
            'name': str(row['Field Name']).strip(),
            'position': int(row['Target Position']),
            'length': int(row['Length']),
            'data_type': str(row['Data Type']).strip(),
            'format': str(row['Format']) if pd.notna(row['Format']) else None,
            'required': str(row['Required']).strip().upper() == 'Y',
            'description': str(row['Description']) if pd.notna(row['Description']) else None,
            'transaction_type': str(row['Transaction Type']) if pd.notna(row['Transaction Type']) else 'default'
        }
        fields.append(field_spec)
    
    # Create configuration
    config = {
        'mapping_name': 'P327 Target Template',
        'format_type': 'fixed_width',
        'total_fields': len(fields),
        'fields': fields
    }
    
    # Calculate total record length
    total_length = sum(f['length'] for f in fields)
    config['total_record_length'] = total_length
    
    # Save to JSON if requested
    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"Configuration saved to: {output_json}")
    
    return config


def generate_field_positions(config: dict):
    """
    Generate field position tuples for fixed-width parser.
    
    Args:
        config: Configuration dictionary from parse_p327_mapping
        
    Returns:
        List of (field_name, start, end) tuples
    """
    positions = []
    current_pos = 0
    
    for field in config['fields']:
        start = current_pos
        end = current_pos + field['length']
        positions.append((field['name'], start, end))
        current_pos = end
    
    return positions


def print_mapping_summary(config: dict):
    """Print summary of mapping configuration."""
    print(f"\n{'='*80}")
    print(f"Mapping: {config['mapping_name']}")
    print(f"{'='*80}")
    print(f"Format Type: {config['format_type']}")
    print(f"Total Fields: {config['total_fields']}")
    print(f"Total Record Length: {config['total_record_length']} characters")
    print(f"\nField Breakdown:")
    print(f"  String fields: {sum(1 for f in config['fields'] if f['data_type'] == 'String')}")
    print(f"  Numeric fields: {sum(1 for f in config['fields'] if f['data_type'] == 'Numeric')}")
    print(f"  Date fields: {sum(1 for f in config['fields'] if f['data_type'] == 'Date')}")
    print(f"  Required fields: {sum(1 for f in config['fields'] if f['required'])}")
    
    print(f"\nFirst 10 fields:")
    print(f"{'Field Name':<25} {'Position':<10} {'Length':<8} {'Type':<10} {'Required'}")
    print(f"{'-'*80}")
    for field in config['fields'][:10]:
        required = 'Y' if field['required'] else 'N'
        print(f"{field['name']:<25} {field['position']:<10} {field['length']:<8} {field['data_type']:<10} {required}")


if __name__ == '__main__':
    import sys
    
    # Parse mapping
    excel_path = 'data/mappings/p327-target-template.xlsx'
    output_json = 'config/mappings/p327_mapping.json'
    
    print("Parsing P327 mapping template...")
    config = parse_p327_mapping(excel_path, output_json)
    
    # Print summary
    print_mapping_summary(config)
    
    # Generate positions for parser
    positions = generate_field_positions(config)
    print(f"\n\nGenerated {len(positions)} field positions for fixed-width parser")
    print(f"Total record length: {config['total_record_length']} characters")
