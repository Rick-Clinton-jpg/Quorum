"""OpenTelemetry tracing for the gate's own stages - new, Quorum-owned
code, layered alongside Warden's existing audit log, never replacing it.

Closes a real observability gap: production agent systems need
end-to-end reasoning-chain traces, not just a log line. Warden's own
audit trail
(gate/quorum_gate.py's audit.log() calls) already IS a real reasoning-
chain record - one entry per Sentry/IntentGraph/Kernel stage, win or
lose - it just isn't OTel-formatted. This module exports that same
per-stage information as real OTel spans instead, viewable in Cloud
Trace, without touching or duplicating Warden's own log calls.

Exports to Google Cloud Trace when the process has real GCP credentials
(Cloud Run's attached service account, once granted roles/cloudtrace.agent
- no separate credential wiring needed, same ADC path Firestore already
uses). Falls back to a local console exporter - never to nothing - when
Cloud Trace isn't reachable (local dev, missing IAM binding), so tracing
output is always inspectable somewhere. A tracing failure must never take
down the gate itself: every failure mode here is caught and logged, same
graceful-degradation shape as gate/firestore_audit.py's own fallback.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_tracer: Optional[Any] = None
_init_attempted = False


def _init_tracer() -> Optional[Any]:
    """Builds the module-level tracer once, lazily - not at import time,
    so importing this module never has side effects on its own (matches
    how firestore_audit.py/firestore_intent.py defer their own client
    construction to __init__, not module load)."""
    global _tracer, _init_attempted
    if _init_attempted:
        return _tracer
    _init_attempted = True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError:
        logger.warning("opentelemetry not installed - gate stages will not be traced.")
        return None

    resource = Resource.create({"service.name": "quorum-coordinator"})
    provider = TracerProvider(resource=resource)

    # K_SERVICE is set by Cloud Run on every real container, never set
    # locally - confirmed live: CloudTraceSpanExporter's own constructor
    # doesn't eagerly validate credentials, so without this check a local
    # run (pytest, `uvicorn --reload`) would only discover it has no real
    # GCP credentials later, in a background export thread, producing a
    # noisy but harmless traceback instead of the clean console fallback
    # this is supposed to be.
    on_cloud_run = bool(os.environ.get("K_SERVICE"))

    if on_cloud_run:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            provider.add_span_processor(SimpleSpanProcessor(CloudTraceSpanExporter()))
            logger.info("OTel tracing exporting to Google Cloud Trace.")
        except Exception as exc:  # noqa: BLE001 - any init failure means "use console fallback"
            logger.warning(
                "Cloud Trace exporter unavailable (%s) - falling back to console span export.", exc
            )
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("quorum.gate")
    return _tracer


class _NullSpan:
    """Returned when tracing is unavailable, so callers can unconditionally
    call `span.set_attribute(...)` without an `if span:` check at every
    call site."""

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@contextmanager
def stage_span(name: str, **attributes: Any) -> Iterator[Any]:
    """One OTel span per gate stage or per full gate run - yields the
    span itself, so the caller can attach outcome attributes (verdict,
    status) once the stage's actual result is known, the same way
    Warden's audit.log() records outcome after the fact rather than
    only at entry. Tracing is strictly additive: any failure in
    SETTING UP tracing is caught and logged, never raised, so a tracing
    outage can never turn into a gate outage.

    Deliberately does NOT wrap `yield span` in the same try/except as
    span setup. Confirmed live (external parallel review): a real
    exception raised by the CALLER's own code inside `with
    stage_span(...):` gets thrown into this generator at the yield
    point - catching that here and yielding _NullSpan() again violates
    the @contextmanager protocol (a generator that catches an injected
    exception must stop, not yield again) and Python raises
    `RuntimeError: generator didn't stop after throw()` instead,
    completely replacing whatever the caller's real failure was. "Tracing
    is additive" means a tracing SETUP failure can't break the gate - it
    never meant a real gate-logic exception should be swallowed and
    replaced by a confusing, unrelated one.
    """
    tracer = _init_tracer()
    if tracer is None:
        yield _NullSpan()
        return

    try:
        span_cm = tracer.start_as_current_span(name)
        span = span_cm.__enter__()
        for key, value in attributes.items():
            if value is None:
                continue
            span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
    except Exception as exc:  # noqa: BLE001 - span *setup* failed; tracing is additive
        logger.warning("OTel span %r failed to start (%s) - continuing without tracing for this stage.", name, exc)
        yield _NullSpan()
        return

    try:
        yield span
    except BaseException:
        # The caller's own code raised inside the `with` block - record
        # it on the span (best-effort, itself guarded so a tracing
        # failure here can't mask it either), then always re-raise the
        # real exception, unmodified.
        try:
            span_cm.__exit__(*sys.exc_info())
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            span_cm.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTel span %r failed to close cleanly (%s)", name, exc)
