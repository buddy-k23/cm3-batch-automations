"""Chunked file comparator for memory-efficient large file comparison."""

import pandas as pd
import sqlite3
import tempfile
import os
from typing import List, Dict, Any, Optional, Iterator
from pathlib import Path
import time
from ..parsers.chunked_parser import ChunkedFileParser
from ..utils.progress import ProgressTracker
from ..utils.memory_monitor import MemoryMonitor
from ..utils.logger import get_logger


class ChunkedFileComparator:
    """Compare large files in chunks using SQLite indexing."""
    
    def __init__(self, file1_path: str, file2_path: str, key_columns: List[str],
                 delimiter: str = '|', chunk_size: int = 100000,
                 ignore_columns: Optional[List[str]] = None):
        """Initialize chunked comparator.
        
        Args:
            file1_path: Path to first file
            file2_path: Path to second file
            key_columns: Columns to use as unique identifiers
            delimiter: Field delimiter
            chunk_size: Rows per chunk
            ignore_columns: Columns to ignore in comparison
        """
        self.file1_path = file1_path
        self.file2_path = file2_path
        self.key_columns = key_columns
        self.delimiter = delimiter
        self.chunk_size = chunk_size
        self.ignore_columns = ignore_columns or []
        self.logger = get_logger(__name__)
        self.memory_monitor = MemoryMonitor()
        
        # Create temporary database
        self.temp_db_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db_path = self.temp_db_file.name
        self.temp_db_file.close()
        self.db_conn = sqlite3.connect(self.temp_db_path)
        
        self.logger.info(f"Created temporary database: {self.temp_db_path}")
    
    def compare(self, detailed: bool = True, show_progress: bool = True) -> Dict[str, Any]:
        """Compare files in chunks.
        
        Args:
            detailed: Include detailed field-level analysis
            show_progress: Show progress bar
            
        Returns:
            Comparison results dictionary
        """
        start_time = time.time()
        self.logger.info(f"Starting chunked comparison: {self.file1_path} vs {self.file2_path}")
        self.memory_monitor.log_memory_usage("comparison start")
        
        try:
            # Step 1: Index file1
            self.logger.info("Step 1: Indexing file1...")
            file1_count = self._index_file1(show_progress)
            self.memory_monitor.log_memory_usage("after indexing file1")
            
            # Step 2: Compare file2 and find differences
            self.logger.info("Step 2: Comparing file2...")
            differences, file2_count, matched_keys = self._compare_file2(detailed, show_progress)
            self.memory_monitor.log_memory_usage("after comparing file2")
            
            # Step 3: Find rows only in file1
            self.logger.info("Step 3: Finding unique rows in file1...")
            only_in_file1_count = self._count_unmatched_file1(matched_keys)
            
            # Calculate statistics
            matching_rows = matched_keys - len(differences)
            only_in_file2_count = file2_count - matched_keys
            
            results = {
                'total_rows_file1': file1_count,
                'total_rows_file2': file2_count,
                'matching_rows': matching_rows,
                'only_in_file1': [],  # Empty list for chunked processing (too large to store)
                'only_in_file2': [],  # Empty list for chunked processing (too large to store)
                'only_in_file1_count': only_in_file1_count,
                'only_in_file2_count': only_in_file2_count,
                'rows_with_differences': len(differences),
                'differences': differences[:1000] if len(differences) > 1000 else differences,  # Limit for memory
                'differences_truncated': len(differences) > 1000,
                'total_differences_found': len(differences)
            }
            
            if detailed and differences:
                results['field_statistics'] = self._calculate_field_statistics(differences)
            
            elapsed = time.time() - start_time
            self.logger.info(
                f"Comparison complete in {elapsed:.1f}s: "
                f"{file1_count:,} vs {file2_count:,} rows, "
                f"{len(differences):,} differences found"
            )
            self.memory_monitor.log_memory_usage("comparison complete")
            
            return results
            
        finally:
            self._cleanup()
    
    def _index_file1(self, show_progress: bool = True) -> int:
        """Index file1 into SQLite database.
        
        Args:
            show_progress: Show progress bar
            
        Returns:
            Total rows indexed
        """
        parser = ChunkedFileParser(self.file1_path, self.delimiter, self.chunk_size)
        
        # Get first chunk to determine columns
        first_chunk = parser.parse_sample(n_rows=10)
        columns = list(first_chunk.columns)
        
        # Create table
        key_cols_str = ', '.join(f'"{col}" TEXT' for col in self.key_columns)
        value_cols_str = ', '.join(
            f'"{col}" TEXT' for col in columns 
            if col not in self.key_columns and col not in self.ignore_columns
        )
        all_cols_str = f"{key_cols_str}, {value_cols_str}" if value_cols_str else key_cols_str
        
        self.db_conn.execute(f"CREATE TABLE file1 ({all_cols_str})")
        
        # Create index on key columns
        key_index_str = ', '.join(f'"{col}"' for col in self.key_columns)
        self.db_conn.execute(f"CREATE INDEX idx_keys ON file1 ({key_index_str})")
        
        # Insert data in chunks
        total_rows = 0
        progress = ProgressTracker(parser.count_rows(), "Indexing file1") if show_progress else None
        
        for chunk in parser.parse_chunks():
            # Filter columns
            chunk_filtered = chunk[[col for col in columns 
                                   if col not in self.ignore_columns]]
            
            # Insert into database
            chunk_filtered.to_sql('file1', self.db_conn, if_exists='append', index=False)
            total_rows += len(chunk)
            
            if progress:
                progress.update(total_rows)
            
            # Periodic garbage collection
            if total_rows % (self.chunk_size * 10) == 0:
                self.memory_monitor.force_garbage_collection()
        
        self.db_conn.commit()
        
        if progress:
            progress.finish()
        
        self.logger.info(f"Indexed {total_rows:,} rows from file1")
        return total_rows
    
    def _compare_file2(self, detailed: bool, show_progress: bool) -> tuple:
        """Compare file2 against indexed file1.
        
        Args:
            detailed: Include detailed analysis
            show_progress: Show progress bar
            
        Returns:
            Tuple of (differences list, total_rows, matched_keys_count)
        """
        parser = ChunkedFileParser(self.file2_path, self.delimiter, self.chunk_size)
        
        differences = []
        total_rows = 0
        matched_keys = 0
        
        progress = ProgressTracker(parser.count_rows(), "Comparing file2") if show_progress else None
        
        for chunk in parser.parse_chunks():
            chunk_diffs, chunk_matches = self._compare_chunk(chunk, detailed)
            differences.extend(chunk_diffs)
            matched_keys += chunk_matches
            total_rows += len(chunk)
            
            if progress:
                progress.update(total_rows)
            
            # Periodic garbage collection
            if total_rows % (self.chunk_size * 10) == 0:
                self.memory_monitor.force_garbage_collection()
        
        if progress:
            progress.finish()
        
        return differences, total_rows, matched_keys
    
    def _compare_chunk(self, chunk: pd.DataFrame, detailed: bool) -> tuple:
        """Compare a chunk against indexed file1.
        
        Args:
            chunk: DataFrame chunk from file2
            detailed: Include detailed analysis
            
        Returns:
            Tuple of (differences list, matched_keys_count)
        """
        differences = []
        matched_keys = 0
        
        # Get value columns
        value_columns = [col for col in chunk.columns 
                        if col not in self.key_columns and col not in self.ignore_columns]
        
        for _, row in chunk.iterrows():
            # Build key condition
            key_conditions = ' AND '.join(
                f'"{col}" = ?' for col in self.key_columns
            )
            key_values = [row[col] for col in self.key_columns]
            
            # Query file1 for matching row
            query = f"SELECT * FROM file1 WHERE {key_conditions}"
            cursor = self.db_conn.execute(query, key_values)
            file1_row = cursor.fetchone()
            
            if file1_row is None:
                # Row only in file2 (handled separately)
                continue
            
            matched_keys += 1
            
            # Convert to dict
            columns = [desc[0] for desc in cursor.description]
            file1_dict = dict(zip(columns, file1_row))
            
            # Compare values
            row_diffs = {}
            for col in value_columns:
                val1 = file1_dict.get(col, '')
                val2 = str(row[col]) if pd.notna(row[col]) else ''
                
                if val1 != val2:
                    if detailed:
                        row_diffs[col] = self._analyze_field_difference(col, val1, val2)
                    else:
                        row_diffs[col] = {'file1': val1, 'file2': val2}
            
            if row_diffs:
                diff_entry = {
                    'keys': {k: row[k] for k in self.key_columns},
                    'differences': row_diffs
                }
                if detailed:
                    diff_entry['difference_count'] = len(row_diffs)
                
                differences.append(diff_entry)
        
        return differences, matched_keys
    
    def _analyze_field_difference(self, field_name: str, val1: Any, val2: Any) -> Dict[str, Any]:
        """Analyze difference between two field values.
        
        Args:
            field_name: Name of field
            val1: Value from file1
            val2: Value from file2
            
        Returns:
            Difference analysis dictionary
        """
        result = {
            'file1': val1,
            'file2': val2,
            'type': self._get_difference_type(val1, val2)
        }
        
        # String analysis
        if isinstance(val1, str) and isinstance(val2, str):
            result['string_analysis'] = {
                'length_diff': len(val2) - len(val1),
                'case_only': val1.lower() == val2.lower(),
                'whitespace_diff': val1.strip() == val2.strip()
            }
        
        # Numeric analysis
        try:
            num1 = float(val1)
            num2 = float(val2)
            diff = num2 - num1
            result['numeric_analysis'] = {
                'absolute_difference': diff,
                'percent_change': (diff / num1 * 100) if num1 != 0 else float('inf')
            }
        except (ValueError, TypeError):
            pass
        
        return result
    
    def _get_difference_type(self, val1: Any, val2: Any) -> str:
        """Determine type of difference.
        
        Args:
            val1: First value
            val2: Second value
            
        Returns:
            Difference type string
        """
        if not val1 and not val2:
            return 'both_empty'
        elif not val1:
            return 'empty_to_value'
        elif not val2:
            return 'value_to_empty'
        else:
            return 'value_difference'
    
    def _count_unmatched_file1(self, matched_keys: int) -> int:
        """Count rows in file1 that weren't matched.
        
        Args:
            matched_keys: Number of keys that were matched
            
        Returns:
            Count of unmatched rows
        """
        cursor = self.db_conn.execute("SELECT COUNT(*) FROM file1")
        total = cursor.fetchone()[0]
        return total - matched_keys
    
    def _calculate_field_statistics(self, differences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate statistics about field differences.
        
        Args:
            differences: List of difference dictionaries
            
        Returns:
            Field statistics dictionary
        """
        field_counts = {}
        
        for diff in differences:
            for field in diff['differences'].keys():
                field_counts[field] = field_counts.get(field, 0) + 1
        
        sorted_fields = sorted(field_counts.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'fields_with_differences': len(field_counts),
            'field_difference_counts': dict(sorted_fields),
            'most_different_field': sorted_fields[0][0] if sorted_fields else None
        }
    
    def _cleanup(self):
        """Clean up temporary database."""
        try:
            self.db_conn.close()
            if os.path.exists(self.temp_db_path):
                os.unlink(self.temp_db_path)
                self.logger.info(f"Cleaned up temporary database: {self.temp_db_path}")
        except Exception as e:
            self.logger.warning(f"Error cleaning up temporary database: {e}")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self._cleanup()
