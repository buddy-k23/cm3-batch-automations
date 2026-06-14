"""Filter a canonical E2E pipeline YAML down to a subset of gates.

Why this exists
---------------
M3's ``generate_pipeline_yaml.py`` emits one canonical pipeline YAML per
``(env, source)`` containing the valdo-runnable gates
(``file_to_staging``, ``L1_structural``, ``L3_baseline_diff``). The M4
wrapper invokes ``valdo run-etl-pipeline`` twice per run:

  1. After the Java load step, just for ``file_to_staging``.
  2. After the Java generate step, for the output gates (L1/L3).

Valdo's runner has no ``--gate`` filter today (and the prompt forbids
adding one), so the wrapper writes a temporary, trimmed pipeline YAML
to the per-run work dir using this helper and points
``valdo run-etl-pipeline --config`` at it.

The split preserves gate order and every other top-level field
(``name``, ``description``, ``sources``) unchanged so the audit trail
in the per-step reports still names the same pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List

import yaml


class SplitPipelineError(RuntimeError):
    """Raised when the requested gate subset cannot be produced."""


def split_pipeline_yaml(
    source_yaml: Path,
    gate_names: Iterable[str],
    out_yaml: Path,
) -> List[str]:
    """Write a trimmed copy of ``source_yaml`` containing only ``gate_names``.

    Args:
        source_yaml: Path to the canonical pipeline YAML from M3.
        gate_names: Gate names to retain, in the canonical execution order.
            Unknown names raise :class:`SplitPipelineError`.
        out_yaml: Destination path for the filtered YAML.

    Returns:
        The list of gate names actually retained (same order as in the
        source YAML, which dictates execution order).

    Raises:
        SplitPipelineError: If ``source_yaml`` does not exist, is malformed,
            or does not contain every requested gate.
    """
    source_yaml = Path(source_yaml)
    out_yaml = Path(out_yaml)
    if not source_yaml.is_file():
        raise SplitPipelineError(f"source pipeline YAML not found: {source_yaml}")

    try:
        data = yaml.safe_load(source_yaml.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SplitPipelineError(
            f"failed to parse {source_yaml}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise SplitPipelineError(
            f"{source_yaml} must contain a YAML mapping at the top level"
        )

    requested = list(gate_names)
    if not requested:
        raise SplitPipelineError("gate_names must contain at least one name")
    requested_set = set(requested)

    gates = data.get("gates") or []
    if not isinstance(gates, list):
        raise SplitPipelineError(
            f"{source_yaml} 'gates' must be a list"
        )

    kept: List[dict] = []
    kept_names: List[str] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        name = gate.get("name")
        if name in requested_set:
            kept.append(gate)
            kept_names.append(name)

    missing = requested_set - set(kept_names)
    if missing:
        raise SplitPipelineError(
            f"gates not found in {source_yaml.name}: "
            f"{sorted(missing)}. Available: "
            f"{[g.get('name') for g in gates if isinstance(g, dict)]}"
        )

    trimmed = {
        "name": data.get("name", ""),
        "description": (
            (data.get("description") or "")
            + f" [filtered to gates: {', '.join(kept_names)}]"
        ).strip(),
        "sources": data.get("sources", []),
        "gates": kept,
    }

    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    out_yaml.write_text(
        yaml.safe_dump(
            trimmed, sort_keys=False, default_flow_style=False, width=120
        ),
        encoding="utf-8",
    )
    return kept_names


def _main(argv: list[str]) -> int:
    """CLI used by the bash wrapper. Args: <source> <out> <gate> [<gate>…]."""
    if len(argv) < 4:
        print(
            "usage: split_pipeline.py <source.yaml> <out.yaml> <gate> [<gate>...]",
            file=sys.stderr,
        )
        return 2
    source_yaml = Path(argv[1])
    out_yaml = Path(argv[2])
    gates = argv[3:]
    try:
        kept = split_pipeline_yaml(source_yaml, gates, out_yaml)
    except SplitPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_yaml} with gates: {', '.join(kept)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv))
