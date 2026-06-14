"""Execute queries against Oracle database."""

import oracledb
import pandas as pd
from typing import List, Dict, Any, Optional
from .connection import OracleConnection


class QueryExecutor:
    """Executes queries and returns results."""

    def __init__(self, connection: OracleConnection):
        """Initialize query executor.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection

    def execute_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Execute SELECT query and return results as DataFrame.
        
        Args:
            query: SQL query string
            params: Optional query parameters
            
        Returns:
            DataFrame containing query results
        """
        try:
            with self.connection as conn:
                df = pd.read_sql(query, conn, params=params)
            return df
        except oracledb.Error as e:
            raise RuntimeError(f"Query execution failed: {e}")

    def execute_many(self, query: str, data: List[tuple]) -> int:
        """Execute query with multiple parameter sets.
        
        Args:
            query: SQL query string (INSERT, UPDATE, DELETE)
            data: List of parameter tuples
            
        Returns:
            Number of rows affected
        """
        try:
            with self.connection as conn:
                cursor = conn.cursor()
                cursor.executemany(query, data)
                conn.commit()
                return cursor.rowcount
        except oracledb.Error as e:
            raise RuntimeError(f"Batch execution failed: {e}")

    def fetch_table_columns(self, table_name: str) -> List[str]:
        """Fetch column names for a table.
        
        Args:
            table_name: Name of the table
            
        Returns:
            List of column names
        """
        query = """
            SELECT column_name 
            FROM user_tab_columns 
            WHERE table_name = :table_name 
            ORDER BY column_id
        """
        df = self.execute_query(query, {"table_name": table_name.upper()})
        return df["COLUMN_NAME"].tolist()
