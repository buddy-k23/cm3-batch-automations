#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def mutate_line(line: str, seed: int) -> str:
    # Deterministic, position-based mutation to keep fixed-width format.
    out_chars = []
    for idx, ch in enumerate(line):
        n = (seed + idx * 131) & 0xFFFFFFFF
        if ch.isdigit():
            d = ord(ch) - ord("0")
            out_chars.append(str((d + (n % 10)) % 10))
        elif "A" <= ch <= "Z":
            shift = n % 26
            out_chars.append(chr(ord("A") + (ord(ch) - ord("A") + shift) % 26))
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def generate_files(src: Path, out_a: Path, out_b: Path, rows: int) -> None:
    lines = src.read_text().splitlines()
    if not lines:
        raise SystemExit(f"No lines found in {src}")
    line_len = len(lines[0])
    if any(len(l) != line_len for l in lines):
        raise SystemExit("Source file contains variable-length lines; aborting.")

    def write_out(path: Path, file_id: int) -> None:
        with path.open("w", newline="") as f:
            for i in range(rows):
                base = lines[i % len(lines)]
                seed = (file_id + 1) * 1_000_003 + i * 9176
                f.write(mutate_line(base, seed))
                f.write("\n")

    write_out(out_a, 0)
    write_out(out_b, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate fixed-width p327 test data files.")
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Path to p327_test_data.txt",
    )
    parser.add_argument(
        "--out-a",
        type=Path,
        required=True,
        help="Output path for first file",
    )
    parser.add_argument(
        "--out-b",
        type=Path,
        required=True,
        help="Output path for second file",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=20000,
        help="Number of rows per output file",
    )
    args = parser.parse_args()

    generate_files(args.src, args.out_a, args.out_b, args.rows)


if __name__ == "__main__":
    main()
