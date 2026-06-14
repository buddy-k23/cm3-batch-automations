"""One-shot smoke recipe for the SHAW TRANERT multi-record validator."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config.multi_record_config import MultiRecordConfig
from src.validators.multi_record_validator import MultiRecordValidator


def main() -> int:
    umbrella_path = "config/mappings/SHAW_TRANERT.yaml"
    # Accept an optional file path argument; default to the May sample.
    sample_path = sys.argv[1] if len(sys.argv) > 1 else "data/samples/tranert_shaw_20260521.txt"

    with open(umbrella_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    cfg = MultiRecordConfig(**raw)
    result = MultiRecordValidator().validate(sample_path, cfg)

    print(f"valid={result['valid']}  total_rows={result['total_rows']}")
    print(f"cross_type_violations={len(result.get('cross_type_violations', []))}")
    for v in result.get("cross_type_violations", []):
        print(f"  CROSS-TYPE: {v.get('message', v)}")
    print()
    print("Per-record-type results:")
    for key, value in sorted(result.get("record_type_results", {}).items()):
        total = value.get("total_rows", value.get("row_count"))
        valid_rows = value.get("valid_rows")
        err_count = value.get("error_count")
        is_valid = value.get("valid")
        ics = value.get("issue_code_summary") or {}
        print(
            f"  {key}: total_rows={total} valid_rows={valid_rows} "
            f"errors={err_count} valid={is_valid} "
            f"issue_codes={dict(ics) if ics else '{}'}"
        )
        errors = value.get("errors") or []
        for err in errors[:3]:
            if isinstance(err, dict):
                print(
                    f"    [{err.get('code')}] row={err.get('row')} "
                    f"field={err.get('field')} "
                    f"msg={(err.get('message') or '')[:100]}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
