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

from gate import otel_tracing
from gate.otel_tracing import _NullSpan, current_trace_id, stage_span


@pytest.fixture(autouse=True)
def _reset_tracer_singleton():
    """_init_tracer() memoizes itself in module globals (deliberately -
    see its own docstring) - tests that need it to actually run its
    exporter-selection logic again must reset that memoization first, or
    every test after the first one just replays whatever the first test
    happened to initialize."""
    otel_tracing._tracer = None
    otel_tracing._init_attempted = False
    yield
    otel_tracing._tracer = None
    otel_tracing._init_attempted = False


def _exporter_types(provider) -> list[type]:
    """White-box: TracerProvider has no public API to inspect which
    exporter(s) it ended up with, so this reaches into the same private
    attribute the OTel SDK itself uses internally - the only way to
    verify "did _init_tracer() actually wire up exporter X", short of
    a live network call to Cloud Trace."""
    return [sp.span_exporter.__class__ for sp in provider._active_span_processor._span_processors]


def _init_tracer_and_capture_provider():
    """Runs the real _init_tracer() but intercepts the provider it
    builds via opentelemetry.trace.set_tracer_provider, rather than
    reading back opentelemetry.trace.get_tracer_provider() - the real
    global provider can only be set ONCE per process (every call after
    the first is a silent no-op with a logged warning), so asserting
    against the live global would only ever reflect whichever test in
    the whole suite happened to initialize it first, not what THIS
    call actually built."""
    captured: list = []
    with patch("opentelemetry.trace.set_tracer_provider", side_effect=captured.append):
        tracer = otel_tracing._init_tracer()
    assert len(captured) == 1, "expected _init_tracer() to call set_tracer_provider exactly once"
    return tracer, captured[0]


def test_local_dev_uses_console_exporter_not_cloud_trace(monkeypatch):
    """Zero test coverage existed for this before this fix - the
    module's own docstring claims a specific exporter-selection
    contract ("Falls back to a local console exporter... when Cloud
    Trace isn't reachable") that nothing verified directly."""
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    monkeypatch.delenv("K_SERVICE", raising=False)
    tracer, provider = _init_tracer_and_capture_provider()

    assert tracer is not None
    assert _exporter_types(provider) == [ConsoleSpanExporter]


def test_on_cloud_run_with_a_working_exporter_uses_cloud_trace(monkeypatch):
    """The other half of the contract: K_SERVICE set (a real Cloud Run
    container) and a constructible exporter must actually route spans
    to Cloud Trace, not silently stay on the console fallback."""
    monkeypatch.setenv("K_SERVICE", "quorum-coordinator")
    fake_exporter = MagicMock(name="CloudTraceSpanExporter instance")
    with patch("opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter", return_value=fake_exporter):
        tracer, provider = _init_tracer_and_capture_provider()

    assert tracer is not None
    exporters = [sp.span_exporter for sp in provider._active_span_processor._span_processors]
    assert exporters == [fake_exporter]


def test_cloud_trace_exporter_failure_falls_back_to_console(monkeypatch):
    """Confirmed by direct local testing (see engine.py's corrected
    comment): CloudTraceSpanExporter() raises
    google.auth.exceptions.DefaultCredentialsError synchronously when no
    real GCP credentials are present - even inside K_SERVICE, that must
    degrade to the console exporter, not propagate and take tracing (or
    worse, the gate) down with it."""
    from google.auth.exceptions import DefaultCredentialsError
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    monkeypatch.setenv("K_SERVICE", "quorum-coordinator")
    with patch(
        "opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter",
        side_effect=DefaultCredentialsError("simulated: no ADC available"),
    ):
        tracer, provider = _init_tracer_and_capture_provider()

    assert tracer is not None, "a real Cloud Trace construction failure must not leave tracing permanently disabled"
    assert _exporter_types(provider) == [ConsoleSpanExporter]


def test_current_trace_id_is_none_with_no_active_span(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert current_trace_id() is None


def test_current_trace_id_matches_the_active_span_inside_stage_span(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    with stage_span("test.stage") as span:
        expected = format(span.get_span_context().trace_id, "032x")
        assert current_trace_id() == expected
        assert len(expected) == 32


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
