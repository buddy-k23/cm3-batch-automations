"""Unit tests for the SequentialCounter and SequentialNumberTransform.

Tests are written before implementation — they are expected to fail initially
(TDD red phase).  Covers:
- next_value starts at configured start (default 1)
- next_value increments by step on each call (default step=1)
- two distinct transform instances maintain independent counters
- reset() resets a single counter back to its start value
- reset_all() resets every counter
- custom start value respected
- custom step value respected
- zero-padding (pad_length) respected
"""

import pytest

from src.transforms.models import SequentialNumberTransform
from src.transforms.sequential_counter import SequentialCounter


# ---------------------------------------------------------------------------
# SequentialNumberTransform model
# ---------------------------------------------------------------------------


class TestSequentialNumberTransformModel:
    """SequentialNumberTransform is a dataclass with correct defaults."""

    def test_default_start(self):
        """Default start is 1."""
        t = SequentialNumberTransform()
        assert t.start == 1

    def test_default_step(self):
        """Default step is 1."""
        t = SequentialNumberTransform()
        assert t.step == 1

    def test_default_pad_length(self):
        """Default pad_length is None (no zero-padding)."""
        t = SequentialNumberTransform()
        assert t.pad_length is None

    def test_type_attribute(self):
        """type is always 'sequential'."""
        t = SequentialNumberTransform()
        assert t.type == "sequential"

    def test_custom_start(self):
        """Custom start is stored."""
        t = SequentialNumberTransform(start=10)
        assert t.start == 10

    def test_custom_step(self):
        """Custom step is stored."""
        t = SequentialNumberTransform(step=5)
        assert t.step == 5

    def test_custom_pad_length(self):
        """Custom pad_length is stored."""
        t = SequentialNumberTransform(pad_length=5)
        assert t.pad_length == 5


# ---------------------------------------------------------------------------
# SequentialCounter — basic next_value behaviour
# ---------------------------------------------------------------------------


class TestSequentialCounterNextValue:
    """SequentialCounter.next_value returns strings and increments state."""

    def test_starts_at_one_by_default(self):
        """First next_value call returns '1' when start=1 (default)."""
        counter = SequentialCounter()
        t = SequentialNumberTransform()
        assert counter.next_value(t) == "1"

    def test_increments_on_each_call(self):
        """Successive calls increment the counter by step."""
        counter = SequentialCounter()
        t = SequentialNumberTransform()
        assert counter.next_value(t) == "1"
        assert counter.next_value(t) == "2"
        assert counter.next_value(t) == "3"

    def test_custom_start(self):
        """Counter starts at the configured start value."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=100)
        assert counter.next_value(t) == "100"
        assert counter.next_value(t) == "101"

    def test_custom_step(self):
        """Counter increments by step on each call."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=0, step=10)
        assert counter.next_value(t) == "0"
        assert counter.next_value(t) == "10"
        assert counter.next_value(t) == "20"

    def test_returns_string(self):
        """next_value always returns a string, not an int."""
        counter = SequentialCounter()
        t = SequentialNumberTransform()
        result = counter.next_value(t)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SequentialCounter — zero-padding
# ---------------------------------------------------------------------------


class TestSequentialCounterPadding:
    """SequentialCounter respects pad_length for zero-padding."""

    def test_zero_padded(self):
        """pad_length=5 pads to 5 digits with leading zeros."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1, pad_length=5)
        assert counter.next_value(t) == "00001"
        assert counter.next_value(t) == "00002"

    def test_no_padding_when_pad_length_is_none(self):
        """When pad_length is None, no zero-padding is applied."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1, pad_length=None)
        assert counter.next_value(t) == "1"

    def test_value_exceeds_pad_length(self):
        """When the value exceeds pad_length, no truncation occurs."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=999999, pad_length=3)
        assert counter.next_value(t) == "999999"


# ---------------------------------------------------------------------------
# SequentialCounter — independent counters per transform instance
# ---------------------------------------------------------------------------


class TestSequentialCounterIndependence:
    """Each distinct transform instance has its own counter slot."""

    def test_two_transforms_are_independent(self):
        """Two transform objects do not share state."""
        counter = SequentialCounter()
        t1 = SequentialNumberTransform(start=1)
        t2 = SequentialNumberTransform(start=1)

        # Advance t1 three times.
        counter.next_value(t1)
        counter.next_value(t1)
        counter.next_value(t1)

        # t2 should still be at 1.
        assert counter.next_value(t2) == "1"

    def test_same_transform_object_shares_state(self):
        """The same transform object accumulates state across calls."""
        counter = SequentialCounter()
        t = SequentialNumberTransform()
        counter.next_value(t)
        counter.next_value(t)
        # Third call on same instance → "3".
        assert counter.next_value(t) == "3"


# ---------------------------------------------------------------------------
# SequentialCounter — reset
# ---------------------------------------------------------------------------


class TestSequentialCounterReset:
    """reset() and reset_all() restore counter state."""

    def test_reset_single_counter(self):
        """reset() for one transform restores it to start without touching others."""
        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1)

        counter.next_value(t)
        counter.next_value(t)
        counter.reset(t)

        assert counter.next_value(t) == "1"

    def test_reset_does_not_affect_other_transform(self):
        """reset() on t1 does not affect t2's counter."""
        counter = SequentialCounter()
        t1 = SequentialNumberTransform(start=1)
        t2 = SequentialNumberTransform(start=1)

        counter.next_value(t1)
        counter.next_value(t2)
        counter.next_value(t2)
        counter.reset(t1)

        # t2 should continue from where it was.
        assert counter.next_value(t2) == "3"

    def test_reset_all(self):
        """reset_all() resets every tracked counter."""
        counter = SequentialCounter()
        t1 = SequentialNumberTransform(start=1)
        t2 = SequentialNumberTransform(start=5)

        counter.next_value(t1)
        counter.next_value(t1)
        counter.next_value(t2)

        counter.reset_all()

        assert counter.next_value(t1) == "1"
        assert counter.next_value(t2) == "5"

    def test_reset_untracked_transform_is_noop(self):
        """reset() on a never-seen transform does not raise."""
        counter = SequentialCounter()
        t = SequentialNumberTransform()
        counter.reset(t)  # Should not raise.
        # After reset (which set it to start), first call gives "1".
        assert counter.next_value(t) == "1"
