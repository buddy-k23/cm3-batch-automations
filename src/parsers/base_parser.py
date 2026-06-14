"""Base parser interface for file parsing."""

from abc import ABC, abstractmethod
from typing import Any, Dict
import pandas as pd


class BaseParser(ABC):
    """Abstract base class for file parsers."""

    def __init__(self, file_path: str):
        """Initialize parser with file path.
        
        Args:
            file_path: Path to the file to parse
        """
        self.file_path = file_path

    @abstractmethod
    def parse(self) -> pd.DataFrame:
        """Parse the file and return a DataFrame.
        
        Returns:
            DataFrame containing parsed data
        """
        pass

    @abstractmethod
    def validate_format(self) -> bool:
        """Validate the file format.
        
        Returns:
            True if format is valid, False otherwise
        """
        pass

    def get_metadata(self) -> Dict[str, Any]:
        """Get file metadata.
        
        Returns:
            Dictionary containing file metadata
        """
        import os
        from datetime import datetime
        
        stat = os.stat(self.file_path)
        return {
            "file_path": self.file_path,
            "file_size": stat.st_size,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
