"""AST instrumentation for Blind Spot evidence.

Two jobs:

1. **Locate** a mutant's node in the original source, using the same walk as
   the mutation engine (``Mutant.node_index``), and derive its *behavioural
   category* (boundary condition, conditional logic, ...) from the syntax
   around it.
2. **Instrument infection**: produce a copy of the *original* module in which
   the mutated expression is evaluated twice, once as written and once as the
   mutant would compute it, recording whether the two values differ. The
   module still returns the original value, so the program behaves as the
   original does. This measures the "Infection" step of the RIPR model
   (Reachability, Infection, Propagation, Revealability) at the mutation point.

Infection can only be measured safely when evaluating the expression twice
has no side effects. Expressions containing calls, ``await``, ``yield`` or
``:=`` are therefore *not* instrumented and reported as unsupported; the
classifier then falls back to the other evidence and may report the case as
inconclusive. Nothing is guessed.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

from ..core.mutation import _Mutator, _docstring_nodes
from ..models import Mutant

BEHAVIOUR_CATEGORIES = [
    ("boundary_conditions", "Boundary conditions"),
    ("conditional_logic", "Conditional logic"),
    ("arithmetic", "Arithmetic operations"),
    ("return_value", "Return-value verification"),
    ("exception_handling", "Exception handling"),
    ("input_validation", "Input validation"),
    ("state_transitions", "State transitions"),
    ("assertion_quality", "Assertion quality"),
    ("unexplored_paths", "Unexplored execution paths"),
]

_IMPURE = (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)
_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


@dataclass
class Located:
    tree: ast.Module
    node: ast.AST            # the node the mutation engine replaces
    replacement: ast.AST     # what the mutant puts there
    parents: dict[int, ast.AST]
    stmt_line: int           # first line of the enclosing statement (coverage granularity)

    def ancestors(self, node: ast.AST | None = None):
        cur = self.parents.get(id(node if node is not None else self.node))
        while cur is not None:
            yield cur
            cur = self.parents.get(id(cur))


def locate(source: str, mutant: Mutant) -> Located | None:
    """Find the mutant's node in a fresh parse of ``source``."""
    tree = ast.parse(source)
    collector = _Mutator()
    collector.skip = _docstring_nodes(tree)
    collector.visit(tree)
    if not (0 <= mutant.node_index < len(collector.found)):
        return None
    _, _, node, replacement = collector.found[mutant.node_index]
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    loc = Located(tree, node, replacement, parents, getattr(node, "lineno", mutant.lineno))
    if not isinstance(node, ast.stmt):
        for anc in loc.ancestors():
            if isinstance(anc, ast.stmt):
                loc.stmt_line = anc.lineno
                break
    return loc


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(n is target for n in ast.walk(root))


def behaviour_category(mutant: Mutant, loc: Located | None) -> str:
    """Behavioural category from the syntax around the mutation (before any
    diagnosis: the engine moves reachability failures to "unexplored paths"
    and revealability failures to "assertion quality")."""
    if loc is None:
        return "unexplored_paths"
    node = loc.node
    ancestors = list(loc.ancestors())
    for anc in ancestors:
        if isinstance(anc, (ast.ExceptHandler, ast.Raise)):
            return "exception_handling"
    for anc in ancestors:
        if isinstance(anc, ast.If) and _contains(anc.test, node) and \
                any(isinstance(n, ast.Raise) for stmt in anc.body for n in ast.walk(stmt)):
            return "input_validation"
    stmt = node if isinstance(node, ast.stmt) else next((a for a in ancestors if isinstance(a, ast.stmt)), None)
    if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
               for t in targets):
            return "state_transitions"
    op = mutant.operator
    if op == "RET":
        return "return_value"
    if op in ("AOR", "ASR"):
        return "arithmetic"
    if op == "UOI":
        return "arithmetic" if isinstance(getattr(node, "op", None), ast.USub) else "conditional_logic"
    if op == "LCR":
        return "conditional_logic"
    if op == "ROR":
        return "boundary_conditions" if any(isinstance(o, _ORDERING) for o in node.ops) else "conditional_logic"
    if op == "CRP":
        value = getattr(node, "value", None)
        parent = loc.parents.get(id(node))
        if isinstance(value, bool):
            return "conditional_logic"
        if isinstance(value, (int, float)):
            in_compare = isinstance(parent, ast.Compare) or any(isinstance(a, ast.Compare) for a in ancestors[:3])
            return "boundary_conditions" if in_compare else "arithmetic"
        return "return_value"
    return "conditional_logic"


