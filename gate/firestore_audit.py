"""Firestore-backed audit log - drop-in for warden.audit.AuditLogger's
real interface. New, Quorum-owned code; does not modify the vendored
warden package (verifiers/warden/).

Matches AuditLogger's ACTUAL signatures (verifiers/warden/warden/audit.py):
  log(*, agent_id, objective, status, tag, note, action="NONE", extra=None)
  read(*, session=None, tag=None, since=None, limit=200) -> list[dict]

Not log(event_type, payload)/read(limit) - those don't match what
gate/quorum_gate.py's run_gate() actually calls (three audit.log(...)
call sites, all keyword args: agent_id/objective/status/tag/note/extra -
see run_gate() itself). Swapping in a logger with the wrong signature
there raises TypeError on the very first stage of the very first gate
run, not a graceful-degradation case - this is why the interface has to
match exactly, not just "look similar."

Falls back to the real, unmodified warden.audit.AuditLogger (local
NDJSON) if google-cloud-firestore isn't installed, Firestore init fails,
or a live Firestore write/read fails - not just at construction time.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from google.cloud import firestore

    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FirestoreAuditLogger:
    """Same log()/read() interface as warden.audit.AuditLogger. Backed by
    Firestore when available; falls back to the real AuditLogger
    (local NDJSON) otherwise, including on a live Firestore error."""

    def __init__(
        self,
        collection_name: str = "quorum_audit_logs",
        project: Optional[str] = None,
        fallback_root: Optional[Path | str] = None,
    ):
        self.collection_name = collection_name
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.db = None

        if FIRESTORE_AVAILABLE:
            try:
                self.db = firestore.Client(project=self.project)
            except Exception as exc:  # noqa: BLE001 - any init failure means "use fallback"
                logger.warning("Firestore client init failed (%s) - falling back to local disk logging.", exc)

        from warden.audit import AuditLogger  # real, vendored - imported, not modified

        self._fallback = AuditLogger(root=fallback_root or ".quorum")

    def log(
        self,
        *,
        agent_id: str,
        objective: str,
        status: str,
        tag: str,
        note: str,
        action: str = "NONE",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict:
        record = {
            "timestamp": _now_iso(),
            "agent_id": agent_id,
            "objective": objective,
            "status": status,
            "tag": tag,
            "note": note,
            "action": action,
        }
        if extra:
            record.update(extra)

        if self.db is not None:
            try:
                self.db.collection(self.collection_name).document().set(record)
                return record
            except Exception as exc:  # noqa: BLE001 - any write failure means "use fallback for this record"
                logger.error("Firestore write failed (%s) - falling back to disk for this record.", exc)

        self._fallback.log(
            agent_id=agent_id,
            objective=objective,
            status=status,
            tag=tag,
            note=note,
            action=action,
            extra=extra,
        )
        return record

    def read(
        self,
        *,
        session: Optional[str] = None,
        tag: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 200,
    ) -> list[dict]:
        if self.db is not None:
            try:
                query = self.db.collection(self.collection_name)
                if session:
                    query = query.where("agent_id", "==", session)
                if tag:
                    query = query.where("tag", "==", tag)
                if since:
                    query = query.where("timestamp", ">=", since.isoformat())
                query = query.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
                return [doc.to_dict() for doc in query.stream()]
            except Exception as exc:  # noqa: BLE001 - any read failure means "use fallback"
                logger.error("Firestore read failed (%s) - reading from local fallback.", exc)

        return self._fallback.read(session=session, tag=tag, since=since, limit=limit)
