"""Demonstrate P327 file comparison capabilities."""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from parsers.fixed_width_parser import FixedWidthParser
from comparators.file_comparator import FileComparator
from reporters.html_reporter import HTMLReporter

# Import functions from other scripts
import importlib.util
spec = importlib.util.spec_from_file_location("generate_p327_test_data", 
    Path(__file__).parent / "generate_p327_test_data.py")
gen_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_module)
generate_test_file = gen_module.generate_test_file


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


def create_comparison_files():
    """Create two P327 files with intentional differences for comparison."""
    config_path = 'config/mappings/p327_mapping.json'
    
    print("Creating comparison test files...")
    
    # Generate first file
    file1 = 'data/samples/p327_file1.txt'
    generate_test_file(config_path, file1, num_records=5)
    print(f"  Created: {file1}")
    
    # Generate second file (will have different data)
    file2 = 'data/samples/p327_file2.txt'
    generate_test_file(config_path, file2, num_records=5)
    print(f"  Created: {file2}")
    
    return file1, file2


def compare_p327_files(file1: str, file2: str, output_report: str = None):
    """
    Compare two P327 files and generate comparison report.
    
    Args:
        file1: Path to first file
        file2: Path to second file
        output_report: Optional path for HTML report
    """
    print("\n" + "="*80)
    print("P327 File Comparison")
    print("="*80)
    
    # Load field positions
    config_path = 'config/mappings/p327_mapping.json'
    field_positions = load_p327_field_positions(config_path)
    
    # Parse both files
    print(f"\n1. Parsing files...")
    parser1 = FixedWidthParser(file1, field_positions)
    df1 = parser1.parse()
    print(f"   File 1: {len(df1)} rows, {len(df1.columns)} columns")
    
    parser2 = FixedWidthParser(file2, field_positions)
    df2 = parser2.parse()
    print(f"   File 2: {len(df2)} rows, {len(df2.columns)} columns")
    
    # Compare files using ACCT-NUM as key
    print(f"\n2. Comparing files...")
    key_columns = ['ACCT-NUM']
    comparator = FileComparator(df1, df2, key_columns)
    results = comparator.compare(detailed=True)
    
    # Display comparison summary
    print(f"\n3. Comparison Results:")
    print(f"   Total rows (File 1): {results['total_rows_file1']}")
    print(f"   Total rows (File 2): {results['total_rows_file2']}")
    print(f"   Matching rows: {results['matching_rows']}")
    print(f"   Only in File 1: {len(results['only_in_file1'])}")
    print(f"   Only in File 2: {len(results['only_in_file2'])}")
    print(f"   Rows with differences: {len(results['differences'])}")
    
    # Field-level statistics
    if results.get('field_statistics'):
        stats = results['field_statistics']
        print(f"\n4. Field-Level Statistics:")
        print(f"   Fields with differences: {stats['fields_with_differences']}")
        if stats.get('most_different_field'):
            print(f"   Most different field: {stats['most_different_field']}")
        
        # Show top fields with differences
        if stats.get('field_difference_counts'):
            print(f"\n   Top 5 fields with most differences:")
            sorted_fields = sorted(
                stats['field_difference_counts'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            for field, count in sorted_fields:
                print(f"     {field}: {count} differences")
    
    # Generate HTML report
    if output_report:
        print(f"\n5. Generating HTML report...")
        reporter = HTMLReporter()
        reporter.generate(results, output_report)
        print(f"   Report saved to: {output_report}")
    
    print(f"\n{'='*80}")
    print("Comparison completed!")
    print(f"{'='*80}\n")
    
    return results


if __name__ == '__main__':
    # Create test files
    file1, file2 = create_comparison_files()
    
    # Compare files
    output_report = 'reports/p327_comparison_report.html'
    results = compare_p327_files(file1, file2, output_report)
    
    print(f"\nComparison Summary:")
    print(f"  Files compared: {file1} vs {file2}")
    print(f"  HTML Report: {output_report}")
    print(f"  Total differences: {len(results['differences'])}")
