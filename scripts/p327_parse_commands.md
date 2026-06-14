# P327 Test Data Parse Commands

## File Information
- **File A**: `data/samples/p327_test_data_a_20000.txt` (20,000 rows)
- **File B**: `data/samples/p327_test_data_b_20000.txt` (20,000 rows)
- **Format**: Fixed-width format (no delimiters)

---

## Parse Commands

### Parse File A (Standard)
```bash
python -m src.main parse \
  -f data/samples/p327_test_data_a_20000.txt \
  -o output/p327_file_a_parsed.csv
```

### Parse File B (Standard)
```bash
python -m src.main parse \
  -f data/samples/p327_test_data_b_20000.txt \
  -o output/p327_file_b_parsed.csv
```

---

## Parse Commands with Chunked Processing (Recommended for 20K rows)

### Parse File A (Chunked)
```bash
python -m src.main parse \
  -f data/samples/p327_test_data_a_20000.txt \
  -o output/p327_file_a_parsed.csv \
  --chunk-size 5000 \
  --progress
```

### Parse File B (Chunked)
```bash
python -m src.main parse \
  -f data/samples/p327_test_data_b_20000.txt \
  -o output/p327_file_b_parsed.csv \
  --chunk-size 5000 \
  --progress
```

---

## Parse Both Files (Batch Script)

### Create Output Directory
```bash
mkdir -p output
```

### Bash Script to Parse Both Files
```bash
#!/bin/bash
# Parse both P327 test files

echo "Parsing P327 test files..."
echo ""

# Parse File A
echo "Parsing File A (p327_test_data_a_20000.txt)..."
python -m src.main parse \
  -f data/samples/p327_test_data_a_20000.txt \
  -o output/p327_file_a_parsed.csv

echo ""

# Parse File B
echo "Parsing File B (p327_test_data_b_20000.txt)..."
python -m src.main parse \
  -f data/samples/p327_test_data_b_20000.txt \
  -o output/p327_file_b_parsed.csv

echo ""
echo "✓ Both files parsed successfully!"
echo "  Output: output/p327_file_a_parsed.csv"
echo "  Output: output/p327_file_b_parsed.csv"
```

---

## Notes

1. **Format Detection**: The parse command will auto-detect the file format (fixed-width)
2. **Output Format**: Parsed files will be saved as CSV for easier analysis
3. **Chunked Processing**: For 20K rows, chunked processing is optional but recommended for memory efficiency
4. **Progress Tracking**: Use `--progress` flag to see real-time parsing progress

---

## Expected Output

After parsing, you'll have:
- `output/p327_file_a_parsed.csv` - Parsed data from File A
- `output/p327_file_b_parsed.csv` - Parsed data from File B

These CSV files can then be:
- Opened in Excel/spreadsheet software
- Compared using the compare command
- Analyzed programmatically
