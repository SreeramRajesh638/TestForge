"""Static analysis of the module under test (used by the Analyzer agent)."""

from __future__ import annotations

import ast
from pathlib import Path

from ..models import ClassInfo, FunctionInfo, ModuleInfo, ParamInfo

_BRANCH_NODES = (ast.If, ast.For, ast.While, ast.Try, ast.IfExp, ast.BoolOp,
                 ast.ExceptHandler, ast.comprehension, ast.Match, ast.AsyncFor)


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef, skip_self: bool) -> list[ParamInfo]:
    args = fn.args
    positional = args.posonlyargs + args.args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    out = []
    for a, d in zip(positional, defaults):
        out.append(ParamInfo(a.arg, ast.unparse(a.annotation) if a.annotation else None,
                             ast.unparse(d) if d is not None else None))
    if args.vararg:
        out.append(ParamInfo("*" + args.vararg.arg))
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        out.append(ParamInfo(a.arg, ast.unparse(a.annotation) if a.annotation else None,
                             ast.unparse(d) if d is not None else None))
    if args.kwarg:
        out.append(ParamInfo("**" + args.kwarg.arg))
    if skip_self and out and out[0].name in ("self", "cls"):
        out = out[1:]
    return out


def _function_info(fn, source: str, class_name: str | None) -> FunctionInfo:
    raises, constants, branches = [], [], 0
    for node in ast.walk(fn):
        if isinstance(node, _BRANCH_NODES):
            branches += 1
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = ast.unparse(exc)
            if name not in raises:
                raises.append(name)
        if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool) and node.value not in constants):
            constants.append(node.value)
    is_static = any(ast.unparse(d) in ("staticmethod",) for d in fn.decorator_list)
    return FunctionInfo(
        name=fn.name,
        qualname=f"{class_name}.{fn.name}" if class_name else fn.name,
        class_name=class_name,
        params=_params(fn, skip_self=class_name is not None and not is_static),
        returns=ast.unparse(fn.returns) if fn.returns else None,
        docstring=ast.get_docstring(fn),
        source=ast.get_source_segment(source, fn) or "",
        lineno=fn.lineno,
        end_lineno=fn.end_lineno or fn.lineno,
        raises=raises,
        branches=branches,
        constants=constants[:12],
    )


def analyze_source(source: str, module_name: str, path: str = "<memory>") -> ModuleInfo:
    tree = ast.parse(source)
    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []
    imports: list[str] = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(_function_info(node, source, None))
        elif isinstance(node, ast.ClassDef):
            methods, init_params = [], []
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name == "__init__":
                    init_params = _params(item, skip_self=True)
                # public methods + dunders worth testing; private helpers are
                # exercised through the public API.
                if not item.name.startswith("_") or item.name in ("__init__", "__eq__", "__len__",
                                                                  "__contains__", "__str__", "__repr__"):
                    methods.append(item.name)
                    functions.append(_function_info(item, source, node.name))
            classes.append(ClassInfo(node.name, init_params, methods, ast.get_docstring(node)))

    return ModuleInfo(module_name, path, source, functions, classes, imports)


def analyze_file(path: str | Path) -> ModuleInfo:
    p = Path(path)
    return analyze_source(p.read_text(), p.stem, str(p))


def enclosing_function(info: ModuleInfo, lineno: int) -> str | None:
    """Qualified name of the function containing ``lineno`` (innermost match)."""
    best = None
    for f in info.functions:
        if f.lineno <= lineno <= f.end_lineno:
            if best is None or f.lineno >= best.lineno:
                best = f
    return best.qualname if best else None
