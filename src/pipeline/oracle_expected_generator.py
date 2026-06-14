"""Generate expected target files from Oracle (app_int) transformation SQL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, List

from src.database.connection import OracleConnection
from src.database.extractor import DataExtractor


def load_oracle_manifest(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def generate_expected_from_oracle(manifest: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """Generate expected files from query definitions.

    Manifest shape:
    {
      "schema": "app_int",
      "jobs": [
        {"name": "SRC_A_P327", "query_file": "config/queries/app_int/SRC_A_p327_transform.sql", "output_file": "outputs/expected/SRC_A/p327.txt", "delimiter": "|"}
      ]
    }
    """
    jobs: List[Dict[str, Any]] = manifest.get('jobs', []) or []
    if not jobs:
        return {'status': 'failed', 'message': 'no jobs configured', 'jobs': []}

    out_jobs = []
    failed = False

    if dry_run:
        for job in jobs:
            out_jobs.append({
                'name': job.get('name'),
                'status': 'dry_run',
                'query_file': job.get('query_file'),
                'output_file': job.get('output_file')
            })
        return {'status': 'passed', 'message': 'dry-run complete', 'jobs': out_jobs}

    conn = OracleConnection.from_env()
    extractor = DataExtractor(conn)

    for job in jobs:
        name = job.get('name')
        qf = job.get('query_file')
        of = job.get('output_file')
        delim = job.get('delimiter', '|')

        if not qf or not of:
            out_jobs.append({'name': name, 'status': 'failed', 'message': 'query_file and output_file are required'})
            failed = True
            continue

        query = Path(qf).read_text(encoding='utf-8')
        Path(of).parent.mkdir(parents=True, exist_ok=True)

        try:
            stats = extractor.extract_to_file(output_file=of, query=query, delimiter=delim)
            out_jobs.append({'name': name, 'status': 'passed', 'output_file': of, 'stats': stats})
        except Exception as e:
            out_jobs.append({'name': name, 'status': 'failed', 'message': str(e)})
            failed = True

    return {
        'status': 'failed' if failed else 'passed',
        'message': 'oracle expected generation complete',
        'jobs': out_jobs,
    }
