"""Test P327 file parsing using the mapping configuration."""

import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from parsers.fixed_width_parser import FixedWidthParser
from parsers.format_detector import FormatDetector
import pandas as pd


def load_p327_field_positions(config_path: str):
    """Load field positions from P327 configuration."""
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    positions = []
    current_pos = 0
    
    for field in config['fields']:
        start = current_pos
        end = current_pos + field['length']
        positions.append((field['name'], start, end))
        current_pos = end
    
    return positions


def test_p327_file_parsing(file_path: str, config_path: str):
    """
    Test parsing of P327 file using the mapping configuration.
    
    Args:
        file_path: Path to test data file
        config_path: Path to P327 mapping JSON
    """
    print("="*80)
    print("P327 File Parsing Test")
    print("="*80)
    
    # Load field positions
    print(f"\n1. Loading field positions from: {config_path}")
    field_positions = load_p327_field_positions(config_path)
    print(f"   Loaded {len(field_positions)} field definitions")
    
    # Detect format
    print(f"\n2. Detecting file format: {file_path}")
    detector = FormatDetector()
    detection = detector.detect(file_path)
    print(f"   Detected format: {detection['format'].value}")
    print(f"   Confidence: {detection['confidence']:.2%}")
    
    # Parse file
    print(f"\n3. Parsing file with fixed-width parser...")
    parser = FixedWidthParser(file_path, field_positions)
    df = parser.parse()
    
    print(f"   Successfully parsed {len(df)} rows")
    print(f"   Columns: {len(df.columns)}")
    
    # Display sample data
    print(f"\n4. Sample Data (first 5 rows, first 10 columns):")
    print(df.iloc[:5, :10].to_string())
    
    # Display statistics
    print(f"\n5. Data Statistics:")
    print(f"   Total rows: {len(df)}")
    print(f"   Total columns: {len(df.columns)}")
    print(f"   Memory usage: {df.memory_usage(deep=True).sum() / 1024:.2f} KB")
    
    # Check required fields
    print(f"\n6. Required Fields Check:")
    required_fields = ['LOCATION-CODE', 'ACCT-NUM']
    for field in required_fields:
        if field in df.columns:
            null_count = df[field].isna().sum()
            print(f"   {field}: {len(df) - null_count}/{len(df)} populated")
        else:
            print(f"   {field}: NOT FOUND")
    
    # Display field value samples
    print(f"\n7. Sample Field Values:")
    sample_fields = ['LOCATION-CODE', 'ACCT-NUM', 'CREDIT-LIMIT-AMT', 'BALANCE-AMT']
    for field in sample_fields:
        if field in df.columns:
            sample_values = df[field].head(3).tolist()
            print(f"   {field}: {sample_values}")
    
    print(f"\n{'='*80}")
    print("Test completed successfully!")
    print(f"{'='*80}")
    
    return df


if __name__ == '__main__':
    file_path = 'data/samples/p327_test_data.txt'
    config_path = 'config/mappings/p327_mapping.json'
    
    try:
        df = test_p327_file_parsing(file_path, config_path)
        
        # Save parsed data to CSV for inspection
        output_csv = 'data/samples/p327_parsed_output.csv'
        df.to_csv(output_csv, index=False)
        print(f"\nParsed data saved to: {output_csv}")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
