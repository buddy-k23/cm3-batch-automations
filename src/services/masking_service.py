"""Service for masking PII data in batch files.

Supports fixed-width and pipe-delimited formats with multiple masking
strategies.  The service reads the input file, applies per-field masking
rules, and writes a masked copy — the original file is never modified.
"""

from __future__ import annotations

import hashlib
import random
import string
from pathlib import Path
from typing import Any


class MaskingService:
    """Applies configurable masking strategies to batch file fields.

    Strategies:
        preserve         -- no masking; value passes through unchanged.
        preserve_format  -- random chars/digits matching the character pattern.
        deterministic_hash -- SHA-256 based; same input always yields the same output.
        random_range     -- random integer within ``min`` / ``max`` bounds.
        redact           -- spaces (fixed-width) or empty string (delimited).
        fake_name        -- random selection from a built-in list of ~50 names.
    """

    FAKE_NAMES: list[str] = [
        "ADAMS", "BAKER", "CLARK", "DAVIS", "EVANS", "FRANK", "GREEN",
        "HAYES", "IRWIN", "JONES", "KELLY", "LEWIS", "MASON", "NORTH",
        "OWENS", "PERRY", "QUINN", "REESE", "SCOTT", "TYLER", "UPTON",
        "VANCE", "WALSH", "YOUNG", "ZHANG", "ALLEN", "BROWN", "CHANG",
        "DRAKE", "ELLIS", "FLOYD", "GRANT", "HARDY", "JAMES", "KEANE",
        "LLOYD", "MILLS", "NOBLE", "OLSEN", "PRICE", "RILEY", "STONE",
        "TERRY", "URBAN", "VILLA", "WHITE", "YATES", "BLOOM", "CRANE",
        "DUKE",
    ]

    # ------------------------------------------------------------------
    # Strategy dispatch
    # ------------------------------------------------------------------

    def apply_strategy(self, strategy: str, value: str, opts: dict[str, Any]) -> str:
        """Apply a single masking strategy to a field value.

        Args:
            strategy: Name of the masking strategy.
            value: Original field value (always a string).
            opts: Extra options for the strategy (e.g. ``length``, ``min``,
                ``max``, ``format_type``).

        Returns:
            The masked string value.

        Raises:
            ValueError: If *strategy* is not recognised.
        """
        handler = {
            "preserve": self._preserve,
            "preserve_format": self._preserve_format,
            "deterministic_hash": self._deterministic_hash,
            "random_range": self._random_range,
            "redact": self._redact,
            "fake_name": self._fake_name,
        }.get(strategy)

        if handler is None:
            raise ValueError(f"Unknown masking strategy: {strategy!r}")

        return handler(value, opts)

    # ------------------------------------------------------------------
    # Individual strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _preserve(value: str, opts: dict[str, Any]) -> str:  # noqa: ARG004
        return value

    @staticmethod
    def _preserve_format(value: str, opts: dict[str, Any]) -> str:  # noqa: ARG004
        """Replace each character with a random character of the same type."""
        result: list[str] = []
        for ch in value:
            if ch.isalpha():
                result.append(random.choice(string.ascii_uppercase if ch.isupper() else string.ascii_lowercase))
            elif ch.isdigit():
                result.append(random.choice(string.digits))
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _deterministic_hash(value: str, opts: dict[str, Any]) -> str:
        """SHA-256 hex digest truncated/padded to the requested length."""
        length = opts.get("length", len(value))
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest().upper()
        # Repeat if length exceeds 64 hex chars (unlikely for field widths)
        while len(digest) < length:
            digest += digest
        return digest[:length]

    @staticmethod
    def _random_range(value: str, opts: dict[str, Any]) -> str:
        """Random integer between ``min`` and ``max`` (inclusive)."""
        lo = int(opts.get("min", 0))
        hi = int(opts.get("max", 999999))
        return str(random.randint(lo, hi))

    @staticmethod
    def _redact(value: str, opts: dict[str, Any]) -> str:
        """Spaces for fixed-width, empty string for delimited."""
        fmt = opts.get("format_type", "fixed_width")
        if fmt == "fixed_width":
            length = opts.get("length", len(value))
            return " " * length
        return ""

    def _fake_name(self, value: str, opts: dict[str, Any]) -> str:  # noqa: ARG002
        return random.choice(self.FAKE_NAMES)

    # ------------------------------------------------------------------
    # File-level masking
    # ------------------------------------------------------------------

    def mask_file(
        self,
        input_path: str,
        output_path: str,
        mapping: dict[str, Any],
        masking_rules: dict[str, Any],
    ) -> dict[str, Any]:
        """Mask an entire file according to *mapping* and *masking_rules*.

        Args:
            input_path: Path to the source batch file (never modified).
            output_path: Destination path for the masked output.
            mapping: Mapping JSON dict with ``source.format`` and ``fields``.
            masking_rules: Dict with ``fields`` mapping field names to
                strategy configuration.

        Returns:
            Summary dict with ``records_masked`` and ``output_path``.

        Raises:
            FileNotFoundError: If *input_path* does not exist.
            ValueError: If the mapping format is unsupported.
        """
        input_file = Path(input_path)
        if not input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        fmt = self._detect_format(mapping)
        if fmt == "fixed_width":
            count = self._mask_fixed_width(input_path, output_path, mapping, masking_rules)
        elif fmt == "pipe_delimited":
            count = self._mask_pipe_delimited(input_path, output_path, mapping, masking_rules)
        else:
            raise ValueError(f"Unsupported mapping format: {fmt!r}")

        return {"records_masked": count, "output_path": output_path}

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_format(mapping: dict[str, Any]) -> str:
        """Return the format string from a mapping dict."""
        fmt = mapping.get("source", {}).get("format", "")
        if not fmt:
            # Fall back to top-level ``format_type`` used in older mappings
            fmt = mapping.get("format_type", "")
        return fmt

    def _mask_fixed_width(
        self,
        input_path: str,
        output_path: str,
        mapping: dict[str, Any],
        masking_rules: dict[str, Any],
    ) -> int:
        """Mask a fixed-width file, preserving exact field widths."""
        fields = mapping.get("fields", [])
        field_rules = masking_rules.get("fields", {})

        # Build (name, start, length) tuples
        positions: list[tuple[str, int, int]] = []
        offset = 0
        for f in fields:
            length = f["length"]
            positions.append((f["name"], offset, length))
            offset += length

        record_count = 0
        with open(input_path, "r", encoding="utf-8", errors="replace") as fin, \
             open(output_path, "w", encoding="utf-8") as fout:
            for line in fin:
                raw = line.rstrip("\n")
                if not raw:
                    fout.write("\n")
                    continue

                chars = list(raw)
                for name, start, length in positions:
                    rule = field_rules.get(name, {"strategy": "preserve"})
                    strategy = rule.get("strategy", "preserve")
                    original_value = raw[start:start + length]

                    opts: dict[str, Any] = {k: v for k, v in rule.items() if k != "strategy"}
                    opts["length"] = length
                    opts["format_type"] = "fixed_width"

                    masked = self.apply_strategy(strategy, original_value, opts)

                    # Pad or truncate to exact field width
                    if len(masked) < length:
                        masked = masked.ljust(length)
                    elif len(masked) > length:
                        masked = masked[:length]

                    for i, ch in enumerate(masked):
                        chars[start + i] = ch

                fout.write("".join(chars) + "\n")
                record_count += 1

        return record_count

    def _mask_pipe_delimited(
        self,
        input_path: str,
        output_path: str,
        mapping: dict[str, Any],
        masking_rules: dict[str, Any],
    ) -> int:
        """Mask a pipe-delimited file, preserving delimiter and column count."""
        fields = mapping.get("fields", [])
        field_names = [f["name"] for f in fields]
        field_rules = masking_rules.get("fields", {})

        record_count = 0
        with open(input_path, "r", encoding="utf-8", errors="replace") as fin, \
             open(output_path, "w", encoding="utf-8") as fout:
            for line in fin:
                raw = line.rstrip("\n")
                if not raw:
                    continue

                parts = raw.split("|")
                masked_parts: list[str] = []

                for idx, part in enumerate(parts):
                    if idx < len(field_names):
                        name = field_names[idx]
                        rule = field_rules.get(name, {"strategy": "preserve"})
                    else:
                        rule = {"strategy": "preserve"}

                    strategy = rule.get("strategy", "preserve")
                    opts = {k: v for k, v in rule.items() if k != "strategy"}
                    opts["format_type"] = "pipe_delimited"

                    masked_parts.append(self.apply_strategy(strategy, part, opts))

                fout.write("|".join(masked_parts) + "\n")
                record_count += 1

        return record_count
