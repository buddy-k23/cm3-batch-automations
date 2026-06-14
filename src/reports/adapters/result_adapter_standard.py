"""Adapter for standard validation output.

Currently pass-through to keep a consistent adapter interface.
"""

from __future__ import annotations

from typing import Dict, Any


def adapt_standard_validation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return standard validator result as reporter model."""
    return result
