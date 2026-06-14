"""TransformEngine orchestrator — applies all field transforms to a source row.

This module is the top-level entry point for the transform system.  Given a
mapping configuration (either a ``list[dict]`` of field definitions or a
full mapping ``dict`` that contains a ``"fields"`` key), :class:`TransformEngine`
pre-compiles every field's :class:`~src.transforms.models.Transform` objects
at construction time and then applies them efficiently on each call to
:meth:`TransformEngine.apply`.

Typical usage::

    engine = TransformEngine(mapping_config)
    for db_row in db_rows:
        transformed = engine.apply(db_row)
        # compare *transformed* against the target file row

The engine is **stateful** only in the sense that
:class:`~src.transforms.sequential_counter.SequentialCounter` tracks
incrementing counters between :meth:`~TransformEngine.apply` calls.  Call
:meth:`~TransformEngine.reset` to restart all sequential counters (e.g.
between files or test runs).

Thread-safety: a single :class:`TransformEngine` instance must **not** be
called concurrently from multiple threads because the sequential counter is
mutable.  Create one instance per worker thread if parallel processing is
required.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from src.transforms.models import Transform
from src.transforms.sequential_counter import SequentialCounter
from src.transforms.transform_engine import apply_transform
from src.transforms.transform_parser import parse_transform


class TransformEngine:
    """Orchestrates field-level transforms for a complete source row.

    At construction time the engine pre-parses every field's transformation
    text (or raw transform dict) into a typed :class:`~src.transforms.models.Transform`
    object.  At apply time the compiled transforms are executed in field-list
    order; the full source row is passed to each transform so that cross-field
    references (e.g. :class:`~src.transforms.models.ConcatTransform`,
    :class:`~src.transforms.models.FieldMapTransform`) can read any field from
    the row.

    Args:
        mapping: Either a ``list`` of field-definition dicts (the ``fields``
            array from a mapping JSON) or a full mapping ``dict`` that has a
            ``"fields"`` key.  Each field dict is expected to have at least
            ``"target_name"``.  The optional ``"transformation"`` key holds a
            free-text string that is parsed by
            :func:`~src.transforms.transform_parser.parse_transform`.  A
            ``"field_length"`` key (int) triggers right-pad / right-truncate
            behaviour on the output.

    Example::

        fields = [
            {"target_name": "STATUS", "transformation": "Pass 'ACTIVE'"},
            {"target_name": "KEY",    "transformation": "BR + CUS + LN"},
        ]
        engine = TransformEngine(fields)
        result = engine.apply({"BR": "001", "CUS": "1234567", "LN": "9999"})
        # {"STATUS": "ACTIVE", "KEY": "00112345679999"}
    """

    def __init__(self, mapping: Union[list, dict]) -> None:
        """Pre-compile field transforms from *mapping*.

        Args:
            mapping: Field list or full mapping dict (see class docstring).
        """
        if isinstance(mapping, dict):
            fields: list = mapping.get("fields", [])
        else:
            fields = list(mapping)

        # Each entry: (target_name, transform_obj, field_length)
        self._fields: List[tuple] = []
        for field_def in fields:
            target = field_def.get("target_name", "")
            length = int(field_def.get("field_length", 0) or 0)
            transform = self._compile(field_def)
            self._fields.append((target, transform, length))

        self._counter = SequentialCounter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, source_row: dict) -> dict:
        """Apply all field transforms to *source_row* and return the output row.

        Fields present in the mapping but absent from *source_row* default to
        an empty string.  Fields in *source_row* that are not in the mapping
        are not included in the output.

        The full *source_row* (original, un-transformed values) is passed to
        every transform so that cross-field references read consistent data.

        Args:
            source_row: Dict mapping source field names to their string values.

        Returns:
            Dict mapping target field names to their transformed string values.
        """
        output: Dict[str, str] = {}
        for target_name, transform, field_length in self._fields:
            source_value: Optional[str] = source_row.get(target_name, "")
            output[target_name] = apply_transform(
                source_value,
                transform,
                field_length=field_length,
                row=source_row,
                counter=self._counter,
            )
        return output

    def reset(self) -> None:
        """Reset all sequential counters to their start values.

        Call between files, batches, or test cases to restart any
        :class:`~src.transforms.models.SequentialNumberTransform` counters.
        """
        self._counter.reset_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compile(field_def: dict) -> Transform:
        """Compile a field definition into a typed :class:`Transform`.

        Handles three cases:

        1. ``"transformation"`` key holds a free-text string → parsed by
           :func:`~src.transforms.transform_parser.parse_transform`.
        2. ``"transformations"`` key holds a list of raw transform dicts →
           the first entry is used (chained raw-dict transforms are not yet
           supported beyond single-entry lists; complex chains should use the
           dedicated transform model API directly).
        3. Neither key present → noop :class:`~src.transforms.models.Transform`.

        Args:
            field_def: A field-definition dict from the mapping JSON.

        Returns:
            A compiled :class:`~src.transforms.models.Transform` object.
        """
        text = field_def.get("transformation", "")
        if text:
            return parse_transform(str(text))

        # Fallback: raw transformations list (e.g. from older mapping format)
        transforms_list = field_def.get("transformations", [])
        if transforms_list and isinstance(transforms_list, list):
            # For now, parse the first entry's type/value as best-effort
            first = transforms_list[0]
            if isinstance(first, dict):
                t_type = first.get("type", "")
                value = first.get("value", first.get("default_value", ""))
                if t_type == "constant" and value:
                    return parse_transform(f"Pass '{value}'")
                if t_type == "default" and value:
                    return parse_transform(f"Default to '{value}'")
            # Unknown raw dict format — noop
            return Transform(type="noop")

        return Transform(type="noop")
