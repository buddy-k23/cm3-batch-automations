#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parents[1]
OUT_BASE = ROOT / "generated" / "pilot_contracts"
SUMMARY_JSON = ROOT / "generated" / "c360_source_tables_all_files.summary.json"
ENV_FILE = Path("/Users/buddy/.openclaw/workspace/cm3-batch-automations/.env")


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def infer_target_type(oracle_type: str) -> str:
    t = (oracle_type or "").upper()
    if t in {"DATE", "TIMESTAMP", "TIMESTAMP(6)", "TIMESTAMP WITH TIME ZONE"}:
        return "date"
    if t in {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}:
        return "numeric"
    if t in {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2", "CLOB"}:
        return "string"
    return "string"


def main() -> None:
    OUT_BASE.mkdir(parents=True, exist_ok=True)

    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    table_names = [x["table"] for x in summary]

    env = load_env(ENV_FILE)
    conn = oracledb.connect(user=env["ORACLE_USER"], password=env["ORACLE_PASSWORD"], dsn=env["ORACLE_DSN"])
    cur = conn.cursor()

    created = []

    for table in table_names:
        cur.execute(
            """
            SELECT column_name, data_type, data_length, nullable
            FROM user_tab_columns
            WHERE table_name = :t
            ORDER BY column_id
            """,
            t=table,
        )
        cols = cur.fetchall()
        if not cols:
            continue

        fields = []
        for c_name, c_type, c_len, c_nullable in cols:
            fields.append(
                {
                    "name": c_name,
                    "source": c_name,
                    "targetType": infer_target_type(c_type),
                    "required": c_nullable == "N",
                    "length": int(c_len or 0) if c_len else None,
                }
            )

        mapping = {
            "mappingId": f"{table.lower()}_mapping",
            "version": "1.0.0",
            "description": f"Auto-generated contract mapping for source table {table}",
            "source": {
                "type": "oracle",
                "query": f"SELECT * FROM {table}",
                "groupKeys": [],
            },
            "target": {
                "format": "delimited",
                "delimiter": "|",
                "quoteChar": '"',
                "escapeChar": "\\",
                "header": {"enabled": True, "totalCountField": "AUTO"},
                "recordLayout": {"strictLength": False, "recordLength": 1},
            },
            "fields": fields,
        }

        rules = {
            "rulePackId": f"{table.lower()}_rules",
            "version": "1.0.0",
            "status": "draft",
            "appliesTo": {"fileType": "delimited", "transactionCodes": []},
            "rules": [
                {
                    "ruleId": "FILE_DETAIL_COUNT_NON_NEGATIVE",
                    "name": "File detail count must be non-negative",
                    "scope": "file",
                    "severity": "ERROR",
                    "priority": 10,
                    "expression": "detail.count >= 0",
                    "messageTemplate": "Detail count must be non-negative",
                    "owner": "contract-generator",
                    "enabled": True,
                }
            ],
        }

        out_dir = OUT_BASE / table.lower()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        (out_dir / "rules.json").write_text(json.dumps(rules, indent=2), encoding="utf-8")
        created.append(table)

    cur.close()
    conn.close()

    (OUT_BASE / "index.json").write_text(json.dumps({"tables": created}, indent=2), encoding="utf-8")
    print(f"Generated contracts for {len(created)} tables in {OUT_BASE}")


if __name__ == "__main__":
    main()
