"""Executes functions of the module under test in a *separate process* and
records what they return. Used by the offline mock backend to build
characterization (regression-oracle) tests.

Input:  JSON on stdin {"module": str, "calls": [{"qualname", "args": [repr...], "init": [...]}],
                       "call_timeout_s": float}
Output: one JSON object per line, in call order:
        {"ok": bool, "value": repr | None, "exc": str | None}
If a call runs longer than call_timeout_s, a watchdog thread prints
{"timeout": true} and ends the process with exit code 3; the caller marks
that call as hanging and restarts the probe with the remaining calls.
(A thread is used instead of signals so this also works on Windows.)
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import threading
import time

TIMEOUT_EXIT = 3


def _literal(value) -> str | None:
    """repr(value) if it round-trips through ast.literal_eval, else None."""
    try:
        text = repr(value)
        if ast.literal_eval(text) == value or value != value:  # NaN
            return text
    except Exception:
        pass
    return None


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    req = json.loads(sys.stdin.read())
    limit = float(req.get("call_timeout_s", 3.0))
    mod = importlib.import_module(req["module"])

    state = {"started": None}

    def watchdog() -> None:
        while True:
            time.sleep(0.05)
            started = state["started"]
            if started is not None and time.monotonic() - started > limit:
                _emit({"timeout": True})
                os._exit(TIMEOUT_EXIT)

    threading.Thread(target=watchdog, daemon=True).start()

    for call in req["calls"]:
        try:
            args, kwargs = [], {}
            for a in call["args"]:  # literals produced by us; "name=value" = keyword
                name, sep, rest = a.partition("=")
                if sep and name.isidentifier() and not rest.startswith("="):
                    kwargs[name] = eval(rest, {})
                else:
                    args.append(eval(a, {}))
            target = call["qualname"]
            state["started"] = time.monotonic()
            if "." in target:
                cls_name, meth = target.split(".", 1)
                obj = getattr(mod, cls_name)(*[eval(a, {}) for a in call.get("init", [])])
                fn = getattr(obj, meth)
            else:
                fn = getattr(mod, target)
            value = fn(*args, **kwargs)
            state["started"] = None
            _emit({"ok": True, "value": _literal(value), "exc": None})
        except Exception as e:  # noqa: BLE001 - we record any exception type
            state["started"] = None
            _emit({"ok": False, "value": None, "exc": type(e).__name__})
    sys.stdout.flush()
    os._exit(0)  # don't wait for threads the module under test may have started


if __name__ == "__main__":
    main()
