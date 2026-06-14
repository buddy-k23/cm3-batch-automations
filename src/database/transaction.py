"""Transaction management for database operations."""

import oracledb
from typing import Optional, Callable, Any
from contextlib import contextmanager
from .connection import OracleConnection
from ..utils.logger import get_logger


class TransactionManager:
    """Manages database transactions with rollback support."""

    def __init__(self, connection: OracleConnection):
        """Initialize transaction manager.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection
        self.logger = get_logger(__name__)
        self._savepoints = []

    @contextmanager
    def transaction(self, auto_commit: bool = True, auto_rollback: bool = True):
        """Context manager for transaction handling.
        
        Args:
            auto_commit: Automatically commit on success
            auto_rollback: Automatically rollback on error
            
        Yields:
            Database connection
            
        Example:
            with transaction_manager.transaction():
                cursor.execute("INSERT ...")
                cursor.execute("UPDATE ...")
            # Auto-commits on success, auto-rolls back on error
        """
        conn = None
        try:
            conn = self.connection.connect()
            self.logger.debug("Transaction started")
            
            yield conn
            
            if auto_commit:
                conn.commit()
                self.logger.debug("Transaction committed")
        except Exception as e:
            if conn and auto_rollback:
                conn.rollback()
                self.logger.warning(f"Transaction rolled back due to error: {e}")
            raise
        finally:
            if conn:
                self.connection.disconnect()

    def create_savepoint(self, savepoint_name: str) -> None:
        """Create a savepoint within a transaction.
        
        Args:
            savepoint_name: Name of the savepoint
        """
        if not self.connection.connection:
            raise RuntimeError("No active connection")
        
        cursor = self.connection.connection.cursor()
        cursor.execute(f"SAVEPOINT {savepoint_name}")
        self._savepoints.append(savepoint_name)
        self.logger.debug(f"Savepoint created: {savepoint_name}")

    def rollback_to_savepoint(self, savepoint_name: str) -> None:
        """Rollback to a specific savepoint.
        
        Args:
            savepoint_name: Name of the savepoint
        """
        if not self.connection.connection:
            raise RuntimeError("No active connection")
        
        if savepoint_name not in self._savepoints:
            raise ValueError(f"Savepoint not found: {savepoint_name}")
        
        cursor = self.connection.connection.cursor()
        cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        
        # Remove savepoints after the rolled back one
        idx = self._savepoints.index(savepoint_name)
        self._savepoints = self._savepoints[:idx + 1]
        
        self.logger.debug(f"Rolled back to savepoint: {savepoint_name}")

    def release_savepoint(self, savepoint_name: str) -> None:
        """Release a savepoint (Oracle doesn't support this, but we track it).
        
        Args:
            savepoint_name: Name of the savepoint
        """
        if savepoint_name in self._savepoints:
            self._savepoints.remove(savepoint_name)
            self.logger.debug(f"Savepoint released: {savepoint_name}")

    def execute_in_transaction(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function within a transaction.
        
        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function
            
        Returns:
            Function return value
            
        Example:
            def insert_data(conn, data):
                cursor = conn.cursor()
                cursor.execute("INSERT ...", data)
                return cursor.rowcount
            
            rows = tm.execute_in_transaction(insert_data, data={'id': 1})
        """
        with self.transaction() as conn:
            return func(conn, *args, **kwargs)


class IsolatedTestTransaction:
    """Manages isolated transactions for testing.
    
    Creates a transaction that can be rolled back after tests,
    ensuring no permanent changes to the database.
    """

    def __init__(self, connection: OracleConnection):
        """Initialize isolated test transaction.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection
        self.logger = get_logger(__name__)
        self._conn = None
        self._original_autocommit = None

    def __enter__(self):
        """Start isolated transaction."""
        self._conn = self.connection.connect()
        
        # Disable autocommit
        self._original_autocommit = self._conn.autocommit
        self._conn.autocommit = 0
        
        self.logger.info("Isolated test transaction started")
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        """End isolated transaction with rollback."""
        if self._conn:
            # Always rollback to ensure no changes persist
            self._conn.rollback()
            self.logger.info("Isolated test transaction rolled back")
            
            # Restore autocommit
            if self._original_autocommit is not None:
                self._conn.autocommit = self._original_autocommit
            
            self.connection.disconnect()
        
        # Don't suppress exceptions
        return False


class BatchTransactionManager:
    """Manages batch operations with transaction support."""

    def __init__(self, connection: OracleConnection, batch_size: int = 1000):
        """Initialize batch transaction manager.
        
        Args:
            connection: OracleConnection instance
            batch_size: Number of operations per batch
        """
        self.connection = connection
        self.batch_size = batch_size
        self.logger = get_logger(__name__)
        self.transaction_manager = TransactionManager(connection)

    def execute_batch(self, operations: list, commit_per_batch: bool = True) -> dict:
        """Execute operations in batches with transaction management.
        
        Args:
            operations: List of (query, params) tuples
            commit_per_batch: Commit after each batch
            
        Returns:
            Dictionary with execution statistics
            
        Example:
            operations = [
                ("INSERT INTO table VALUES (:1, :2)", (1, 'a')),
                ("INSERT INTO table VALUES (:1, :2)", (2, 'b')),
            ]
            stats = batch_manager.execute_batch(operations)
        """
        total_operations = len(operations)
        successful = 0
        failed = 0
        batches = 0
        
        try:
            with self.transaction_manager.transaction(auto_commit=False) as conn:
                cursor = conn.cursor()
                
                for i in range(0, total_operations, self.batch_size):
                    batch = operations[i:i + self.batch_size]
                    batches += 1
                    
                    # Create savepoint for this batch
                    savepoint_name = f"batch_{batches}"
                    self.transaction_manager.create_savepoint(savepoint_name)
                    
                    try:
                        for query, params in batch:
                            cursor.execute(query, params)
                            successful += 1
                        
                        if commit_per_batch:
                            conn.commit()
                            self.logger.debug(f"Batch {batches} committed ({len(batch)} operations)")
                    
                    except oracledb.Error as e:
                        # Rollback this batch
                        self.transaction_manager.rollback_to_savepoint(savepoint_name)
                        failed += len(batch)
                        self.logger.error(f"Batch {batches} failed: {e}")
                        
                        if not commit_per_batch:
                            # If not committing per batch, fail entire operation
                            raise
                
                # Final commit if not committing per batch
                if not commit_per_batch:
                    conn.commit()
                    self.logger.info(f"All batches committed ({batches} batches)")
        
        except Exception as e:
            self.logger.error(f"Batch execution failed: {e}")
            raise
        
        return {
            'total_operations': total_operations,
            'successful': successful,
            'failed': failed,
            'batches': batches,
            'batch_size': self.batch_size,
        }


class TransactionLogger:
    """Logs transaction operations for audit trail."""

    def __init__(self, connection: OracleConnection, log_table: str = "TRANSACTION_LOG"):
        """Initialize transaction logger.
        
        Args:
            connection: OracleConnection instance
            log_table: Name of the log table
        """
        self.connection = connection
        self.log_table = log_table
        self.logger = get_logger(__name__)

    def log_transaction(self, operation: str, table_name: str, 
                       row_count: int, status: str, details: Optional[str] = None) -> None:
        """Log a transaction operation.
        
        Args:
            operation: Operation type (INSERT, UPDATE, DELETE, etc.)
            table_name: Target table name
            row_count: Number of rows affected
            status: Status (SUCCESS, FAILED, ROLLED_BACK)
            details: Optional details or error message
        """
        try:
            with self.connection as conn:
                cursor = conn.cursor()
                
                query = f"""
                    INSERT INTO {self.log_table} 
                    (operation, table_name, row_count, status, details, log_timestamp)
                    VALUES (:operation, :table_name, :row_count, :status, :details, SYSTIMESTAMP)
                """
                
                cursor.execute(query, {
                    'operation': operation,
                    'table_name': table_name,
                    'row_count': row_count,
                    'status': status,
                    'details': details
                })
                
                conn.commit()
                self.logger.debug(f"Transaction logged: {operation} on {table_name}")
        
        except oracledb.Error as e:
            self.logger.error(f"Failed to log transaction: {e}")
            # Don't raise - logging failure shouldn't break the main operation

    def create_log_table(self) -> None:
        """Create the transaction log table if it doesn't exist."""
        try:
            with self.connection as conn:
                cursor = conn.cursor()
                
                create_table_sql = f"""
                    CREATE TABLE {self.log_table} (
                        log_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        operation VARCHAR2(50) NOT NULL,
                        table_name VARCHAR2(100) NOT NULL,
                        row_count NUMBER,
                        status VARCHAR2(20) NOT NULL,
                        details VARCHAR2(4000),
                        log_timestamp TIMESTAMP DEFAULT SYSTIMESTAMP
                    )
                """
                
                cursor.execute(create_table_sql)
                conn.commit()
                self.logger.info(f"Transaction log table created: {self.log_table}")
        
        except oracledb.Error as e:
            if "ORA-00955" in str(e):  # Table already exists
                self.logger.debug(f"Transaction log table already exists: {self.log_table}")
            else:
                self.logger.error(f"Failed to create log table: {e}")
                raise
