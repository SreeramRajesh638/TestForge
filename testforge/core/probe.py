"""Executes functions of the module under test in a *separate process* and
records what they return. Used by the offline mock backend to build
characterization (regression-oracle) tests.

Input:  JSON on stdin {"module": str, "calls": [{"qualname", "args": [repr...]}]}
Output: JSON list of {"ok": bool, "value": repr | None, "exc": str | None}
"""

from __future__ import annotations

import ast
import importlib
import json
import sys


def _literal(value) -> str | None:
    """repr(value) if it round-trips through ast.literal_eval, else None."""
    try:
        text = repr(value)
        if ast.literal_eval(text) == value or value != value:  # NaN
            return text
    except Exception:
        pass
    return None


def main() -> None:
    req = json.loads(sys.stdin.read())
    mod = importlib.import_module(req["module"])
    out = []
    for call in req["calls"]:
        try:
            args = [eval(a, {}) for a in call["args"]]  # literals produced by us
            target = call["qualname"]
            if "." in target:
                cls_name, meth = target.split(".", 1)
                obj = getattr(mod, cls_name)(*[eval(a, {}) for a in call.get("init", [])])
                fn = getattr(obj, meth)
            else:
                fn = getattr(mod, target)
            value = fn(*args)
            out.append({"ok": True, "value": _literal(value), "exc": None})
        except Exception as e:  # noqa: BLE001 - we record any exception type
            out.append({"ok": False, "value": None, "exc": type(e).__name__})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
