"""Tests for gate/otel_tracing.py's stage_span().

The critical case isn't "does span attribution work" - it's "does a
real exception raised by the CALLER's own code inside `with
stage_span(...):` propagate as ITSELF, not as a confusing, unrelated
RuntimeError." See stage_span()'s own docstring for the exact bug this
guards against - found live by an external parallel review of this
project: before the fix, ANY real exception raised inside a
`with stage_span(...):` block anywhere in the gate (Sentry, IntentGraph,
Kernel stages) came out as `RuntimeError: generator didn't stop after
throw()` instead, silently replacing whatever the real failure was.
Zero test coverage existed for this module before this fix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gate.otel_tracing import _NullSpan, stage_span


def test_a_real_exception_inside_the_span_propagates_as_itself():
    """This is the actual bug. Before the fix, this raised
    `RuntimeError: generator didn't stop after throw()` instead of the
    ValueError below, masking whatever the real failure was."""
    with pytest.raises(ValueError, match="a real bug in gate logic"):
        with stage_span("test.stage"):
            raise ValueError("a real bug in gate logic")


def test_span_setup_failure_degrades_to_null_span_but_still_propagates_caller_exceptions():
    """Tracing is additive: if starting the span itself fails, the
    caller's code must still run against a _NullSpan - but a real
    exception from the caller's own code must still propagate, not be
    swallowed just because tracing itself is broken."""
    broken_tracer = MagicMock()
    broken_tracer.start_as_current_span.side_effect = RuntimeError("simulated span setup failure")

    with patch("gate.otel_tracing._init_tracer", return_value=broken_tracer):
        with pytest.raises(ValueError, match="still propagates"):
            with stage_span("test.stage") as span:
                assert isinstance(span, _NullSpan)
                raise ValueError("still propagates")


def test_no_exception_closes_the_span_cleanly_and_returns_a_usable_span():
    with stage_span("test.stage") as span:
        span.set_attribute("quorum.verdict", "PASS")  # must not raise
