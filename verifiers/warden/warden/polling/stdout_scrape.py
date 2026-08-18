"""Stdout-scrape strategy for agents run as subprocesses."""

from __future__ import annotations

import subprocess
from typing import Optional

from .base import PollingStrategy


class StdoutScrapeStrategy(PollingStrategy):
    """
    Capture stdout from a running subprocess and extract the latest
    WARDEN_STATUS. Best-effort; interactive TUIs are not fully supported.
    """

    def __init__(self, process: Optional[subprocess.Popen] = None):
        self.process = process
        self._buffer: list[str] = []

    def attach(self, process: subprocess.Popen) -> None:
        self.process = process

    def poll(self, session_id: str) -> Optional[str]:
        if self.process is None or self.process.stdout is None:
            return None

        # Non-blocking read of whatever is currently available
        try:
            # This is deliberately simple; for production you may want
            # a background reader thread.
            import select
            import sys

            if sys.platform != "win32":
                while select.select([self.process.stdout], [], [], 0)[0]:
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    self._buffer.append(
                        line.decode("utf-8", errors="replace")
                        if isinstance(line, bytes)
                        else line
                    )
            else:
                # Windows fallback — just try a non-blocking read
                pass
        except Exception:
            pass

        text = "".join(self._buffer[-200:])  # keep recent history
        return self.extract_status(text)