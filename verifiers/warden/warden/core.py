"""Core orchestration — polling loop, session registry, comparison."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import AuditLogger
from .matcher import dual_compare
from .polling import get_strategy, PollingStrategy


def format_result_line(result: dict, *, agent_id: Optional[str] = None) -> str:
    """
    Render a single check_once() result as a one-line status string,
    expanding DIVERGENT results into their word/trigram rater breakdown.
    Shared by run_loop (single-session) and run_daemon (multi-session) so
    both surfaces display DIVERGENT results identically.
    """
    tag = result.get("tag", "?")
    status = result.get("status") or "(none)"
    prefix = f"[{result.get('timestamp', '')}] "
    if agent_id is not None:
        prefix += f"{agent_id:12} "

    if tag == "DIVERGENT":
        word_tag = result.get("word_tag", "?")
        trigram_tag = result.get("trigram_tag", "?")
        return (
            f"{prefix}{tag:9} | word={word_tag} trigram={trigram_tag} | "
            f"{status[:80]}"
        )
    return f"{prefix}{tag:9} | {status[:80]}"


@dataclass
class Session:
    agent_id: str
    agent_type: str
    objective: str
    interval_seconds: int = 600  # 10 min default
    strategy_name: str = "claude-code"
    transcript_path: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    last_checked: Optional[str] = None
    last_status: Optional[str] = None
    last_tag: Optional[str] = None
    last_word_tag: Optional[str] = None
    last_trigram_tag: Optional[str] = None
    active: bool = True


class Warden:
    def __init__(self, root: Path | str = ".warden"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.objectives_dir = self.root / "objectives"
        self.objectives_dir.mkdir(exist_ok=True)
        self.registry_path = self.root / "sessions.json"
        self.audit = AuditLogger(self.root)
        self._sessions: dict[str, Session] = {}
        self._load_registry()

    # ------------------------------------------------------------------
    # Registry persistence
    # ------------------------------------------------------------------
    def _load_registry(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for item in data:
                s = Session(**item)
                self._sessions[s.agent_id] = s
        except Exception:
            pass

    def _save_registry(self) -> None:
        data = [asdict(s) for s in self._sessions.values()]
        self.registry_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _objective_path(self, agent_id: str) -> Path:
        safe = agent_id.replace("/", "_").replace(" ", "_")
        return self.objectives_dir / f"{safe}.objective"

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def watch(
        self,
        *,
        agent_id: str,
        objective: str,
        agent_type: str = "claude-code",
        interval_seconds: int = 600,
        transcript_path: Optional[str] = None,
    ) -> Session:
        """Register a session for monitoring."""
        session = Session(
            agent_id=agent_id,
            agent_type=agent_type,
            objective=objective.strip(),
            interval_seconds=interval_seconds,
            strategy_name=agent_type,
            transcript_path=transcript_path,
            active=True,
        )
        self._sessions[agent_id] = session
        self._objective_path(agent_id).write_text(objective.strip() + "\n", encoding="utf-8")
        self._save_registry()
        return session

    def stop(self, agent_id: str) -> bool:
        if agent_id not in self._sessions:
            return False
        self._sessions[agent_id].active = False
        self._save_registry()
        return True

    def list_sessions(self, active_only: bool = True) -> list[Session]:
        sessions = list(self._sessions.values())
        if active_only:
            sessions = [s for s in sessions if s.active]
        return sessions

    # ------------------------------------------------------------------
    # Single check
    # ------------------------------------------------------------------
    def _get_strategy(self, session: Session) -> PollingStrategy:
        kwargs = {}
        if session.transcript_path:
            kwargs["transcript_path"] = session.transcript_path
        return get_strategy(session.strategy_name, **kwargs)

    def check_once(self, agent_id: str) -> dict:
        """
        Poll one session, compare, log, return result dict.
        """
        session = self._sessions.get(agent_id)
        if session is None:
            return {"error": f"Unknown session: {agent_id}"}

        strategy = self._get_strategy(session)
        status = strategy.poll(session.agent_id)

        result = dual_compare(status or "", session.objective)

        self.audit.log(
            agent_id=session.agent_id,
            objective=session.objective,
            status=status or "",
            tag=result.final_tag,
            note=result.note,
            extra={
                "word_tag": result.word_tag,
                "word_score": round(result.word_score, 3),
                "trigram_tag": result.trigram_tag,
                "trigram_score": round(result.trigram_score, 3),
            },
        )

        session.last_checked = datetime.now().astimezone().isoformat(timespec="seconds")
        session.last_status = status
        session.last_tag = result.final_tag
        session.last_word_tag = result.word_tag
        session.last_trigram_tag = result.trigram_tag
        self._save_registry()

        return {
            "agent_id": session.agent_id,
            "objective": session.objective,
            "status": status,
            "tag": result.final_tag,
            "word_tag": result.word_tag,
            "trigram_tag": result.trigram_tag,
            "note": result.note,
            "timestamp": session.last_checked,
        }

    def check_all(self) -> list[dict]:
        results = []
        for s in self.list_sessions(active_only=True):
            results.append(self.check_once(s.agent_id))
        return results

    # ------------------------------------------------------------------
    # Blocking watch loop
    # ------------------------------------------------------------------
    def run_loop(
        self,
        agent_id: str,
        *,
        once: bool = False,
        interval_override: Optional[int] = None,
    ) -> None:
        """
        Poll a single session on an interval until stopped or Ctrl-C.
        """
        session = self._sessions.get(agent_id)
        if session is None:
            raise ValueError(f"Unknown session: {agent_id}")

        interval = interval_override or session.interval_seconds
        print(
            f"[warden] Watching {agent_id} every {interval}s "
            f"(objective: {session.objective[:60]}…)"
        )
        print("[warden] Ctrl-C to stop.\n")

        try:
            while True:
                result = self.check_once(agent_id)
                print(format_result_line(result))
                if once:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[warden] Stopped.")

    # ------------------------------------------------------------------
    # Multi-session daemon
    # ------------------------------------------------------------------
    def _is_due(self, session: "Session", now: datetime) -> bool:
        if not session.last_checked:
            return True
        try:
            last = datetime.fromisoformat(session.last_checked)
        except ValueError:
            return True
        return (now - last).total_seconds() >= session.interval_seconds

    def run_daemon(self, tick_seconds: int = 30) -> None:
        """
        Single long-running process that checks every ACTIVE registered
        session on its own --interval, sleeping `tick_seconds` between
        scan passes. Reloads the registry each tick so sessions registered
        from another terminal are picked up without restarting the daemon.
        """
        print(f"[warden] Daemon started. Tick={tick_seconds}s. Ctrl-C to stop.\n")
        try:
            while True:
                self._load_registry()
                active = self.list_sessions(active_only=True)
                if not active:
                    print("[warden] No active sessions yet — waiting...")
                now = datetime.now().astimezone()
                for session in active:
                    if self._is_due(session, now):
                        result = self.check_once(session.agent_id)
                        print(format_result_line(result, agent_id=session.agent_id))
                time.sleep(tick_seconds)
        except KeyboardInterrupt:
            print("\n[warden] Daemon stopped.")