"""Real-time telemetry: what the system is actually doing.

Every LLM call, pytest run, mutant run, probe, UI-automation scenario and
HTTP request is recorded as an event with its real duration and outcome.
The dashboard's System Monitor streams these events; nothing is simulated.

Events live in a bounded in-memory ring buffer (no database, no disk), so
they cost almost nothing and disappear when the server stops.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator

# Components of the running system (nodes in the monitor diagram).
NODES = ("browser", "server", "orchestrator", "llm", "pytest", "mutation", "playwright")

_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=600)
_subscribers: list[queue.Queue] = []
_seq = itertools.count(1)
_local = threading.local()
_started = time.time()
_totals: dict[str, dict[str, float]] = {}


def set_run(run_id: str | None) -> None:
    """Associate events emitted from this thread with a run."""
    _local.run_id = run_id


def current_run() -> str | None:
    return getattr(_local, "run_id", None)


def emit(kind: str, source: str, target: str, *, duration_ms: float | None = None,
         ok: bool = True, detail: str = "", **extra: Any) -> dict:
    """Record one real event: ``source`` talked to ``target``."""
    event = {"seq": next(_seq), "t": time.time(), "kind": kind, "source": source, "target": target,
             "duration_ms": None if duration_ms is None else round(duration_ms, 1), "ok": ok,
             "detail": detail[:300], "run_id": extra.pop("run_id", None) or current_run(), **extra}
    with _lock:
        _events.append(event)
        t = _totals.setdefault(target, {"calls": 0, "errors": 0, "total_ms": 0.0, "timed": 0})
        t["calls"] += 1
        t["errors"] += 0 if ok else 1
        if duration_ms is not None:
            t["total_ms"] += duration_ms
            t["timed"] += 1
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass  # a slow viewer must never slow the system down
    return event


@contextmanager
def span(kind: str, source: str, target: str, detail: str = "", **extra: Any) -> Iterator[dict]:
    """Time a block; record it as an event (ok=False if it raised).
    The block may set ``info["ok"]`` / ``info["detail"]`` / extra fields."""
    info: dict = {"ok": True, "detail": detail}
    start = time.perf_counter()
    try:
        yield info
    except Exception as e:
        info["ok"] = False
        info["detail"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        ok = info.pop("ok")
        text = info.pop("detail")
        emit(kind, source, target, duration_ms=(time.perf_counter() - start) * 1000, ok=ok,
             detail=text, **extra, **info)


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=1000)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def snapshot(limit: int = 200) -> dict:
    """Recent events plus per-component totals since the server started."""
    with _lock:
        events = list(_events)[-limit:]
        totals = {k: {"calls": int(v["calls"]), "errors": int(v["errors"]),
                      "avg_ms": round(v["total_ms"] / v["timed"], 1) if v["timed"] else None}
                  for k, v in _totals.items()}
    return {"started": _started, "uptime_s": round(time.time() - _started, 1),
            "events": events, "totals": totals}


def reset() -> None:
    """Testing helper."""
    with _lock:
        _events.clear()
        _totals.clear()


def instrument_llm(llm) -> None:
    """Wrap an LLM instance's ``complete`` so every call is recorded with its
    provider, model, agent role, latency, token counts and outcome."""
    if getattr(llm, "_tf_instrumented", False):
        return
    original = llm.complete
    provider = type(llm).__name__.replace("Client", "").replace("LLM", "").lower() or "llm"
    provider = {"mock": "mock", "openaicompat": getattr(llm, "provider", "openai-compatible")}.get(provider, provider)
    model = getattr(llm, "model", provider)

    def complete(role, system, prompt, schema, context=None):
        before_in, before_out = llm.usage.input_tokens, llm.usage.output_tokens
        with span("llm_call", "orchestrator", "llm", detail=f"{role} → {schema.__name__}",
                  role=role, provider=provider, model=model) as info:
            result = original(role, system, prompt, schema, context)
            info["tokens_in"] = llm.usage.input_tokens - before_in
            info["tokens_out"] = llm.usage.output_tokens - before_out
            return result

    llm.complete = complete
    llm._tf_instrumented = True
