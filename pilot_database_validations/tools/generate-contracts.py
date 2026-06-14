#!/usr/bin/env python3
"""Compatibility wrapper for contract generator CLI.

Use `tools/generate_contracts.py` for importable module implementation.
"""

from pathlib import Path
import sys

# Ensure sibling module is importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_contracts import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
