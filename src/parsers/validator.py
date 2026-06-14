"""File validation utilities."""

import os
from typing import List, Dict, Any, Optional
import pandas as pd
from .base_parser import BaseParser


class FileValidator:
    """Validates file structure and content."""

    def __init__(self, parser: BaseParser):
        """Initialize validator.
        
        Args:
            parser: Parser instance to use for validation
        """
        self.parser = parser
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self) -> Dict[str, Any]:
        """Perform comprehensive file validation.
        
        Returns:
            Validation results dictionary
        """
        self.errors = []
        self.warnings = []

        # Check file exists
        if not self._validate_file_exists():
            return self._build_result(False)

        # Check file size
        self._validate_file_size()

        # Check file format
        if not self._validate_format():
            return self._build_result(False)

        # Try parsing
        try:
            df = self.parser.parse()
            self._validate_dataframe(df)
        except Exception as e:
            self.errors.append(f"Parse error: {str(e)}")
            return self._build_result(False)

        return self._build_result(len(self.errors) == 0)

    def _validate_file_exists(self) -> bool:
        """Check if file exists."""
        if not os.path.exists(self.parser.file_path):
            self.errors.append(f"File not found: {self.parser.file_path}")
            return False
        return True

    def _validate_file_size(self) -> None:
        """Check file size."""
        size = os.path.getsize(self.parser.file_path)
        
        if size == 0:
            self.errors.append("File is empty")
        elif size < 10:
            self.warnings.append("File is very small (< 10 bytes)")
        elif size > 1024 * 1024 * 1024:  # 1GB
            self.warnings.append(f"Large file detected ({size / (1024**3):.2f} GB)")

    def _validate_format(self) -> bool:
        """Validate file format."""
        try:
            if not self.parser.validate_format():
                self.errors.append("Invalid file format")
                return False
            return True
        except Exception as e:
            self.errors.append(f"Format validation error: {str(e)}")
            return False

    def _validate_dataframe(self, df: pd.DataFrame) -> None:
        """Validate parsed DataFrame."""
        # Check if empty
        if df.empty:
            self.warnings.append("DataFrame is empty (no rows)")
            return

        # Check for duplicate rows
        duplicates = df.duplicated().sum()
        if duplicates > 0:
            self.warnings.append(f"Found {duplicates} duplicate rows")

        # Check for null values
        null_counts = df.isnull().sum()
        null_columns = null_counts[null_counts > 0]
        if not null_columns.empty:
            for col, count in null_columns.items():
                pct = (count / len(df)) * 100
                self.warnings.append(
                    f"Column '{col}' has {count} null values ({pct:.1f}%)"
                )

        # Check for empty strings
        for col in df.columns:
            if df[col].dtype == 'object':
                empty_count = (df[col] == '').sum()
                if empty_count > 0:
                    pct = (empty_count / len(df)) * 100
                    self.warnings.append(
                        f"Column '{col}' has {empty_count} empty strings ({pct:.1f}%)"
                    )

    def _build_result(self, is_valid: bool) -> Dict[str, Any]:
        """Build validation result."""
        return {
            'valid': is_valid,
            'errors': self.errors,
            'warnings': self.warnings,
            'file_path': self.parser.file_path,
        }


class SchemaValidator:
    """Validates DataFrame against expected schema."""

    def __init__(self, expected_columns: List[str], required_columns: Optional[List[str]] = None):
        """Initialize schema validator.
        
        Args:
            expected_columns: List of expected column names
            required_columns: List of required column names (subset of expected)
        """
        self.expected_columns = expected_columns
        # By default, treat all expected columns as optional unless explicitly required.
        self.required_columns = required_columns or []

    def validate(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate DataFrame schema.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Validation results
        """
        errors = []
        warnings = []

        actual_columns = set(df.columns)
        expected_set = set(self.expected_columns)
        required_set = set(self.required_columns)

        # Check required columns
        missing_required = required_set - actual_columns
        if missing_required:
            errors.append(f"Missing required columns: {sorted(missing_required)}")

        # Check for unexpected columns
        unexpected = actual_columns - expected_set
        if unexpected:
            warnings.append(f"Unexpected columns: {sorted(unexpected)}")

        # Check for missing optional columns
        missing_optional = expected_set - required_set - actual_columns
        if missing_optional:
            warnings.append(f"Missing optional columns: {sorted(missing_optional)}")

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'expected_columns': self.expected_columns,
            'actual_columns': list(df.columns),
            'missing_required': list(missing_required),
            'unexpected': list(unexpected),
        }
