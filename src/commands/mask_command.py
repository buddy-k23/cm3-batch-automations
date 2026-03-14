"""Masking command implementation for DEV-safe batch files."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Dict, List


def _digit_hash(value: str, width: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    digits = "".join(str(int(ch, 16) % 10) for ch in digest)
    out = (digits * ((width // len(digits)) + 1))[:width]
    return out


def _preserve_format(value: str) -> str:
    out = []
    for ch in value:
        if ch.isdigit():
            out.append(str((int(ch) + 7) % 10))
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    return "".join(out)


def _apply_strategy(value: str, rule: Dict) -> str:
    strategy = rule.get("strategy", "preserve")
    if strategy == "preserve":
        return value
    if strategy == "preserve_format":
        return _preserve_format(value)
    if strategy == "deterministic_hash":
        return _digit_hash(value.strip() or "0", len(value))
    if strategy == "redact":
        return " " * len(value)
    if strategy == "random_range":
        low = int(rule.get("min", 0))
        high = int(rule.get("max", 99999))
        n = random.randint(low, high)
        s = str(n)
        return s[-len(value):].rjust(len(value), "0")
    if strategy == "fake_name":
        name = random.choice(["ALICE", "BOB", "CAROL", "DAVID", "EVA"])
        return name[: len(value)].ljust(len(value))
    return value


def run_mask_command(file: str, mapping: str, rules: str, output: str) -> None:
    """Mask a batch file using mapping/rules and write a new output file.

    Args:
        file: Input batch file path.
        mapping: Mapping JSON path.
        rules: Masking rules JSON path.
        output: Output masked file path.
    """
    mapping_doc = json.loads(Path(mapping).read_text(encoding="utf-8"))
    rules_doc = json.loads(Path(rules).read_text(encoding="utf-8"))

    strategy_by_name = {r["name"]: r for r in rules_doc.get("fields", [])}
    fmt = mapping_doc.get("source", {}).get("format", "fixed_width")
    fields: List[Dict] = mapping_doc.get("fields", [])

    src = Path(file)
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with src.open("r", encoding="utf-8") as fin, dest.open("w", encoding="utf-8") as fout:
        if fmt == "fixed_width":
            lengths = [int(f.get("length", 0)) for f in fields]
            for line in fin:
                row = line.rstrip("\n")
                cursor = 0
                chunks: List[str] = []
                for f, w in zip(fields, lengths):
                    value = row[cursor:cursor + w]
                    cursor += w
                    masked = _apply_strategy(value, strategy_by_name.get(f.get("name", ""), {}))
                    chunks.append(masked[:w].ljust(w))
                fout.write("".join(chunks) + "\n")
        else:
            delim = "|" if fmt == "pipe_delimited" else ","
            for line in fin:
                parts = line.rstrip("\n").split(delim)
                out_parts = []
                for i, value in enumerate(parts):
                    field_name = fields[i].get("name") if i < len(fields) else f"FIELD_{i+1}"
                    masked = _apply_strategy(value, strategy_by_name.get(field_name, {}))
                    out_parts.append(masked)
                fout.write(delim.join(out_parts) + "\n")
