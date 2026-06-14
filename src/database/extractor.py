"""Database data extraction utilities."""

import pandas as pd
from typing import Optional, List, Dict, Any
import oracledb
from .connection import OracleConnection
from .query_executor import QueryExecutor


class DataExtractor:
    """Extract data from Oracle database."""

    def __init__(self, connection: OracleConnection):
        """Initialize data extractor.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection
        self.executor = QueryExecutor(connection)

    def extract_table(self, table_name: str, columns: Optional[List[str]] = None,
                     where_clause: Optional[str] = None, limit: Optional[int] = None) -> pd.DataFrame:
        """Extract data from a table.
        
        Args:
            table_name: Name of the table
            columns: List of columns to extract (None = all)
            where_clause: Optional WHERE clause (without 'WHERE' keyword)
            limit: Optional row limit
            
        Returns:
            DataFrame with extracted data
        """
        # Build column list
        if columns:
            col_list = ', '.join(columns)
        else:
            col_list = '*'

        # Build query
        query = f"SELECT {col_list} FROM {table_name}"
        
        if where_clause:
            query += f" WHERE {where_clause}"
        
        if limit:
            query += f" AND ROWNUM <= {limit}" if where_clause else f" WHERE ROWNUM <= {limit}"

        return self.executor.execute_query(query)

    def extract_sample(self, table_name: str, sample_size: int = 1000,
                      columns: Optional[List[str]] = None) -> pd.DataFrame:
        """Extract a sample of data from a table.
        
        Args:
            table_name: Name of the table
            sample_size: Number of rows to sample
            columns: List of columns to extract
            
        Returns:
            DataFrame with sampled data
        """
        return self.extract_table(table_name, columns=columns, limit=sample_size)

    def extract_by_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Extract data using custom query.
        
        Args:
            query: SQL query
            params: Optional query parameters
            
        Returns:
            DataFrame with query results
        """
        return self.executor.execute_query(query, params)

    def extract_to_file(self, table_name: Optional[str] = None, output_file: str = None,
                       columns: Optional[List[str]] = None,
                       where_clause: Optional[str] = None,
                       delimiter: str = '|',
                       chunk_size: int = 10000,
                       query: Optional[str] = None) -> Dict[str, Any]:
        """Extract data to file in chunks.
        
        Args:
            table_name: Name of the table (optional if query is provided)
            output_file: Output file path
            columns: List of columns to extract (ignored if query is provided)
            where_clause: Optional WHERE clause (ignored if query is provided)
            delimiter: File delimiter
            chunk_size: Number of rows per chunk
            query: Optional raw SQL query (overrides table_name/columns/where_clause)
            
        Returns:
            Dictionary with extraction statistics
        """
        # Build query
        if query:
            # Use custom SQL query directly
            sql_query = query
        elif table_name:
            # Build table-based query
            if columns:
                col_list = ', '.join(columns)
            else:
                col_list = '*'

            sql_query = f"SELECT {col_list} FROM {table_name}"
            if where_clause:
                sql_query += f" WHERE {where_clause}"
        else:
            raise ValueError("Either 'table_name' or 'query' must be provided")

        total_rows = 0
        chunks_written = 0

        try:
            with self.connection as conn:
                cursor = conn.cursor()
                cursor.execute(sql_query)
                
                # Get column names
                col_names = [desc[0] for desc in cursor.description]
                
                # Write header
                with open(output_file, 'w') as f:
                    f.write(delimiter.join(col_names) + '\n')
                    
                    # Write data in chunks
                    while True:
                        rows = cursor.fetchmany(chunk_size)
                        if not rows:
                            break
                        
                        for row in rows:
                            f.write(delimiter.join(str(val) if val is not None else '' 
                                                  for val in row) + '\n')
                            total_rows += 1
                        
                        chunks_written += 1

                cursor.close()

        except oracledb.Error as e:
            raise RuntimeError(f"Data extraction failed: {e}")

        return {
            'output_file': output_file,
            'total_rows': total_rows,
            'chunks_written': chunks_written,
            'chunk_size': chunk_size,
            'query': sql_query,
        }

    def get_table_stats(self, table_name: str) -> Dict[str, Any]:
        """Get statistics about a table.
        
        Args:
            table_name: Name of the table
            
        Returns:
            Dictionary with table statistics
        """
        # Get row count
        count_query = f"SELECT COUNT(*) as row_count FROM {table_name}"
        count_df = self.executor.execute_query(count_query)
        row_count = count_df['ROW_COUNT'].iloc[0]

        # Get column info
        columns = self.executor.fetch_table_columns(table_name)

        # Get table size (approximate)
        size_query = """
            SELECT 
                segment_name,
                SUM(bytes)/1024/1024 as size_mb
            FROM user_segments
            WHERE segment_name = :table_name
            GROUP BY segment_name
        """
        size_df = self.executor.execute_query(size_query, {'table_name': table_name.upper()})
        size_mb = size_df['SIZE_MB'].iloc[0] if not size_df.empty else 0

        return {
            'table_name': table_name,
            'row_count': int(row_count),
            'column_count': len(columns),
            'columns': columns,
            'size_mb': float(size_mb),
        }

    def compare_tables(self, table1: str, table2: str, key_columns: List[str]) -> Dict[str, Any]:
        """Compare two tables and return differences.
        
        Args:
            table1: First table name
            table2: Second table name
            key_columns: Columns to use as keys for comparison
            
        Returns:
            Dictionary with comparison results
        """
        # Extract both tables
        df1 = self.extract_table(table1)
        df2 = self.extract_table(table2)

        # Use FileComparator
        from ..comparators.file_comparator import FileComparator
        comparator = FileComparator(df1, df2, key_columns)
        
        return comparator.compare()


class BulkExtractor:
    """Bulk data extraction with parallel processing support."""

    def __init__(self, connection: OracleConnection):
        """Initialize bulk extractor.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection
        self.extractor = DataExtractor(connection)

    def extract_multiple_tables(self, tables: List[str], output_dir: str,
                               delimiter: str = '|') -> Dict[str, Any]:
        """Extract multiple tables to files.
        
        Args:
            tables: List of table names
            output_dir: Output directory
            delimiter: File delimiter
            
        Returns:
            Dictionary with extraction results
        """
        import os
        
        os.makedirs(output_dir, exist_ok=True)
        
        results = {}
        total_rows = 0
        
        for table in tables:
            output_file = os.path.join(output_dir, f"{table}.txt")
            try:
                result = self.extractor.extract_to_file(
                    table, output_file, delimiter=delimiter
                )
                results[table] = {
                    'status': 'success',
                    'output_file': output_file,
                    'rows': result['total_rows'],
                }
                total_rows += result['total_rows']
            except Exception as e:
                results[table] = {
                    'status': 'failed',
                    'error': str(e),
                }

        return {
            'tables_processed': len(tables),
            'tables_succeeded': sum(1 for r in results.values() if r['status'] == 'success'),
            'tables_failed': sum(1 for r in results.values() if r['status'] == 'failed'),
            'total_rows': total_rows,
            'results': results,
        }
