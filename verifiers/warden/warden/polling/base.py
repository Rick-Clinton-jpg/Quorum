"""Abstract base for pluggable polling strategies."""

from abc import ABC, abstractmethod
from typing import Optional


class PollingStrategy(ABC):
    """Return the latest WARDEN_STATUS line (or None)."""

    @abstractmethod
    def poll(self, session_id: str) -> Optional[str]:
        """
        Return the most recent valid WARDEN_STATUS text, or None if
        no status was found in the polling window.
        """
        pass

    def extract_status(self, text: str) -> Optional[str]:
        """
        Scan text (multi-line or single) for the most recent valid
        WARDEN_STATUS line. Robust to extra whitespace and multiline noise.
        """
        if not text:
            return None

        latest: Optional[str] = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("WARDEN_STATUS:"):
                # Everything after the first colon
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    status = parts[1].strip()
                    # Truncate at first embedded newline (shouldn't happen)
                    status = status.split("\n")[0].strip()
                    if status:
                        latest = status
        return latest