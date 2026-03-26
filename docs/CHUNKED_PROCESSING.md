# Chunked Processing Guide

## Overview

Chunked processing enables Valdo to handle files with 10M+ rows using minimal memory (<1GB RAM). This guide explains how to use chunked processing effectively.

---

## 🎯 When to Use Chunked Processing

### Use Chunked Processing When:
- Files have >500K rows
- Machine has <16GB RAM
- Processing multiple large files simultaneously
- Memory usage is a concern

### Use Standard Processing When:
- Files have <100K rows
- Machine has 32GB+ RAM
- Maximum speed is priority (chunked is slightly slower)

---

## 🚀 Quick Start

### File Comparison (Chunked)

```bash
valdo compare \
  -f1 large_file1.txt \
  -f2 large_file2.txt \
  -k customer_id \
  --use-chunked \
  --chunk-size 100000 \
  --progress
```

**Options:**
- `--use-chunked`: Enable chunked processing
- `--chunk-size`: Rows per chunk (default: 100,000)
- `--progress`: Show progress bar with ETA

---

## 📊 Performance Characteristics

### Memory Usage

| File Size | Standard | Chunked | Savings |
|-----------|----------|---------|---------|
| 100K rows | 200MB | 100MB | 50% |
| 1M rows | 2GB | 500MB | 75% |
| 5M rows | 10GB | 800MB | 92% |
| 10M rows | 20GB+ | 1GB | 95% |

### Processing Time

| File Size | Standard | Chunked | Overhead |
|-----------|----------|---------|----------|
| 100K rows | 5s | 8s | +60% |
| 1M rows | 45s | 90s | +100% |
| 5M rows | OOM | 8min | N/A |
| 10M rows | OOM | 18min | N/A |

**Note:** Standard processing fails (Out of Memory) on large files.

---

## 🔧 Tuning Chunk Size

### Choosing Chunk Size

**Smaller chunks (10K-50K):**
- ✅ Lower memory usage
- ✅ Better for very large files
- ❌ Slower processing
- ❌ More I/O operations

**Larger chunks (100K-500K):**
- ✅ Faster processing
- ✅ Fewer I/O operations
- ❌ Higher memory usage
- ❌ May fail on very large files

### Recommended Chunk Sizes

| Available RAM | Recommended Chunk Size |
|---------------|------------------------|
| 4GB | 50,000 |
| 8GB | 100,000 (default) |
| 16GB+ | 250,000 |

### Auto-Tuning Formula

```python
# Estimate optimal chunk size
available_ram_gb = 8
num_columns = 20
chunk_size = int((available_ram_gb * 1000000) / (num_columns * 2))
```

---

## 📝 Examples

### Example 1: Compare Large Files

```bash
# Compare two 5M row files
valdo compare \
  -f1 source_5m.txt \
  -f2 target_5m.txt \
  -k id,account \
  --use-chunked \
  --chunk-size 100000 \
  --progress \
  -o comparison_report.html
```

**Output:**
```
Using chunked processing (chunk size: 100,000)...
Indexing file1: |████████████████████████████| 100.0% (5,000,000/5,000,000) [50000 rows/s, ETA: 0s]
Comparing file2: |████████████████████████████| 100.0% (5,000,000/5,000,000) [45000 rows/s, ETA: 0s]

Comparison Summary:
  Total rows (File 1): 5,000,000
  Total rows (File 2): 5,000,000
  Matching rows: 4,950,000
  Only in File 1: 25,000
  Only in File 2: 20,000
  Rows with differences: 5,000
```

### Example 2: Parse Large File

```python
from src.parsers.chunked_parser import ChunkedFileParser
from src.utils.progress import SimpleProgressCallback

# Create parser
parser = ChunkedFileParser(
    'large_file.txt',
    delimiter='|',
    chunk_size=100000
)

# Parse with progress
callback = SimpleProgressCallback("Parsing")
for chunk in parser.parse_with_progress(progress_callback=callback):
    # Process chunk
    process_chunk(chunk)
```

### Example 3: Validate Large File

```python
from src.parsers.chunked_validator import ChunkedFileValidator

# Create validator
validator = ChunkedFileValidator(
    'large_file.txt',
    delimiter='|',
    chunk_size=100000
)

# Validate
result = validator.validate(show_progress=True)

if result['valid']:
    print("✓ File is valid")
    print(f"Total rows: {result['total_rows']:,}")
else:
    print("✗ Validation failed")
    for error in result['errors']:
        print(f"  ERROR: {error}")
```

---

## 🔍 How It Works

### Chunked File Comparison

