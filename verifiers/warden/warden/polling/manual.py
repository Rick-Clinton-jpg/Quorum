"""Manual / fallback polling — user pastes the status line."""

from typing import Optional

from .base import PollingStrategy


class ManualInputStrategy(PollingStrategy):
    """Prompt the user to paste the latest WARDEN_STATUS."""

    def poll(self, session_id: str) -> Optional[str]:
        try:
            raw = input(
                f"[warden] Paste latest WARDEN_STATUS for session '{session_id}' "
                "(or empty to skip): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if not raw:
            return None

        # Accept either the full "WARDEN_STATUS: ..." line or just the text
        if raw.startswith("WARDEN_STATUS:"):
            return self.extract_status(raw)
        return raw