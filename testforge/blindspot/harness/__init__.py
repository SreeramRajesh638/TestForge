"""Scripts copied into a sandbox workspace for Blind Spot evidence runs.
They run in the child process, never inside the TestForge server."""

from pathlib import Path

HERE = Path(__file__).parent


def text(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")
