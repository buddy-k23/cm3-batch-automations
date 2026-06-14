"""Chunked file parser for memory-efficient large file processing."""

import pandas as pd
from typing import Iterator, Optional, List, Callable, Dict, Any
from pathlib import Path
import time
from ..utils.logger import get_logger


class ChunkedFileParser:
    """Parse large files in chunks to minimize memory usage."""
    
    def __init__(self, file_path: str, delimiter: str = '|',
                 chunk_size: int = 100000, encoding: str = 'utf-8',
                 has_header: bool = True):
        """Initialize chunked parser.

        Args:
            file_path: Path to file to parse
            delimiter: Field delimiter
            chunk_size: Number of rows per chunk
            encoding: File encoding
            has_header: Whether the file contains a header row. When False,
                pandas reads all rows as data and column names are auto-assigned
                (0, 1, …) unless columns are supplied to parse_chunks.
        """
        self.file_path = file_path
        self.delimiter = delimiter
        self.chunk_size = chunk_size
        self.encoding = encoding
        self.has_header = has_header
        self.logger = get_logger(__name__)
        
    def parse_chunks(self, columns: Optional[List[str]] = None) -> Iterator[pd.DataFrame]:
        """Parse file in chunks.
        
        Args:
            columns: Optional column names
            
        Yields:
            DataFrame chunks
        """
        try:
            # When the file has no header row, use header=None so pandas treats
            # all lines as data.  header=0 is the pandas default (row 0 as
            # column names); header=None means "no header, auto-number columns".
            header: Optional[int] = 0 if self.has_header else None
            for chunk in pd.read_csv(
                self.file_path,
                sep=self.delimiter,
                names=columns,
                header=header,
                dtype=str,
                keep_default_na=False,
                chunksize=self.chunk_size,
                encoding=self.encoding,
                engine='c'  # Faster C engine
            ):
                yield chunk
                
        except Exception as e:
            self.logger.error(f"Error parsing file in chunks: {e}")
            raise ValueError(f"Failed to parse file: {e}")
    
    def parse_with_progress(self, 
                           columns: Optional[List[str]] = None,
                           progress_callback: Optional[Callable[[int, int], None]] = None) -> Iterator[pd.DataFrame]:
        """Parse file with progress tracking.
        
        Args:
            columns: Optional column names
            progress_callback: Callback function(current_rows, total_rows)
            
        Yields:
            DataFrame chunks with progress updates
        """
        total_rows = self.count_rows()
        processed_rows = 0
        start_time = time.time()
        
        self.logger.info(f"Starting chunked parsing: {total_rows:,} total rows, "
                        f"chunk size: {self.chunk_size:,}")
        
        for chunk_num, chunk in enumerate(self.parse_chunks(columns), 1):
            processed_rows += len(chunk)
            
            if progress_callback:
                progress_callback(processed_rows, total_rows)
            
            # Log progress every 10 chunks
            if chunk_num % 10 == 0:
                elapsed = time.time() - start_time
                rate = processed_rows / elapsed if elapsed > 0 else 0
                self.logger.info(
                    f"Processed chunk {chunk_num}: {processed_rows:,}/{total_rows:,} rows "
                    f"({processed_rows/total_rows*100:.1f}%) at {rate:.0f} rows/sec"
                )
            
            yield chunk
        
        elapsed = time.time() - start_time
        self.logger.info(
            f"Parsing complete: {processed_rows:,} rows in {elapsed:.1f}s "
            f"({processed_rows/elapsed:.0f} rows/sec)"
        )
    
    def count_rows(self) -> int:
        """Count total rows in file.
        
        Returns:
            Total number of rows
        """
        try:
            # Fast line counting
            with open(self.file_path, 'rb') as f:
                return sum(1 for _ in f)
        except Exception as e:
            self.logger.warning(f"Could not count rows: {e}")
            return 0
    
    def get_file_info(self) -> Dict[str, Any]:
        """Get file information.
        
        Returns:
            Dictionary with file metadata
        """
        path = Path(self.file_path)
        size_bytes = path.stat().st_size
        total_rows = self.count_rows()
        
        return {
            'file_path': str(path.absolute()),
            'file_name': path.name,
            'size_bytes': size_bytes,
            'size_mb': size_bytes / (1024 * 1024),
            'total_rows': total_rows,
            'chunk_size': self.chunk_size,
            'estimated_chunks': (total_rows // self.chunk_size) + 1,
            'delimiter': self.delimiter,
            'encoding': self.encoding
        }
    
    def parse_sample(self, n_rows: int = 1000) -> pd.DataFrame:
        """Parse sample of file.
        
        Args:
            n_rows: Number of rows to sample
            
        Returns:
            DataFrame with sample rows
        """
        try:
            return pd.read_csv(
                self.file_path,
                sep=self.delimiter,
                dtype=str,
                keep_default_na=False,
                nrows=n_rows,
                encoding=self.encoding
            )
        except Exception as e:
            self.logger.error(f"Error parsing sample: {e}")
            raise ValueError(f"Failed to parse sample: {e}")
    
    def validate_structure(self) -> Dict[str, Any]:
        """Validate file structure.
        
        Returns:
            Validation results
        """
        errors = []
        warnings = []
        
        # Check file exists
        if not Path(self.file_path).exists():
            errors.append(f"File not found: {self.file_path}")
            return {'valid': False, 'errors': errors, 'warnings': warnings}
        
        # Check file size
        size_mb = Path(self.file_path).stat().st_size / (1024 * 1024)
        if size_mb == 0:
            errors.append("File is empty")
        elif size_mb > 1000:  # >1GB
            warnings.append(f"Large file detected: {size_mb:.1f} MB")
        
        # Try parsing first chunk
        try:
            first_chunk = next(self.parse_chunks())
            if first_chunk.empty:
                warnings.append("First chunk is empty")
        except Exception as e:
            errors.append(f"Failed to parse first chunk: {e}")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'file_info': self.get_file_info()
        }


