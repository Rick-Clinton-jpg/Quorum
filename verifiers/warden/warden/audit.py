"""NDJSON audit trail."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    # Prefer local offset if available, else UTC
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLogger:
    def __init__(self, root: Path | str = ".warden"):
        self.root = Path(root)
        self.audit_dir = self.root / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_today(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        return self.audit_dir / f"{day}.ndjson"

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
    ) -> Path:
        record = {
            "timestamp": _now_iso(),
            "agent_id": agent_id,
            "objective": objective,
            "status": status or "",
            "tag": tag,
            "note": note,
            "action": action,
        }
        if extra:
            record.update(extra)

        path = self._path_for_today()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def read(
        self,
        *,
        session: Optional[str] = None,
        tag: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Read recent audit records, optionally filtered."""
        records: list[dict] = []
        files = sorted(self.audit_dir.glob("*.ndjson"), reverse=True)
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if session and rec.get("agent_id") != session:
                    continue
                if tag and rec.get("tag") != tag:
                    continue
                if since:
                    try:
                        ts = datetime.fromisoformat(rec["timestamp"])
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts < since:
                            continue
                    except Exception:
                        pass
                records.append(rec)
                if len(records) >= limit:
                    return records
        return records