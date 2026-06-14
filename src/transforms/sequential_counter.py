"""Stateful sequential counter for :class:`SequentialNumberTransform`.

The :class:`SequentialCounter` maintains one integer counter slot per
``SequentialNumberTransform`` instance, keyed by the object's ``id()``.
This ensures that two distinct transform objects in the same mapping are
completely independent even when they share identical parameters.

The counter is intentionally separate from the transform model so that
transform objects remain lightweight, immutable value objects.  The owning
service or pipeline runner is responsible for creating a single counter,
passing it into :func:`~src.transforms.transform_engine.apply_transform`
for every field in a batch, and calling :meth:`SequentialCounter.reset_all`
between batches or files.
"""

from __future__ import annotations

from typing import Dict

from src.transforms.models import SequentialNumberTransform


class SequentialCounter:
    """Maintains per-transform-instance sequential counters.

    Each :class:`~src.transforms.models.SequentialNumberTransform` object is
    tracked by its Python ``id()``.  A new counter slot is created lazily on
    the first :meth:`next_value` or :meth:`reset` call for that transform.

    Example::

        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1)
        counter.next_value(t)  # "1"
        counter.next_value(t)  # "2"
        counter.reset(t)
        counter.next_value(t)  # "1"
    """

    def __init__(self) -> None:
        """Initialise with an empty counter registry."""
        self._counters: Dict[int, int] = {}

    def next_value(self, transform: SequentialNumberTransform) -> str:
        """Return the current counter value as a string, then advance by step.

        If this is the first call for *transform*, the counter is initialised
        to ``transform.start`` before the value is returned.

        When ``transform.pad_length`` is set the returned string is
        zero-padded to that width.  Values that already meet or exceed
        ``pad_length`` are never truncated.

        Args:
            transform: The :class:`SequentialNumberTransform` whose counter
                should be read and advanced.

        Returns:
            The current counter value as a (possibly zero-padded) string.
        """
        key = id(transform)
        if key not in self._counters:
            self._counters[key] = transform.start

        current = self._counters[key]
        self._counters[key] = current + transform.step

        raw = str(current)
        if transform.pad_length is not None:
            raw = raw.zfill(transform.pad_length)
        return raw

    def reset(self, transform: SequentialNumberTransform) -> None:
        """Reset the counter for *transform* back to ``transform.start``.

        If *transform* has never been seen before, this is a no-op.

        Args:
            transform: The transform whose counter should be reset.
        """
        self._counters[id(transform)] = transform.start

    def reset_all(self) -> None:
        """Reset all tracked counters to their respective start values.

        After this call every transform that was previously tracked will
        restart at its ``start`` value on the next :meth:`next_value` call.
        Transform objects that have never been seen are unaffected.
        """
        self._counters.clear()
