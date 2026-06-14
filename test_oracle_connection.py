#!/usr/bin/env python3
"""Test Oracle database connection."""

import os
import sys
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path)
    print(f"Loaded environment from: {env_path}")
except ImportError:
    print("Warning: python-dotenv not installed, using system environment variables only")
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

def test_oracle_connection():
    """Test Oracle connection using environment variables."""
    
    print("=" * 60)
    print("Oracle Connection Test")
    print("=" * 60)
    
    # Check environment variables
    print("\n1. Checking Environment Variables:")
    print("-" * 60)
    
    oracle_user = os.getenv("ORACLE_USER")
    oracle_password = os.getenv("ORACLE_PASSWORD")
    oracle_dsn = os.getenv("ORACLE_DSN")
    oracle_home = os.getenv("ORACLE_HOME")
    
    if oracle_user:
        print(f"✓ ORACLE_USER: {oracle_user}")
    else:
        print("✗ ORACLE_USER: Not set")
    
    if oracle_password:
        print(f"✓ ORACLE_PASSWORD: {'*' * len(oracle_password)} (hidden)")
    else:
        print("✗ ORACLE_PASSWORD: Not set")
    
    if oracle_dsn:
        print(f"✓ ORACLE_DSN: {oracle_dsn}")
    else:
        print("✗ ORACLE_DSN: Not set")
    
    if oracle_home:
        print(f"✓ ORACLE_HOME: {oracle_home}")
    else:
        print("⚠ ORACLE_HOME: Not set (may be optional)")
    
    # Check if all required variables are set
    if not all([oracle_user, oracle_password, oracle_dsn]):
        print("\n❌ ERROR: Missing required environment variables!")
        print("\nPlease ensure you have set:")
        print("  export ORACLE_USER=your_username")
        print("  export ORACLE_PASSWORD=your_password")
        print("  export ORACLE_DSN=hostname:port/service_name")
        print("\nOr reload your ~/.zshrc:")
        print("  source ~/.zshrc")
        return False
    
    # Check oracledb
    print("\n2. Checking oracledb Library:")
    print("-" * 60)
    
    try:
        import oracledb
        print(f"✓ oracledb version: {oracledb.__version__}")
        print(f"✓ Using thin mode (no Oracle Client required)")
    except ImportError as e:
        print(f"✗ oracledb not installed: {e}")
        print("\nInstall with: pip install oracledb")
        return False
    
    # Test connection
    print("\n3. Testing Database Connection:")
    print("-" * 60)
    
    try:
        from src.database.connection import OracleConnection
        
        print(f"Connecting to: {oracle_dsn}")
        print(f"As user: {oracle_user}")
        
        conn = OracleConnection.from_env()
        connection = conn.connect()
        
        print("✓ Connection successful!")
        
        # Test a simple query
        cursor = connection.cursor()
        cursor.execute("SELECT 'Hello from Oracle!' as message FROM DUAL")
        result = cursor.fetchone()
        print(f"✓ Query test: {result[0]}")
        cursor.close()
        
        conn.disconnect()
        print("✓ Disconnected successfully")
        
        print("\n" + "=" * 60)
        print("✅ All tests passed! Oracle connection is working.")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\nPossible issues:")
        print("  - Check your credentials are correct")
        print("  - Verify the DSN format: hostname:port/service_name")
        print("  - Ensure the database is accessible from your network")
        print("  - Check firewall settings")
        return False

if __name__ == "__main__":
    success = test_oracle_connection()
    sys.exit(0 if success else 1)
