"""Unit tests for transaction management."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.database.transaction import (
    TransactionManager,
    IsolatedTestTransaction,
    BatchTransactionManager,
)
from src.database.connection import OracleConnection


class TestTransactionManager:
    """Test TransactionManager class."""

    @patch('src.database.connection.cx_Oracle')
    def test_transaction_commit(self, mock_cx_oracle):
        """Test successful transaction with commit."""
        mock_conn = MagicMock()
        mock_cx_oracle.connect.return_value = mock_conn
        
        connection = OracleConnection('user', 'pass', 'dsn')
        tm = TransactionManager(connection)
        
        with tm.transaction():
            pass  # Simulate successful operation
        
        mock_conn.commit.assert_called_once()

    @patch('src.database.connection.cx_Oracle')
    def test_transaction_rollback_on_error(self, mock_cx_oracle):
        """Test transaction rollback on error."""
        mock_conn = MagicMock()
        mock_cx_oracle.connect.return_value = mock_conn
        
        connection = OracleConnection('user', 'pass', 'dsn')
        tm = TransactionManager(connection)
        
        with pytest.raises(ValueError):
            with tm.transaction():
                raise ValueError("Test error")
        
        mock_conn.rollback.assert_called_once()

    @patch('src.database.connection.cx_Oracle')
    def test_savepoint_creation(self, mock_cx_oracle):
        """Test savepoint creation."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        
        connection = OracleConnection('user', 'pass', 'dsn')
        connection.connection = mock_conn
        
        tm = TransactionManager(connection)
        tm.create_savepoint('test_savepoint')
        
        mock_cursor.execute.assert_called_with('SAVEPOINT test_savepoint')


class TestIsolatedTestTransaction:
    """Test IsolatedTestTransaction class."""

    @patch('src.database.connection.cx_Oracle')
    def test_isolated_transaction_rollback(self, mock_cx_oracle):
        """Test that isolated transaction always rolls back."""
        mock_conn = MagicMock()
        mock_conn.autocommit = 1
        mock_cx_oracle.connect.return_value = mock_conn
        
        connection = OracleConnection('user', 'pass', 'dsn')
        itt = IsolatedTestTransaction(connection)
        
        with itt as conn:
            pass  # Simulate test operations
        
        mock_conn.rollback.assert_called_once()
