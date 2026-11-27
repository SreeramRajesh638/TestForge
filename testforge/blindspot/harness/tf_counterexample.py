"""Counterexample search: find inputs on which the ORIGINAL and the MUTATED
module behave differently (different return value, exception or object state).

Runs in a sandbox process. Reads a JSON request on stdin:
    {"module": str, "entries": [{"fn": qualname, "seeds": [[literal, ...]], "init": [literal, ...] | null,
                                 "kinds": [kind, ...]}],
     "boundaries": [number, ...], "max_candidates": int, "hypothesis_examples": int,
     "call_timeout_s": float}
and prints one JSON object with the findings.

Search order (cheapest and most explainable first):
1. the inputs the existing tests used ("seeds");
2. single-argument neighbours of the seeds: +-1, the boundary constants of the
   mutated code (c-1, c, c+1), 0, -1, empty/shortened strings and lists, flipped
   booleans;
3. property-based search with Hypothesis over the argument types, which also
   shrinks a found input to a minimal one.
"""

import ast
import importlib.util
import json
import os
import re
import sys
import threading
import time

REQ = json.loads(sys.stdin.read())
LIMIT = float(REQ.get("call_timeout_s", 2.0))
STATE = {"started": None, "tried": 0}
RESULT = {"entries": [], "hypothesis_available": True, "timed_out": False}


def _emit_and_exit(code=0):
    sys.stdout.write(json.dumps(RESULT) + "\n")
    sys.stdout.flush()
    os._exit(code)


def watchdog():
    while True:
        time.sleep(0.05)
        s = STATE["started"]
        if s is not None and time.monotonic() - s > LIMIT:
            RESULT["timed_out"] = True
            _emit_and_exit(0)


threading.Thread(target=watchdog, daemon=True).start()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ORIG = load(os.path.join("orig", REQ["module"] + ".py"), "tf_original")
MUT = load(os.path.join("mut", REQ["module"] + ".py"), "tf_mutant")


def norm(v):
    try:
        text = repr(v)
    except Exception:
        text = "<unrepresentable>"
    return re.sub(r" at 0x[0-9a-fA-F]+", "", text)[:300]


def literal(v):
    try:
        text = repr(v)
        if len(text) <= 400 and (ast.literal_eval(text) == v or v != v):
            return text
    except Exception:
        pass
    return None


def outcome(mod, fn, args, init):
    STATE["started"] = time.monotonic()
    obj = None
    try:
        if "." in fn:
            cls_name, meth = fn.split(".", 1)
            obj = getattr(mod, cls_name)(*init)
            value = getattr(obj, meth)(*args)
        else:
            value = getattr(mod, fn)(*args)
        res = {"kind": "value", "repr": norm(value), "literal": literal(value)}
    except BaseException as e:  # noqa: BLE001
        res = {"kind": "exception", "repr": type(e).__name__, "literal": None}
    finally:
        STATE["started"] = None
    if obj is not None:
        try:
            d = {k: v for k, v in vars(obj).items() if not k.startswith("__")}
            res["state"], res["state_literal"] = norm(d), literal(d)
        except TypeError:
            pass
    return res


def differs(a, b):
    return (a["kind"], a["repr"], a.get("state")) != (b["kind"], b["repr"], b.get("state"))


def neighbours(value, boundaries):
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        out = [value - 1, value + 1, 0, -1, -value]
        for b in boundaries:
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                out += [int(b) - 1, int(b), int(b) + 1]
        return out
    if isinstance(value, float):
        out = [value - 1, value + 1, 0.0, -value, value + 0.5]
        for b in boundaries:
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                out += [b - 1, float(b), b + 1, b - 0.01, b + 0.01]
        return out
    if isinstance(value, str):
        return ["", value[:-1], value + "x", value.upper(), value.lower(), " " + value, value[::-1]]
    if isinstance(value, list):
        return [[], value[:-1], value + value[-1:], list(reversed(value))]
    if isinstance(value, tuple):
        return [(), value[:-1]]
    if value is None:
        return [0, ""]
    return []


DEFAULTS = {"int": 1, "float": 1.0, "str": "a", "bool": True, "list": [1], "none": None}


def size(args):
    total = 0
    for a in args:
        if isinstance(a, bool):
            total += 1
        elif isinstance(a, (int, float)):
            total += abs(a) + 1
        else:
            total += len(repr(a))
    return total


