"""Service for multi-record config wizard auto-detection helpers.

Provides :func:`detect_discriminator` which scans the first N lines of a
batch file and returns candidate ``(position, length)`` pairs that are likely
discriminator fields, scored by a confidence metric.

Typical usage::

    content = uploaded_file.read().decode("utf-8", errors="replace")
    result = detect_discriminator(content, max_lines=20)
    best = result["best"]          # highest-confidence candidate, or None
    candidates = result["candidates"]  # all candidates with confidence ≥ 0.5
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def detect_discriminator(
    content: str,
    max_lines: int = 20,
) -> dict[str, Any]:
    """Scan file text for a likely record-type discriminator field.

    The algorithm inspects each ``(position p, length l)`` pair where
    ``p`` ∈ 0–10 (0-indexed byte offset) and ``l`` ∈ 1–6, extracts that
    substring from every line, and scores it:

    - Require 2–8 distinct repeating values (each appearing ≥ 2 times).
    - Confidence = ``repeating_lines / total_lines`` (coverage).
    - Ties broken by: n_distinct DESC (more types = better discrimination),
      position ASC (prefer earlier fields), length DESC (prefer longer keys).

    Only pairs with a computed confidence ≥ 0.5 are returned.  Candidates
    are sorted descending by ``(confidence, n_distinct, -position, length)``.
    Positions in the response are **1-indexed** (user-facing field position).

    Args:
        content: Raw text of the batch file to scan.
        max_lines: Maximum number of lines to read.  Defaults to ``20``.

    Returns:
        Dict with two keys:

        - ``candidates``: list of dicts, each with ``position`` (1-indexed
          int), ``length`` (int), ``values`` (list[str] of distinct repeating
          values), ``confidence`` (float 0–1).  Empty list when no pattern
          found.
        - ``best``: the first (highest-confidence) candidate, or ``None``
          when ``candidates`` is empty.

    Example::

        >>> result = detect_discriminator("HDR...\\nDTL...\\nTRL...\\n")
        >>> result["best"]["position"]
        1
        >>> result["best"]["length"]
        3
    """
    lines = [ln for ln in content.splitlines() if ln.strip()]
    lines = lines[:max_lines]

    if not lines:
        return {"candidates": [], "best": None}

    n_lines = len(lines)
    min_line_len = min(len(ln) for ln in lines)

    candidates: list[dict[str, Any]] = []

    for offset in range(min(11, min_line_len)):
        for length in range(1, 7):
            end = offset + length
            if end > min_line_len:
                break

            values = [ln[offset:end] for ln in lines]
            counts = Counter(values)

            # Build the set of values that repeat (appear ≥ 2 times)
            repeating = {v: c for v, c in counts.items() if c >= 2}
            n_repeating_distinct = len(repeating)

            # Need 2–8 repeating-distinct values
            if n_repeating_distinct < 2 or n_repeating_distinct > 8:
                continue

            repeating_lines = sum(repeating.values())
            confidence = round(repeating_lines / n_lines, 4)

            if confidence < 0.5:
                continue

            candidates.append(
                {
                    "position": offset + 1,  # 1-indexed
                    "length": length,
                    "values": sorted(repeating.keys()),
                    "confidence": confidence,
                    # Internal sort key fields (removed before return)
                    "_n_distinct": n_repeating_distinct,
                }
            )

    # Sort: confidence DESC, n_distinct DESC (more types = better),
    # position ASC (prefer earlier), length DESC (prefer longer/more specific)
    candidates.sort(
        key=lambda c: (
            -c["confidence"],
            -c["_n_distinct"],
            c["position"],
            -c["length"],
        )
    )

    # Strip internal sort keys from output
    for c in candidates:
        c.pop("_n_distinct")

    return {
        "candidates": candidates,
        "best": candidates[0] if candidates else None,
    }
