"""AST-based mutation testing engine.

We implement our own engine (instead of wrapping mutmut) so that every mutant
has a stable id, operator class, line and before/after snippet. That metadata
is what the Mutation Critic feeds back to the LLM.

Mutation operators (classic names from the literature):
    AOR  arithmetic operator replacement     a + b  ->  a - b
    ROR  relational operator replacement     a < b  ->  a <= b
    LCR  logical connector replacement       a and b -> a or b
    UOI  unary operator deletion             not x  ->  x,  -x -> x
    CRP  constant replacement                10 -> 11, True -> False, "s" -> ""
    RET  return value replacement            return x -> return None
    ASR  augmented assignment replacement    x += 1 -> x -= 1
"""

from __future__ import annotations

import ast
import copy
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..models import Mutant, MutationResult, ModuleInfo
from .analysis import enclosing_function
from .runner import run_pytest_quick

_AOR = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
        ast.FloorDiv: ast.Div, ast.Mod: ast.Mult, ast.Pow: ast.Mult}
_ROR = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
        ast.In: ast.NotIn, ast.NotIn: ast.In}
_LCR = {ast.And: ast.Or, ast.Or: ast.And}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


class _Mutator(ast.NodeTransformer):
    """Walks the tree; in 'collect' mode records candidate mutations, in
    'apply' mode applies only the mutation with index ``target``."""

    def __init__(self, target: int | None = None):
        self.target = target
        self.index = -1
        self.found: list[tuple[str, int, ast.AST, ast.AST]] = []  # op, line, before, after
        self.skip: set[int] = set()
        self._in_annotation = 0

    # --- helpers --------------------------------------------------------
    def _candidate(self, op: str, node: ast.AST, replacement: ast.AST) -> ast.AST:
        self.index += 1
        ast.copy_location(replacement, node)
        if self.target is None:
            self.found.append((op, getattr(node, "lineno", 0), node, replacement))
            return node
        return replacement if self.index == self.target else node

    def visit(self, node):
        # Don't mutate annotations or decorators (no runtime effect / noise).
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = node.decorator_list  # untouched
            node.body = [self.visit(n) for n in node.body]
            node.body = [n for n in node.body if n is not None]
            return node
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                node.value = self.visit(node.value)
            return node
        return super().visit(node)

    # --- operators ------------------------------------------------------
    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        new_op = _AOR.get(type(node.op))
        if new_op:
            return self._candidate("AOR", node, ast.BinOp(copy.deepcopy(node.left), new_op(),
                                                          copy.deepcopy(node.right)))
        return node

    def visit_AugAssign(self, node: ast.AugAssign):
        self.generic_visit(node)
        new_op = _AOR.get(type(node.op))
        if new_op:
            return self._candidate("ASR", node, ast.AugAssign(copy.deepcopy(node.target), new_op(),
                                                              copy.deepcopy(node.value)))
        return node

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        result = node
        for i, op in enumerate(node.ops):
            new_op = _ROR.get(type(op))
            if new_op:
                ops = list(node.ops)
                ops[i] = new_op()
                repl = ast.Compare(copy.deepcopy(node.left), ops, copy.deepcopy(node.comparators))
                out = self._candidate("ROR", node, repl)
                if out is not node:
                    result = out
        return result

    def visit_BoolOp(self, node: ast.BoolOp):
        self.generic_visit(node)
        new_op = _LCR[type(node.op)]
        return self._candidate("LCR", node, ast.BoolOp(new_op(), copy.deepcopy(node.values)))

    def visit_UnaryOp(self, node: ast.UnaryOp):
        self.generic_visit(node)
        if isinstance(node.op, (ast.Not, ast.USub)):
            return self._candidate("UOI", node, copy.deepcopy(node.operand))
        return node

    def visit_Constant(self, node: ast.Constant):
        if id(node) in self.skip:
            return node
        v = node.value
        if isinstance(v, bool):
            return self._candidate("CRP", node, ast.Constant(not v))
        if isinstance(v, (int, float)):
            return self._candidate("CRP", node, ast.Constant(v + 1))
        if isinstance(v, str) and v:
            return self._candidate("CRP", node, ast.Constant(""))
        return node

    def visit_JoinedStr(self, node):  # f-strings: mutating parts produces noise
        return node

    def visit_Return(self, node: ast.Return):
        self.generic_visit(node)
        if node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None):
            return node
        return self._candidate("RET", node, ast.Return(ast.Constant(None)))


def generate_mutants(info: ModuleInfo, limit: int | None = None) -> list[tuple[Mutant, str]]:
    """Return (mutant metadata, mutated source) pairs for a module."""
    tree = ast.parse(info.source)
    collector = _Mutator()
    collector.skip = _docstring_nodes(tree)
    collector.visit(tree)

    results: list[tuple[Mutant, str]] = []
    for idx, (op, line, before, after) in enumerate(collector.found):
        if limit is not None and len(results) >= limit:
            break
        t = ast.parse(info.source)
        applier = _Mutator(target=idx)
        applier.skip = _docstring_nodes(t)
        mutated_tree = ast.fix_missing_locations(applier.visit(t))
        try:
            mutated_src = ast.unparse(mutated_tree)
            compile(mutated_src, "<mutant>", "exec")
        except (SyntaxError, ValueError):
            continue
        m = Mutant(id=len(results), operator=op, lineno=line,
                   original=ast.unparse(before), mutated=ast.unparse(after),
                   function=enclosing_function(info, line))
        results.append((m, mutated_src))
    return results


def run_mutation_analysis(info: ModuleInfo, test_code: str, *, timeout_s: int = 10,
                          workers: int = 4, limit: int | None = None,
                          known_equivalent: set[tuple[int, str, str]] | None = None,
                          extra_paths: list[str] | None = None) -> MutationResult:
    """Run the test suite against every mutant. A mutant is *killed* when at
    least one test fails (or the run times out)."""
    start = time.time()
    pairs = generate_mutants(info, limit)
    known_equivalent = known_equivalent or set()

    def evaluate(pair: tuple[Mutant, str]) -> Mutant:
        mutant, src = pair
        if (mutant.lineno, mutant.original, mutant.mutated) in known_equivalent:
            mutant.status = "equivalent"
            return mutant
        work = Path(tempfile.mkdtemp(prefix="tf_mut_"))
        try:
            (work / f"{info.module_name}.py").write_text(src, encoding="utf-8")
            (work / "test_generated.py").write_text(test_code, encoding="utf-8")
            status = run_pytest_quick(work, "test_generated.py", timeout_s, extra_paths)
            mutant.status = {"pass": "survived", "fail": "killed", "timeout": "timeout"}.get(status, "error")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return mutant

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        mutants = list(pool.map(evaluate, pairs))
    return MutationResult(mutants=mutants, duration_s=time.time() - start)


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2)))