def strategy_for(value, kind):
    from hypothesis import strategies as st
    probe = value if value is not None else DEFAULTS.get(kind)
    if isinstance(probe, bool):
        return st.booleans()
    if isinstance(probe, int):
        return st.integers(-10_000, 10_000)
    if isinstance(probe, float):
        return st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False)
    if isinstance(probe, str):
        return st.text(max_size=12)
    if isinstance(probe, list):
        return st.lists(st.integers(-1000, 1000), max_size=8)
    return None


def search(entry):
    fn, init = entry["fn"], [ast.literal_eval(x) for x in (entry.get("init") or [])]
    seeds = []
    for s in entry.get("seeds") or []:
        try:
            seeds.append([ast.literal_eval(x) for x in s])
        except (ValueError, SyntaxError):
            continue
    kinds = entry.get("kinds") or []
    if not seeds and kinds:
        seeds = [[DEFAULTS.get(k, 1) for k in kinds]]
    report = {"fn": fn, "tried": 0, "found": [], "method": None, "init": entry.get("init") or []}
    RESULT["entries"].append(report)  # kept even if the watchdog stops the search

    def check(args, source):
        if report["tried"] >= REQ.get("max_candidates", 400):
            return False
        report["tried"] += 1
        a = outcome(ORIG, fn, args, init)
        b = outcome(MUT, fn, args, init)
        if differs(a, b):
            texts = [literal(x) for x in args]
            if None in texts:
                return False
            report["found"].append({"args": texts, "original": a, "mutant": b, "source": source,
                                    "size": size(args)})
            return True
        return False

    if REQ.get("only_seeds"):
        # "Run this input": evaluate exactly the given inputs, report both outcomes
        report["evaluated"] = []
        for args in seeds:
            report["tried"] += 1
            a = outcome(ORIG, fn, args, init)
            b = outcome(MUT, fn, args, init)
            report["evaluated"].append({"args": [literal(x) for x in args], "original": a, "mutant": b,
                                        "differs": differs(a, b)})
        report["method"] = "user"
        return report

    seen = set()
    candidates = []
    for s in seeds:
        candidates.append((s, "input used by an existing test"))
    for s in seeds:
        for i, v in enumerate(s):
            for n in neighbours(v, REQ.get("boundaries", [])):
                c = list(s)
                c[i] = n
                candidates.append((c, "neighbour of a tested input (boundary analysis)"))
    for args, source in candidates:
        key = repr(args)
        if key in seen:
            continue
        seen.add(key)
        check(args, source)
        if len(report["found"]) >= 6:
            break
    if report["found"]:
        report["method"] = "boundary"
        return report

    # Property-based search with Hypothesis (shrinks to a minimal example).
    try:
        from hypothesis import HealthCheck, find, settings
        from hypothesis import strategies as st
        from hypothesis.errors import NoSuchExample, Unsatisfiable
    except ImportError:
        RESULT["hypothesis_available"] = False
        return report
    template = seeds[0] if seeds else []
    strats = [strategy_for(v, kinds[i] if i < len(kinds) else None) for i, v in enumerate(template)]
    if not strats or any(s is None for s in strats):
        report["method"] = "hypothesis-unsupported-types"
        return report
    before = report["tried"]

    def pred(args):
        report["tried"] += 1
        a = outcome(ORIG, fn, list(args), init)
        b = outcome(MUT, fn, list(args), init)
        return differs(a, b) and all(literal(x) is not None for x in args)

    try:
        example = find(st.tuples(*strats), pred,
                       settings=settings(max_examples=REQ.get("hypothesis_examples", 300), deadline=None,
                                         database=None, suppress_health_check=list(HealthCheck)))
        args = list(example)
        report["found"].append({"args": [literal(x) for x in args], "original": outcome(ORIG, fn, args, init),
                                "mutant": outcome(MUT, fn, args, init),
                                "source": "Hypothesis property-based search (shrunk to a minimal input)",
                                "size": size(args)})
    except (NoSuchExample, Unsatisfiable):
        pass
    report["method"] = "hypothesis"
    report["hypothesis_tried"] = report["tried"] - before
    return report


for entry in REQ["entries"]:
    search(entry)
_emit_and_exit(0)
