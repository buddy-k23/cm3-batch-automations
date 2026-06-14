"""Test database fetch."""
from dotenv import load_dotenv
load_dotenv()

from src.services.run_history_service import fetch_history_from_db
import json

print("Fetching from database...")
results = fetch_history_from_db(limit=20)
print(f"Found {len(results)} records")
print(json.dumps(results, indent=2, default=str))
