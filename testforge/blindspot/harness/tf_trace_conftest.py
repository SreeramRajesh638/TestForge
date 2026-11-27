"""pytest plugin (installed as conftest.py) that records every *test-level*
call into the module under test: arguments, result or exception, and the
object's state after a method call. Calls made from inside the module are not
recorded separately: what a test can observe is what its own calls return.

Configured by the TF_TRACE_CFG environment variable (JSON):
    {"module": str, "functions": [qualname...], "out": path}
"""

import ast
import functools
import importlib
import inspect
import json
import os
import re

CFG = json.loads(os.environ["TF_TRACE_CFG"])
STATE = {"test": None, "depth": 0}
CALLS = []
INITS = {}
OUTCOMES = {}


def _norm(v):
    try:
        text = repr(v)
    except Exception:
        text = "<unrepresentable %s>" % type(v).__name__
    return re.sub(r" at 0x[0-9a-fA-F]+", "", text)[:300]


def _literal(v):
    try:
        text = repr(v)
        if len(text) <= 400 and (ast.literal_eval(text) == v or v != v):
            return text
    except Exception:
        pass
    return None


def _state(obj):
    try:
        d = {k: v for k, v in vars(obj).items() if not k.startswith("__")}
    except TypeError:
        return None, None
    return _norm(d), _literal(d)


def _probe_entry(idx):
    try:
        import _tf_probe
        _tf_probe.CURRENT["entry"] = idx
    except ImportError:
        pass


def _wrap(fn, qual, is_method):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if STATE["depth"] > 0:
            return fn(*args, **kwargs)
        STATE["depth"] += 1
        shown = args[1:] if is_method else args
        rec = {"i": len(CALLS), "test": STATE["test"], "fn": qual,
               "args": [_norm(a) for a in shown], "args_literal": [_literal(a) for a in shown],
               "kwargs": {k: _literal(v) for k, v in kwargs.items()},
               "obj": id(args[0]) if is_method and args else None}
        CALLS.append(rec)
        _probe_entry(rec["i"])
        try:
            out = fn(*args, **kwargs)
            rec["result"], rec["result_literal"], rec["exc"] = _norm(out), _literal(out), None
            return out
        except BaseException as e:
            rec["result"], rec["result_literal"], rec["exc"] = None, None, type(e).__name__
            raise
        finally:
            if is_method and args:
                rec["state"], rec["state_literal"] = _state(args[0])
            STATE["depth"] -= 1
            _probe_entry(None)
    return wrapper


def _wrap_init(fn, cls_name):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if STATE["depth"] == 0:
            INITS[str(id(self))] = {"cls": cls_name, "args_literal": [_literal(a) for a in args],
                                    "kwargs": {k: _literal(v) for k, v in kwargs.items()}}
        return fn(self, *args, **kwargs)
    return wrapper


def pytest_configure(config):
    mod = importlib.import_module(CFG["module"])
    done_init = set()
    for qual in CFG["functions"]:
        try:
            if "." in qual:
                cls_name, name = qual.split(".", 1)
                cls = getattr(mod, cls_name)
                raw = cls.__dict__.get(name)
                if cls_name not in done_init and isinstance(cls.__dict__.get("__init__"), type(_wrap)):
                    cls.__init__ = _wrap_init(cls.__dict__["__init__"], cls_name)
                    done_init.add(cls_name)
                if not inspect.isfunction(raw) or name.startswith("__"):
                    continue
                if inspect.isgeneratorfunction(raw) or inspect.iscoroutinefunction(raw):
                    continue
                setattr(cls, name, _wrap(raw, qual, True))
            else:
                raw = getattr(mod, qual, None)
                if not inspect.isfunction(raw) or inspect.isgeneratorfunction(raw) or inspect.iscoroutinefunction(raw):
                    continue
                setattr(mod, qual, _wrap(raw, qual, False))
        except Exception:
            continue


def pytest_runtest_setup(item):
    STATE["test"] = item.name


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.failed):
        OUTCOMES[report.nodeid.split("::")[-1]] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    probe = None
    try:
        import _tf_probe
        probe = _tf_probe.RECORDS
    except ImportError:
        pass
    with open(CFG["out"], "w", encoding="utf-8") as f:
        json.dump({"calls": CALLS, "inits": INITS, "outcomes": OUTCOMES, "probe": probe}, f)
