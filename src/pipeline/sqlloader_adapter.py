"""SQL*Loader adapter helpers for pipeline stage evaluation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Any


def parse_sqlloader_log(log_path: str) -> Dict[str, int]:
    """Parse key SQL*Loader counters from a log file.

    Returns metrics with defaults when patterns are absent.
    """
    text = Path(log_path).read_text(encoding='utf-8', errors='ignore')

    def _num(pattern: str) -> int:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        return int(m.group(1)) if m else 0

    return {
        'rows_loaded': _num(r'(\d+)\s+Rows?\s+successfully\s+loaded'),
        'rows_rejected': _num(r'(\d+)\s+Rows?\s+not\s+loaded\s+due\s+to\s+data\s+errors'),
        'rows_discarded': _num(r'(\d+)\s+Rows?\s+not\s+loaded\s+because\s+all\s+when\s+clauses\s+were\s+failed'),
    }


def evaluate_sqlloader_stage(config: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate SQL*Loader stage using log and optional thresholds.

    Config keys:
    - log_file (required)
    - max_rejected (optional, default 0)
    - max_discarded (optional, default 0)
    """
    log_file = config.get('log_file')
    if not log_file:
        return {'status': 'failed', 'message': 'sqlloader.log_file is required', 'metrics': {}}

    p = Path(log_file)
    if not p.exists():
        return {'status': 'failed', 'message': f'log file not found: {log_file}', 'metrics': {}}

    metrics = parse_sqlloader_log(log_file)
    max_rejected = int(config.get('max_rejected', 0))
    max_discarded = int(config.get('max_discarded', 0))

    if metrics['rows_rejected'] > max_rejected:
        return {
            'status': 'failed',
            'message': f"rows_rejected {metrics['rows_rejected']} exceeded max_rejected {max_rejected}",
            'metrics': metrics,
        }

    if metrics['rows_discarded'] > max_discarded:
        return {
            'status': 'failed',
            'message': f"rows_discarded {metrics['rows_discarded']} exceeded max_discarded {max_discarded}",
            'metrics': metrics,
        }

    return {'status': 'passed', 'message': 'sqlloader thresholds satisfied', 'metrics': metrics}
