"""Restricted execution for Blind Spot evidence runs.

Every run happens in a fresh temporary directory, in a separate Python
process, with:

* a wall-clock timeout (the process is killed when it expires);
* on POSIX: limits on address space (memory) and CPU time (``resource``);
* outbound network disabled for the code under test: a ``sitecustomize``
  module replaces ``socket.socket.connect`` and ``socket.create_connection``
  with functions that raise ``PermissionError``.

The network block is a guard against accidental network use by the code under
test, not a security boundary: code that is determined to escape a Python
process can. Run TestForge on code you trust or inside a container/VM (see the
README security note).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MEMORY_LIMIT_BYTES = 1536 * 1024 * 1024

_SITECUSTOMIZE = '''
import socket as _s
def _blocked(*a, **k):
    raise PermissionError("network access is disabled inside the TestForge sandbox")
_s.socket.connect = _blocked
_s.socket.connect_ex = _blocked
_s.create_connection = _blocked
'''


@dataclass
class ProcResult:
    returncode: int | None    # None: killed by the timeout
    stdout: str
    stderr: str

    @property
    def timed_out(self) -> bool:
        return self.returncode is None


class Workspace:
    """A temporary directory that is deleted on exit."""

    def __init__(self, prefix: str = "tf_bs_") -> None:
        self.path = Path(tempfile.mkdtemp(prefix=prefix))
        guard = self.path / "_tf_guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")

    def write(self, name: str, text: str) -> Path:
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _limits(cpu_s: int):
    def apply() -> None:  # runs in the child before exec (POSIX only)
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        except (ValueError, OSError):
            pass
    return apply


def run(ws: Workspace, args: list[str], *, timeout_s: float, stdin: str | None = None,
        extra_paths: list[str] | None = None, env_extra: dict | None = None) -> ProcResult:
    """Run ``python <args>`` inside the workspace under the limits above."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ws.path / "_tf_guard"), str(ws.path)] + list(extra_paths or []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.pop("PYTEST_ADDOPTS", None)
    env.update(env_extra or {})
    kwargs = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _limits(int(timeout_s) + 5)
    try:
        proc = subprocess.run([sys.executable, *args], cwd=ws.path, env=env, input=stdin,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, **kwargs)
        return ProcResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        return ProcResult(None, out.decode(errors="replace") if isinstance(out, bytes) else out, "")
