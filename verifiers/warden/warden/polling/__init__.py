"""Pluggable polling strategies."""

from .base import PollingStrategy
from .file_watcher import FileWatcherStrategy
from .manual import ManualInputStrategy
from .stdout_scrape import StdoutScrapeStrategy

STRATEGIES = {
    "claude-code": FileWatcherStrategy,
    "claude": FileWatcherStrategy,
    "codex": FileWatcherStrategy,
    "openai-codex": FileWatcherStrategy,
    "subprocess": StdoutScrapeStrategy,
    "manual": ManualInputStrategy,
    "auto": FileWatcherStrategy,
}


def get_strategy(name: str, **kwargs) -> PollingStrategy:
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown polling strategy '{name}'. "
            f"Available: {', '.join(sorted(STRATEGIES))}"
        )
    if name in ("claude-code", "claude", "codex", "openai-codex", "auto"):
        return cls(agent_type=name, **kwargs)
    return cls(**kwargs)


__all__ = [
    "PollingStrategy",
    "FileWatcherStrategy",
    "ManualInputStrategy",
    "StdoutScrapeStrategy",
    "STRATEGIES",
    "get_strategy",
]