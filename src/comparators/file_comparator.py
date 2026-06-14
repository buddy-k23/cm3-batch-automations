"""Compare files and identify differences."""

import pandas as pd
from typing import Dict, List, Any, Optional
from ..utils.logger import get_logger


class FileComparator:
    """Compares two DataFrames and identifies differences."""

    def __init__(self, df1: pd.DataFrame, df2: pd.DataFrame, key_columns: Optional[List[str]] = None,
                 ignore_columns: Optional[List[str]] = None):
        """Initialize file comparator.
        
        Args:
            df1: First DataFrame
            df2: Second DataFrame
            key_columns: Columns to use as unique identifiers. If None, compares row-by-row.
            ignore_columns: Columns to ignore in comparison
        """
        self.df1 = df1
        self.df2 = df2
        self.key_columns = key_columns
        self.ignore_columns = ignore_columns or []
        self.logger = get_logger(__name__)

    def compare(self, detailed: bool = True) -> Dict[str, Any]:
        """Compare DataFrames and return differences.
        
        Args:
            detailed: Include detailed field-level differences
        
        Returns:
            Dictionary containing comparison results
        """
        # Row-by-row comparison if no keys provided
        if self.key_columns is None:
            return self._compare_row_by_row(detailed)
        
        # Key-based comparison
        only_in_df1 = self._find_unique_rows(self.df1, self.df2)
        only_in_df2 = self._find_unique_rows(self.df2, self.df1)
        
        if detailed:
            differences = self._find_detailed_differences()
        else:
            differences = self._find_differences()
        
        total_differences = len(differences)
        field_diff_stats = self._calculate_field_statistics(differences) if detailed else {}
        
        return {
            "only_in_file1": only_in_df1,
            "only_in_file2": only_in_df2,
            "differences": differences,
            "total_rows_file1": len(self.df1),
            "total_rows_file2": len(self.df2),
            "matching_rows": len(self.df1) - len(only_in_df1) - total_differences,
            "rows_with_differences": total_differences,
            "field_statistics": field_diff_stats,
        }

    def _find_unique_rows(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> pd.DataFrame:
        """Find rows in source that don't exist in target.

        Args:
            df_source: DataFrame whose unique rows are returned.
            df_target: DataFrame used as the reference set.

        Returns:
            DataFrame of rows present in ``df_source`` but absent from
            ``df_target``, with ``__source_row__`` renamed to ``source_row``
            for user-facing readability.
        """
        # Only merge on key columns that exist in df_target (guard against missing cols)
        target_key_cols = [c for c in self.key_columns if c in df_target.columns]
        merged = df_source.merge(
            df_target[target_key_cols],
            on=target_key_cols,
            how="left",
            indicator=True,
        )
        unique_rows = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
        # Rename the internal tracking column to a user-friendly name.
        if '__source_row__' in unique_rows.columns:
            unique_rows = unique_rows.rename(columns={'__source_row__': 'source_row'})
        return unique_rows

    def _find_differences(self) -> List[Dict[str, Any]]:
        """Find rows with same keys but different values.

        Returns:
            List of dicts, each containing ``keys``, ``differences``,
            ``source_row_file1``, and ``source_row_file2``.  The source
            row values are taken from the ``__source_row__`` column when
            present, falling back to the merged DataFrame positional index.
        """
        merged = self.df1.merge(
            self.df2,
            on=self.key_columns,
            how="inner",
            suffixes=("_file1", "_file2"),
        )

        differences = []
        value_columns = [col for col in self.df1.columns
                         if col not in self.key_columns and col not in self.ignore_columns
                         and col != '__source_row__']

        for idx, row in merged.iterrows():
            row_diffs = {}
            for col in value_columns:
                val1 = row.get(f"{col}_file1")
                val2 = row.get(f"{col}_file2")
                if val1 != val2:
                    row_diffs[col] = {"file1": val1, "file2": val2}

            if row_diffs:
                key_values = {k: row[k] for k in self.key_columns}
                source_row_file1 = row.get('__source_row___file1', row.get('__source_row__', idx + 1))
                source_row_file2 = row.get('__source_row___file2', row.get('__source_row__', idx + 1))
                differences.append({
                    "keys": key_values,
                    "differences": row_diffs,
                    "source_row_file1": source_row_file1,
                    "source_row_file2": source_row_file2,
                })

        return differences

    def _find_detailed_differences(self) -> List[Dict[str, Any]]:
        """Find rows with detailed field-level difference analysis.

        Returns:
            List of dicts, each containing ``keys``, ``differences``,
            ``difference_count``, ``source_row_file1``, and
            ``source_row_file2``.  Source row values come from the
            ``__source_row__`` column when present in the input DataFrames.
        """
        merged = self.df1.merge(
            self.df2,
            on=self.key_columns,
            how="inner",
            suffixes=("_file1", "_file2"),
        )

        differences = []
        value_columns = [col for col in self.df1.columns
                         if col not in self.key_columns and col not in self.ignore_columns
                         and col != '__source_row__']

        for idx, row in merged.iterrows():
            row_diffs = {}
            for col in value_columns:
                val1 = row.get(f"{col}_file1")
                val2 = row.get(f"{col}_file2")

                if val1 != val2:
                    diff_detail = self._analyze_field_difference(col, val1, val2)
                    row_diffs[col] = diff_detail

            if row_diffs:
                key_values = {k: row[k] for k in self.key_columns}
                source_row_file1 = row.get('__source_row___file1', row.get('__source_row__', idx + 1))
                source_row_file2 = row.get('__source_row___file2', row.get('__source_row__', idx + 1))
                differences.append({
                    "keys": key_values,
                    "differences": row_diffs,
                    "difference_count": len(row_diffs),
                    "source_row_file1": source_row_file1,
                    "source_row_file2": source_row_file2,
                })

        return differences

    def _analyze_field_difference(self, field_name: str, val1: Any, val2: Any) -> Dict[str, Any]:
        """Analyze difference between two field values."""
        result = {
            "file1": val1,
            "file2": val2,
            "type": self._get_difference_type(val1, val2),
        }
        
        if isinstance(val1, str) and isinstance(val2, str):
            result["string_analysis"] = self._analyze_string_difference(val1, val2)
        elif isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
            result["numeric_analysis"] = self._analyze_numeric_difference(val1, val2)
        
        return result

    def _get_difference_type(self, val1: Any, val2: Any) -> str:
        """Determine the type of difference."""
        if pd.isna(val1) and pd.isna(val2):
            return "both_null"
        elif pd.isna(val1):
            return "null_to_value"
        elif pd.isna(val2):
            return "value_to_null"
        elif type(val1) != type(val2):
            return "type_mismatch"
        else:
            return "value_difference"

    def _analyze_string_difference(self, str1: str, str2: str) -> Dict[str, Any]:
        """Analyze difference between two strings."""
        return {
            "length_diff": len(str2) - len(str1),
            "case_only": str1.lower() == str2.lower(),
            "whitespace_diff": str1.strip() == str2.strip(),
            "length_file1": len(str1),
            "length_file2": len(str2),
        }

    def _analyze_numeric_difference(self, num1: float, num2: float) -> Dict[str, Any]:
        """Analyze difference between two numbers."""
        diff = num2 - num1
        percent_change = (diff / num1 * 100) if num1 != 0 else float('inf')
        
        return {
            "absolute_difference": diff,
            "percent_change": percent_change,
            "sign_change": (num1 < 0) != (num2 < 0),
        }

    def _calculate_field_statistics(self, differences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate statistics about field differences."""
        field_counts = {}
        field_types = {}
        
        for diff in differences:
            for field, detail in diff["differences"].items():
                field_counts[field] = field_counts.get(field, 0) + 1
                
                diff_type = detail.get("type", "unknown")
                if field not in field_types:
                    field_types[field] = {}
                field_types[field][diff_type] = field_types[field].get(diff_type, 0) + 1
        
        sorted_fields = sorted(field_counts.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "fields_with_differences": len(field_counts),
            "field_difference_counts": dict(sorted_fields),
            "field_difference_types": field_types,
            "most_different_field": sorted_fields[0][0] if sorted_fields else None,
        }

    def get_summary(self) -> str:
        """Get human-readable comparison summary."""
        results = self.compare(detailed=True)
        
        summary = []
        summary.append("=" * 70)
        summary.append("FILE COMPARISON SUMMARY")
        summary.append("=" * 70)
        summary.append(f"Total Rows (File 1): {results['total_rows_file1']}")
        summary.append(f"Total Rows (File 2): {results['total_rows_file2']}")
        summary.append(f"Matching Rows: {results['matching_rows']}")
        summary.append(f"Only in File 1: {len(results['only_in_file1'])}")
        summary.append(f"Only in File 2: {len(results['only_in_file2'])}")
        summary.append(f"Rows with Differences: {results['rows_with_differences']}")
        summary.append("")
        
        if results['field_statistics']:
            stats = results['field_statistics']
            summary.append("FIELD-LEVEL STATISTICS:")
            summary.append(f"Fields with Differences: {stats['fields_with_differences']}")
            
            if stats['most_different_field']:
                summary.append(f"Most Different Field: {stats['most_different_field']}")
            
            summary.append("\nTop 5 Fields by Difference Count:")
            for field, count in list(stats['field_difference_counts'].items())[:5]:
                summary.append(f"  {field}: {count} differences")
        
        summary.append("=" * 70)
        return "\n".join(summary)
    
    def _compare_row_by_row(self, detailed: bool = True) -> Dict[str, Any]:
        """Compare files row-by-row (positional comparison).

        Args:
            detailed: Include detailed field-level differences.

        Returns:
            Dictionary containing comparison results, including
            ``source_row_file1`` and ``source_row_file2`` on every diff
            entry so users can trace differences back to the physical
            source file line numbers.
        """
        min_rows = min(len(self.df1), len(self.df2))

        differences = []
        matching_rows = 0

        # Compare rows that exist in both files
        for idx in range(min_rows):
            row1 = self.df1.iloc[idx]
            row2 = self.df2.iloc[idx]

            # Get columns to compare (excluding ignored columns and the
            # internal tracking column which is not a data field).
            compare_cols = [
                col for col in self.df1.columns
                if col not in self.ignore_columns
                and col != '__source_row__'
                and col in self.df2.columns
            ]

            row_diffs = {}
            for col in compare_cols:
                val1 = row1[col] if pd.notna(row1[col]) else ''
                val2 = row2[col] if pd.notna(row2[col]) else ''

                if str(val1) != str(val2):
                    if detailed:
                        row_diffs[col] = {
                            'file1': str(val1),
                            'file2': str(val2),
                            'type': 'value_difference'
                        }
                    else:
                        row_diffs[col] = {'file1': str(val1), 'file2': str(val2)}

            if row_diffs:
                source_row1 = int(row1.get('__source_row__', idx + 1))
                source_row2 = int(row2.get('__source_row__', idx + 1))
                diff_entry = {
                    'row_number': idx + 1,  # 1-indexed for user display
                    'source_row_file1': source_row1,
                    'source_row_file2': source_row2,
                    'differences': row_diffs,
                }
                if detailed:
                    diff_entry['difference_count'] = len(row_diffs)
                differences.append(diff_entry)
            else:
                matching_rows += 1

        # Rows only in file1 (if file1 is longer)
        only_in_file1 = []
        if len(self.df1) > len(self.df2):
            for idx in range(min_rows, len(self.df1)):
                row1 = self.df1.iloc[idx]
                source_row = int(row1.get('__source_row__', idx + 1))
                only_in_file1.append({'row_number': idx + 1, 'source_row': source_row})

        # Rows only in file2 (if file2 is longer)
        only_in_file2 = []
        if len(self.df2) > len(self.df1):
            for idx in range(min_rows, len(self.df2)):
                row2 = self.df2.iloc[idx]
                source_row = int(row2.get('__source_row__', idx + 1))
                only_in_file2.append({'row_number': idx + 1, 'source_row': source_row})

        field_diff_stats = self._calculate_field_statistics(differences) if detailed else {}

        return {
            "only_in_file1": only_in_file1,
            "only_in_file2": only_in_file2,
            "differences": differences,
            "total_rows_file1": len(self.df1),
            "total_rows_file2": len(self.df2),
            "matching_rows": matching_rows,
            "rows_with_differences": len(differences),
            "field_statistics": field_diff_stats,
            "comparison_mode": "row-by-row"
        }
