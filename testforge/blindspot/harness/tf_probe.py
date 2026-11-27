"""Infection probe imported by the instrumented original module (as _tf_probe_mod;
a single leading underscore, because names starting with two underscores are
mangled inside class bodies).

Each probe evaluates the original and the mutated version of one expression,
records whether their values differ, and returns the ORIGINAL value, so the
program keeps its original behaviour."""

import operator
import re

RECORDS = {"evaluations": 0, "infected": 0, "examples": [], "entries": []}
CURRENT = {"entry": None}   # index of the test-level call in progress (set by the trace plugin)
_OPS = {"Add": operator.add, "Sub": operator.sub, "Mult": operator.mul, "Div": operator.truediv,
        "FloorDiv": operator.floordiv, "Mod": operator.mod, "Pow": operator.pow}


def _r(v):
    try:
        text = repr(v)
    except Exception:
        text = "<unrepresentable>"
    return re.sub(r" at 0x[0-9a-fA-F]+", "", text)[:200]


def _same(a, b):
    try:
        if type(a) is not type(b):
            return False
        return bool(a == b)
    except Exception:
        return _r(a) == _r(b)


def _record(orig, mut, infected):
    RECORDS["evaluations"] += 1
    entry = CURRENT["entry"]
    if infected:
        RECORDS["infected"] += 1
    if entry is not None and entry not in RECORDS["entries"] and len(RECORDS["entries"]) < 200:
        RECORDS["entries"].append(entry)
    same_kind = [e for e in RECORDS["examples"] if e["infected"] == infected]
    if len(same_kind) < 6:
        RECORDS["examples"].append({"original": orig, "mutant": mut, "infected": infected, "entry": entry})


def infect(orig_thunk, mut_thunk):
    try:
        value = orig_thunk()
    except BaseException as e:
        try:
            mut_thunk()
            _record("raises " + type(e).__name__, "no exception", True)
        except BaseException as e2:
            _record("raises " + type(e).__name__, "raises " + type(e2).__name__, type(e) is not type(e2))
        raise
    try:
        mutated = mut_thunk()
        _record(_r(value), _r(mutated), not _same(value, mutated))
    except BaseException as e2:
        _record(_r(value), "raises " + type(e2).__name__, True)
    return value


def ret(value):
    _record(_r(value), "None", value is not None)
    return value


def aug(current, operand, op, mutated_op):
    try:
        a = _OPS[op](current, operand)
    except Exception as e:
        a = "raises " + type(e).__name__
    try:
        b = _OPS[mutated_op](current, operand)
    except Exception as e:
        b = "raises " + type(e).__name__
    _record(_r(a), _r(b), not _same(a, b))
