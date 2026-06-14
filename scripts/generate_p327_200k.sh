#!/usr/bin/env bash
set -euo pipefail

# Generate a large fixed-width P327 file by multiplying sample records
# with distinct account numbers and varying data fields.
#
# Usage:
#   bash scripts/generate_p327_200k.sh \
#     /Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample.txt \
#     /Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample_200k.txt \
#     200000

INPUT_FILE="${1:-/Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample.txt}"
OUTPUT_FILE="${2:-/Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample_200k.txt}"
TARGET_COUNT="${3:-200000}"

if [[ ! -f "$INPUT_FILE" ]]; then
  echo "❌ Input file not found: $INPUT_FILE"
  exit 1
fi

if ! [[ "$TARGET_COUNT" =~ ^[0-9]+$ ]] || [[ "$TARGET_COUNT" -le 0 ]]; then
  echo "❌ TARGET_COUNT must be a positive integer. Got: $TARGET_COUNT"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "Generating $TARGET_COUNT records..."
echo "Input : $INPUT_FILE"
echo "Output: $OUTPUT_FILE"

awk -v target="$TARGET_COUNT" '
function padleft(val, width,    s) {
  s = val ""
  while (length(s) < width) s = "0" s
  if (length(s) > width) s = substr(s, length(s)-width+1)
  return s
}

function setfield(rec, start, len, value,    v, prefix, suffix, reclen) {
  # 1-indexed start
  v = value ""
  if (length(v) < len) {
    while (length(v) < len) v = v " "
  } else if (length(v) > len) {
    v = substr(v, 1, len)
  }

  reclen = length(rec)
  if (reclen < start + len - 1) {
    while (length(rec) < start + len - 1) rec = rec " "
  }

  prefix = (start > 1) ? substr(rec, 1, start-1) : ""
  suffix = substr(rec, start+len)
  return prefix v suffix
}

{
  src[++n] = $0
}

END {
  if (n == 0) {
    print "No source rows found" > "/dev/stderr"
    exit 1
  }

  for (i = 1; i <= target; i++) {
    rec = src[((i - 1) % n) + 1]

    # P327 positions (from mapping):
    # LOCATION-CODE: start 1, len 6
    # ACCT-NUM:      start 7, len 18
    # BALANCE-AMT:   start 46, len 19 (+9(12)V9(6) style packed as + + 18 digits)

    location = padleft(i % 1000000, 6)
    acct_num = padleft(i, 18)

    # Keep a deterministic but varied numeric amount per record
    bal_raw = padleft((i * 137) % 999999999999999999, 18)
    balance_amt = "+" bal_raw

    rec = setfield(rec, 1, 6, location)
    rec = setfield(rec, 7, 18, acct_num)
    rec = setfield(rec, 46, 19, balance_amt)

    print rec
  }
}
' "$INPUT_FILE" > "$OUTPUT_FILE"

echo "✅ Done. Wrote $(wc -l < "$OUTPUT_FILE") records to $OUTPUT_FILE"
