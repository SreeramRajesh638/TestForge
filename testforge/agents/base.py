from __future__ import annotations

from typing import Callable

from ..config import Config
from ..llm import LLM

EventFn = Callable[[str, dict], None]


class Agent:
    """Base class: every agent has a name, the shared LLM and config, and an
    event callback used for logging and the live dashboard."""

    name = "agent"
    emoji = "🤖"

    def __init__(self, llm: LLM, config: Config, emit: EventFn | None = None) -> None:
        self.llm = llm
        self.config = config
        self._emit = emit or (lambda kind, data: None)

    def log(self, message: str, **data) -> None:
        self._emit("agent", {"agent": self.name, "emoji": self.emoji, "message": message, **data})