def is_boundary_operator(mutant: Mutant, loc: Located | None) -> bool:
    """True for mutations that move a decision boundary (< vs <=, a threshold constant)."""
    if loc is None:
        return False
    if mutant.operator == "ROR":
        return any(isinstance(o, _ORDERING) for o in getattr(loc.node, "ops", []))
    if mutant.operator == "CRP" and isinstance(getattr(loc.node, "value", None), (int, float)) \
            and not isinstance(loc.node.value, bool):
        return isinstance(loc.parents.get(id(loc.node)), (ast.Compare, ast.BinOp))
    return False


# ---------------------------------------------------------------- infection
def _path(root: ast.AST, target: ast.AST) -> list | None:
    """Field/index path from root to target (None when not found)."""
    if root is target:
        return []
    for field, value in ast.iter_fields(root):
        items = value if isinstance(value, list) else [value]
        for i, item in enumerate(items):
            if isinstance(item, ast.AST):
                sub = _path(item, target)
                if sub is not None:
                    return [(field, i if isinstance(value, list) else None)] + sub
    return None


def _replace_at(root: ast.AST, path: list, new: ast.AST) -> ast.AST:
    root = copy.deepcopy(root)
    if not path:
        return copy.deepcopy(new)
    cur = root
    for field, idx in path[:-1]:
        cur = getattr(cur, field)[idx] if idx is not None else getattr(cur, field)
    field, idx = path[-1]
    if idx is None:
        setattr(cur, field, copy.deepcopy(new))
    else:
        getattr(cur, field)[idx] = copy.deepcopy(new)
    return root


def _lambda(body: ast.expr) -> ast.Lambda:
    return ast.Lambda(args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                                         kw_defaults=[], kwarg=None, defaults=[]), body=body)


def _probe_call(name: str, *args: ast.expr) -> ast.Call:
    return ast.Call(func=ast.Attribute(value=ast.Name("_tf_probe_mod", ast.Load()), attr=name, ctx=ast.Load()),
                    args=list(args), keywords=[])


@dataclass
class Infection:
    source: str | None     # instrumented original module (None: not supported)
    point: str             # what is compared: the expression text
    supported: bool
    reason: str = ""


