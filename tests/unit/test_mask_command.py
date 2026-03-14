"""Tests for batch file masking command."""

import json
from pathlib import Path

from src.commands.mask_command import run_mask_command


def test_mask_fixed_width_preserves_record_length(tmp_path: Path):
    mapping = {
        "mapping_name": "m1",
        "source": {"format": "fixed_width"},
        "fields": [
            {"name": "ACCT", "length": 6},
            {"name": "SSN", "length": 9},
            {"name": "DATE", "length": 8},
        ],
    }
    rules = {
        "fields": [
            {"name": "ACCT", "strategy": "preserve_format"},
            {"name": "SSN", "strategy": "deterministic_hash"},
            {"name": "DATE", "strategy": "preserve"},
        ]
    }
    inp = tmp_path / "in.txt"
    outp = tmp_path / "out.txt"
    mp = tmp_path / "m.json"
    rp = tmp_path / "r.json"

    inp.write_text("12345611122333320260301\n12345611122333320260301\n", encoding="utf-8")
    mp.write_text(json.dumps(mapping), encoding="utf-8")
    rp.write_text(json.dumps(rules), encoding="utf-8")

    run_mask_command(str(inp), str(mp), str(rp), str(outp))

    out_lines = outp.read_text(encoding="utf-8").splitlines()
    assert len(out_lines) == 2
    assert len(out_lines[0]) == len("12345611122333320260301")
    assert out_lines[0][15:23] == "20260301"


def test_mask_deterministic_hash_stable_for_same_input(tmp_path: Path):
    mapping = {
        "mapping_name": "m1",
        "source": {"format": "fixed_width"},
        "fields": [
            {"name": "SSN", "length": 9},
        ],
    }
    rules = {"fields": [{"name": "SSN", "strategy": "deterministic_hash"}]}
    inp = tmp_path / "in.txt"
    outp = tmp_path / "out.txt"
    mp = tmp_path / "m.json"
    rp = tmp_path / "r.json"

    inp.write_text("111223333\n111223333\n", encoding="utf-8")
    mp.write_text(json.dumps(mapping), encoding="utf-8")
    rp.write_text(json.dumps(rules), encoding="utf-8")

    run_mask_command(str(inp), str(mp), str(rp), str(outp))

    out = outp.read_text(encoding="utf-8").splitlines()
    assert out[0] == out[1]
    assert out[0] != "111223333"