#### Step 1: Index File1
```
File1 → SQLite Database (indexed by key columns)
Memory: ~100MB regardless of file size
```

#### Step 2: Compare File2
```
For each chunk in File2:
  1. Load chunk (100K rows)
  2. Query SQLite for matching keys
  3. Compare values
  4. Write differences
  5. Free memory
```

#### Step 3: Results
```
Differences written incrementally
Memory: <1GB total
```

### Key Optimizations

1. **SQLite Indexing**: Fast key lookups without loading entire file
2. **Streaming I/O**: Read/write in chunks
3. **Garbage Collection**: Periodic memory cleanup
4. **Progress Tracking**: Real-time feedback

---

## 📈 Monitoring

### Memory Monitoring

```python
from src.utils.memory_monitor import MemoryMonitor

# Create monitor with 2GB threshold
monitor = MemoryMonitor(threshold_mb=2000)

# Check memory
current_mb = monitor.get_current_memory_mb()
print(f"Memory usage: {current_mb:.1f} MB")

# Get system info
info = monitor.get_system_memory_info()
print(f"System: {info['percent_used']:.1f}% used")

# Force garbage collection if needed
if monitor.check_threshold():
    monitor.force_garbage_collection()
```

### Progress Tracking

```python
from src.utils.progress import ProgressTracker

# Create tracker
progress = ProgressTracker(total=1000000, description="Processing")

# Update progress
for i in range(1000000):
    process_item(i)
    if i % 10000 == 0:
        progress.update(i)

progress.finish()
```

---

## ⚠️ Limitations

### Current Limitations

1. **Comparison Differences Limit**: Returns max 1,000 differences (memory protection)
2. **Duplicate Detection**: Limited to first 100K rows
3. **Slightly Slower**: 50-100% slower than standard processing
4. **Temporary Database**: Creates temp SQLite file (auto-cleaned)

### Workarounds

**For >1,000 differences:**
```bash
# Write differences to file incrementally
# (Feature coming soon)
```

**For full duplicate detection:**
```bash
# Use standard processing on smaller file
# Or split file into chunks
```

---

## 🐛 Troubleshooting

### Issue: Out of Memory

**Solution:**
- Reduce chunk size: `--chunk-size 50000`
- Close other applications
- Use machine with more RAM

### Issue: Slow Performance

**Solution:**
- Increase chunk size: `--chunk-size 250000`
- Use SSD instead of HDD
- Disable detailed analysis: `--basic`

### Issue: Temp Database Errors

**Solution:**
- Ensure /tmp has enough space
- Check file permissions
- Restart application

---

## 📚 API Reference

### ChunkedFileParser

```python
parser = ChunkedFileParser(
    file_path: str,
    delimiter: str = '|',
    chunk_size: int = 100000,
    encoding: str = 'utf-8'
)

# Parse in chunks
for chunk in parser.parse_chunks():
    process(chunk)

# Parse with progress
for chunk in parser.parse_with_progress(progress_callback=callback):
    process(chunk)

# Get file info
info = parser.get_file_info()
```

### ChunkedFileComparator

```python
comparator = ChunkedFileComparator(
    file1_path: str,
    file2_path: str,
    key_columns: List[str],
    delimiter: str = '|',
    chunk_size: int = 100000,
    ignore_columns: Optional[List[str]] = None
)

# Compare
results = comparator.compare(
    detailed: bool = True,
    show_progress: bool = True
)
```

### ChunkedFileValidator

```python
validator = ChunkedFileValidator(
    file_path: str,
    delimiter: str = '|',
    chunk_size: int = 100000
)

# Validate
result = validator.validate(show_progress=True)

# Validate with schema
result = validator.validate_with_schema(
    expected_columns=['id', 'name', 'value'],
    required_columns=['id', 'name']
)
```

---

## 🎯 Best Practices

1. **Start with defaults**: Use default chunk size (100K) first
2. **Monitor memory**: Watch memory usage during first run
3. **Tune as needed**: Adjust chunk size based on performance
4. **Use progress bars**: Always enable `--progress` for large files
5. **Test on samples**: Test with small sample before full file
6. **Clean up**: Temp files are auto-cleaned, but verify

---

## 🚀 Next Steps

- See [`docs/FUNCTIONALITY_GUIDE.md`](FUNCTIONALITY_GUIDE.md) for all features
- See [`docs/SCALABILITY_ANALYSIS.md`](SCALABILITY_ANALYSIS.md) for performance details
- See [`tests/unit/test_chunked_parser.py`](../tests/unit/test_chunked_parser.py) for code examples