def infection_source(source: str, mutant: Mutant) -> Infection:
    loc = locate(source, mutant)
    if loc is None:
        return Infection(None, "", False, "mutation point not found in the source")
    node, repl = loc.node, loc.replacement

    # Choose the expression whose value is compared. A changed constant is
    # judged at the comparison/arithmetic it feeds (18 -> 19 always differs as
    # a number; what matters is whether `age >= 18` changes).
    point, mutated_point = node, repl
    parent = loc.parents.get(id(node))
    if mutant.operator == "CRP" and isinstance(parent, (ast.Compare, ast.BinOp, ast.UnaryOp, ast.BoolOp)):
        path = _path(parent, node)
        point, mutated_point = parent, _replace_at(parent, path, repl)

    if isinstance(point, ast.Return):
        if point.value is None:
            return Infection(None, "return", False, "bare return")
        new = ast.Return(_probe_call("ret", point.value))
        target = point
    elif isinstance(point, ast.AugAssign):
        if any(isinstance(n, _IMPURE) for n in ast.walk(point)):
            return Infection(None, ast.unparse(point), False, "augmented assignment calls a function")
        read = copy.deepcopy(point.target)
        for n in ast.walk(read):
            if hasattr(n, "ctx"):
                n.ctx = ast.Load()
        check = ast.Expr(_probe_call("aug", read, copy.deepcopy(point.value),
                                     ast.Constant(type(point.op).__name__),
                                     ast.Constant(type(repl.op).__name__)))
        new = [check, point]
        target = point
    elif isinstance(point, ast.expr):
        if any(isinstance(n, _IMPURE) for n in ast.walk(point)):
            return Infection(None, ast.unparse(point), False,
                             "the expression calls a function, so evaluating it twice could change behaviour")
        if any(isinstance(a, ast.ClassDef) for a in loc.ancestors()) and \
                not any(isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for a in loc.ancestors()):
            return Infection(None, ast.unparse(point), False, "class-level expression")
        new = _probe_call("infect", _lambda(copy.deepcopy(point)), _lambda(copy.deepcopy(mutated_point)))
        target = point
    else:
        return Infection(None, ast.unparse(point), False, "unsupported mutation point")

    class Swap(ast.NodeTransformer):
        def generic_visit(self, n):
            for field, value in ast.iter_fields(n):
                if isinstance(value, list):
                    out = []
                    for item in value:
                        if item is target:
                            out.extend(new if isinstance(new, list) else [new])
                        elif isinstance(item, ast.AST):
                            r = self.visit(item)
                            out.extend(r if isinstance(r, list) else [r])
                        else:
                            out.append(item)
                    value[:] = out
                elif isinstance(value, ast.AST):
                    if value is target:
                        setattr(n, field, new)
                    else:
                        setattr(n, field, self.visit(value))
            return n

    tree = Swap().visit(loc.tree)
    imp = ast.Import(names=[ast.alias(name="_tf_probe", asname="_tf_probe_mod")])
    body = tree.body
    pos = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        pos = 1
    while pos < len(body) and isinstance(body[pos], ast.ImportFrom) and body[pos].module == "__future__":
        pos += 1
    body.insert(pos, imp)
    try:
        text = ast.unparse(ast.fix_missing_locations(tree))
        compile(text, "<instrumented>", "exec")
    except (SyntaxError, ValueError) as e:
        return Infection(None, ast.unparse(point), False, f"instrumentation failed: {e}")
    return Infection(text, ast.unparse(point), True)


# ---------------------------------------------------------------- test analysis
_WEAK_CALLS = {"isinstance", "callable", "hasattr", "len", "bool", "type"}


def analyze_assertions(test_code: str) -> dict[str, dict]:
    """Per test function: number of asserts, which look weak, and whether it
    checks an exception. Used as assertion-quality evidence."""
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return {}
    out: dict[str, dict] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.name.startswith("test"):
            continue
        asserts, weak, raises = 0, [], 0
        for n in ast.walk(fn):
            if isinstance(n, ast.Assert):
                asserts += 1
                t = n.test
                text = ast.unparse(t)
                if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.IsNot) \
                        and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None:
                    weak.append(text)
                elif isinstance(t, (ast.Name, ast.Attribute)) or \
                        (isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id in _WEAK_CALLS):
                    weak.append(text)
                elif isinstance(t, ast.Compare) and isinstance(t.left, ast.Call) and \
                        isinstance(t.left.func, ast.Name) and t.left.func.id == "len" and \
                        isinstance(t.ops[0], (ast.Gt, ast.GtE)) and isinstance(t.comparators[0], ast.Constant) \
                        and t.comparators[0].value in (0, 1):
                    weak.append(text)
            if isinstance(n, ast.With):
                for item in n.items:
                    if "raises" in ast.unparse(item.context_expr):
                        raises += 1
        out[fn.name] = {"asserts": asserts, "weak": weak[:5], "checks_exception": raises > 0,
                        "line": fn.lineno}
    return out
