"""Multi-record-type transform engine — routes rows to per-type TransformEngines.

For batch files that contain multiple interleaved record types (e.g. header /
detail / trailer), each type may have its own transform rules.
:class:`MultiRecordTransformEngine` manages one
:class:`~src.transforms.transform_orchestrator.TransformEngine` per record
type and dispatches each incoming row to the correct engine based on a
discriminator field value.

Typical usage::

    config = {
        "discriminator": {"field": "REC_TYPE"},
        "record_types": [
            {"type_name": "HEADER",  "discriminator_value": "H", "mapping": {...}},
            {"type_name": "DETAIL",  "discriminator_value": "D", "mapping": {...}},
            {"type_name": "TRAILER", "discriminator_value": "T", "mapping": None},
        ],
    }
    engine = MultiRecordTransformEngine(config)
    for row in db_rows:
        transformed = engine.apply(row)
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.transforms.transform_orchestrator import TransformEngine


# Sentinel type name for rows whose discriminator value is not recognised.
_UNKNOWN = "UNKNOWN"


class MultiRecordTransformEngine:
    """Dispatch rows to per-record-type :class:`~src.transforms.transform_orchestrator.TransformEngine` instances.

    Each record type in the *config* ``record_types`` list may optionally
    define a ``mapping`` dict.  When ``mapping`` is ``None`` or absent, rows
    of that type pass through unchanged.  An additional ``_record_type`` key
    is injected into every output row to indicate which type was matched.

    Args:
        config: Multi-record configuration dict with keys:

            - ``discriminator``: dict with at least a ``field`` key naming
              the row field used to identify the record type.
            - ``record_types``: list of type-definition dicts, each with
              ``type_name``, ``discriminator_value``, and optional
              ``mapping``.

    Example::

        engine = MultiRecordTransformEngine(config)
        result = engine.apply({"REC_TYPE": "H", "TITLE": "X"})
        # {"REC_TYPE": "H", "TITLE": "HDR", "_record_type": "HEADER"}
    """

    def __init__(self, config: dict) -> None:
        """Pre-compile per-type TransformEngines from *config*.

        Args:
            config: Multi-record config dict (see class docstring).
        """
        discriminator_cfg = config.get("discriminator", {})
        self._discriminator_field: str = discriminator_cfg.get("field", "")

        # Map discriminator_value → (type_name, TransformEngine | None)
        self._engines: Dict[str, tuple] = {}
        for type_def in config.get("record_types", []):
            disc_value = str(type_def.get("discriminator_value", ""))
            type_name = type_def.get("type_name", disc_value)
            mapping = type_def.get("mapping")
            engine: Optional[TransformEngine] = (
                TransformEngine(mapping) if mapping else None
            )
            self._engines[disc_value] = (type_name, engine)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, source_row: dict) -> dict:
        """Apply the appropriate per-type transform to *source_row*.

        The discriminator field value is read from *source_row* to select
        the correct :class:`~src.transforms.transform_orchestrator.TransformEngine`.
        When the value is not recognised, or the matched type has no mapping,
        the row is returned unchanged (with ``_record_type`` injected).

        Args:
            source_row: Dict mapping source field names to string values.

        Returns:
            Transformed row dict with ``_record_type`` key added.
        """
        disc_value = str(source_row.get(self._discriminator_field, ""))
        type_name, engine = self._engines.get(disc_value, (_UNKNOWN, None))

        if engine is not None:
            result = engine.apply(source_row)
        else:
            result = dict(source_row)

        result["_record_type"] = type_name
        return result

    def apply_batch(self, rows: List[dict]) -> List[dict]:
        """Apply :meth:`apply` to every row in *rows*.

        Args:
            rows: List of source row dicts.

        Returns:
            List of transformed row dicts in the same order.
        """
        return [self.apply(row) for row in rows]
