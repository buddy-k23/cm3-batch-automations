"""Validate file-to-database column mappings."""

from typing import Dict, List
import pandas as pd


class MappingValidator:
    """Validates column mappings between files and database."""

    def __init__(self, mapping: Dict[str, str]):
        """Initialize mapping validator.
        
        Args:
            mapping: Dictionary mapping file columns to database columns
        """
        self.mapping = mapping

    def validate_file_columns(self, df: pd.DataFrame) -> List[str]:
        """Validate that all mapped file columns exist in DataFrame.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            List of missing columns
        """
        file_columns = set(self.mapping.keys())
        df_columns = set(df.columns)
        missing = file_columns - df_columns
        return list(missing)

    def validate_db_columns(self, db_columns: List[str]) -> List[str]:
        """Validate that all mapped database columns exist.
        
        Args:
            db_columns: List of database column names
            
        Returns:
            List of missing columns
        """
        mapped_db_columns = set(self.mapping.values())
        available_columns = set(db_columns)
        missing = mapped_db_columns - available_columns
        return list(missing)

    def get_unmapped_columns(self, df: pd.DataFrame) -> List[str]:
        """Get file columns that are not mapped.
        
        Args:
            df: DataFrame to check
            
        Returns:
            List of unmapped columns
        """
        mapped_columns = set(self.mapping.keys())
        df_columns = set(df.columns)
        unmapped = df_columns - mapped_columns
        return list(unmapped)

    def apply_mapping(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply column mapping to DataFrame.
        
        Args:
            df: DataFrame to transform
            
        Returns:
            DataFrame with renamed columns
        """
        missing = self.validate_file_columns(df)
        if missing:
            raise ValueError(f"Missing columns in file: {missing}")
        
        mapped_df = df[list(self.mapping.keys())].rename(columns=self.mapping)
        return mapped_df