class ChunkedFixedWidthParser(ChunkedFileParser):
    """Parse fixed-width files in chunks."""
    
    def __init__(self, file_path: str, field_specs: List[tuple],
                 chunk_size: int = 100000, encoding: str = 'utf-8'):
        """Initialize fixed-width parser.
        
        Args:
            file_path: Path to file
            field_specs: List of (name, start, end) tuples
            chunk_size: Rows per chunk
            encoding: File encoding
        """
        super().__init__(file_path, delimiter='', chunk_size=chunk_size, encoding=encoding)
        self.field_specs = field_specs
        self.colspecs = [(start, end) for _, start, end in field_specs]
        self.names = [name for name, _, _ in field_specs]
    
    def parse_chunks(self, columns: Optional[List[str]] = None) -> Iterator[pd.DataFrame]:
        """Parse fixed-width file in chunks.
        
        Args:
            columns: Ignored for fixed-width (uses field_specs)
            
        Yields:
            DataFrame chunks
        """
        try:
            for chunk in pd.read_fwf(
                self.file_path,
                colspecs=self.colspecs,
                names=self.names,
                dtype=str,
                chunksize=self.chunk_size,
                encoding=self.encoding
            ):
                yield chunk
                
        except Exception as e:
            self.logger.error(f"Error parsing fixed-width file: {e}")
            raise ValueError(f"Failed to parse fixed-width file: {e}")

    def parse_sample(self, n_rows: int = 1000) -> pd.DataFrame:
        """Parse sample of fixed-width file."""
        try:
            return pd.read_fwf(
                self.file_path,
                colspecs=self.colspecs,
                names=self.names,
                dtype=str,
                nrows=n_rows,
                encoding=self.encoding
            )
        except Exception as e:
            self.logger.error(f"Error parsing fixed-width sample: {e}")
            raise ValueError(f"Failed to parse fixed-width sample: {e}")
