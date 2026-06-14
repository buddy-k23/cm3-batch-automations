#!/bin/bash
# Compare P327 test files using chunked processing

echo "Comparing P327 test files (20,000 rows each)..."
echo ""
echo "Files:"
echo "  File 1: data/samples/p327_test_data_a_20000.txt"
echo "  File 2: data/samples/p327_test_data_b_20000.txt"
echo ""

# Since these are fixed-width files without headers, we'll use a simple diff
# For a full comparison with the chunked processor, we'd need to define the field positions

# Option 1: Simple diff to see if files are identical
echo "Running simple diff check..."
if diff -q data/samples/p327_test_data_a_20000.txt data/samples/p327_test_data_b_20000.txt > /dev/null 2>&1; then
    echo "✓ Files are identical - no differences found"
else
    echo "✗ Files have differences"
    echo ""
    echo "Generating difference summary..."
    
    # Count different lines
    diff_count=$(diff data/samples/p327_test_data_a_20000.txt data/samples/p327_test_data_b_20000.txt | grep -c "^<\|^>")
    echo "  Total lines with differences: $diff_count"
    
    # Show first 10 differences
    echo ""
    echo "First 10 differences:"
    diff data/samples/p327_test_data_a_20000.txt data/samples/p327_test_data_b_20000.txt | head -20
    
    # Save full diff to file
    echo ""
    echo "Saving full diff to reports/p327_diff.txt..."
    mkdir -p reports
    diff data/samples/p327_test_data_a_20000.txt data/samples/p327_test_data_b_20000.txt > reports/p327_diff.txt
    echo "✓ Full diff saved to reports/p327_diff.txt"
fi

echo ""
echo "Comparison complete!"
