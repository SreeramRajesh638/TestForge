"""LLM backends.

* ClaudeClient - Anthropic Messages API with structured outputs, so every
  agent reply is validated against a pydantic schema.
* MockLLM      - offline backend (no API key). Builds characterization tests
  by actually calling the code in a sandboxed subprocess. It is also used as
  a non-LLM baseline in the experiments.

Agents talk to either through ``LLM.complete(role, system, prompt, schema, context)``.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import GeneratedTests, ModuleInfo, RefinedTests, TestPlan, PlannedCase

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    by_role: dict = field(default_factory=dict)

    def add(self, role: str, inp: int, out: int) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.calls += 1
        r = self.by_role.setdefault(role, {"input": 0, "output": 0, "calls": 0})
        r["input"] += inp
        r["output"] += out
        r["calls"] += 1


class LLM:
    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, role: str, system: str, prompt: str, schema: type[T],
                 context: dict | None = None) -> T:
        raise NotImplementedError


# ==========================================================================
# Claude (Anthropic API)
# ==========================================================================
class ClaudeClient(LLM):
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high",
                 max_tokens: int = 32000) -> None:
        super().__init__()
        import anthropic  # imported lazily so the mock works without it

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def complete(self, role, system, prompt, schema, context=None):
        a = self._anthropic
        try:
            # Streaming avoids HTTP timeouts on long test files.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema.model_json_schema()},
                },
                # If a safety classifier declines, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except a.RateLimitError as e:
            raise LLMError(f"Rate limited by the API: {e.message}") from e
        except a.AuthenticationError as e:
            raise LLMError("Invalid or missing ANTHROPIC_API_KEY") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Network error: {e}") from e

        self.usage.add(role, response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise LLMError(f"{role}: model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{role}: reply truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError(f"{role}: no text block in reply")
        return schema.model_validate_json(text)


# ==========================================================================
# Offline mock backend
# ==========================================================================
_VALUES = {
    "int": [0, 1, -1, 2, 10, -7, 100, 3],
    "float": [0.0, 1.5, -2.5, 10.0, 0.1, 100.0],
    "str": ["", "a", "hello world", "Racecar", "abc123", "  padded  "],
    "list": [[], [1], [3, 1, 2], [5, 5, -1], [0, 0, 0, 1]],
    "bool": [True, False],
    "dict": [{}, {"a": 1}, {"x": 2, "y": -3}],
}
_NAME_HINTS = [
    (("text", "word", "name", "string", "s", "sentence", "char", "prefix", "suffix", "sep",
      "owner", "title", "label", "key", "username", "email"), "str"),
    (("items", "nums", "numbers", "values", "lst", "xs", "data", "seq", "scores"), "list"),
    (("flag", "strict", "enabled", "verbose", "reverse"), "bool"),
    (("rate", "price", "amount", "ratio", "percent", "balance", "weight", "height"), "float"),
]


def _kind(annotation: str | None, name: str) -> str:
    if annotation:
        a = annotation.lower()
        for k in ("bool", "int", "float", "str", "list", "dict"):
            if a.startswith(k) or a.startswith(f"typing.{k}") or k in a.split("[")[0]:
                return k
    for names, kind in _NAME_HINTS:
        if name.lower() in names or any(name.lower().endswith("_" + n) for n in names):
            return kind
    return "int"


class MockLLM(LLM):
    """Deterministic, offline stand-in for an LLM."""

    def __init__(self, extra_paths: list[str] | None = None) -> None:
        super().__init__()
        self.extra_paths = extra_paths or []
        self._round = 0

    def complete(self, role, system, prompt, schema, context=None):
        context = context or {}
        info: ModuleInfo = context["module"]
        self.usage.add(role, 0, 0)
        if schema is TestPlan:
            return TestPlan(cases=[PlannedCase(target=f.qualname, category="normal",
                                               description=f"characterize {f.qualname}",
                                               inputs="generated", expected="observed")
                                   for f in info.functions])
        if schema is GeneratedTests and role == "fixer":
            # The mock cannot reason about failures; return unchanged so the
            # orchestrator prunes the failing tests.
            return GeneratedTests(code=context["test_code"], notes="mock: no repair")
        if schema is GeneratedTests:
            return GeneratedTests(code=self._characterize(info, context, per_fn=4, offset=0),
                                  notes="mock characterization tests")
        if schema is RefinedTests:
            self._round += 1
            targets = {m.function for m in context.get("survivors", []) if m.function}
            code = self._characterize(info, context, per_fn=8, offset=self._round * 3,
                                      only=targets, boundaries=True)
            return RefinedTests(code=code, equivalent_mutant_ids=[], notes="mock refinement")
        raise LLMError(f"mock cannot produce {schema.__name__}")

    # -- characterization -------------------------------------------------
    def _arg_sets(self, fn, n, offset, boundaries):
        params = [p for p in fn.params if not p.name.startswith("*")]
        pools = []
        for i, p in enumerate(params):
            kind = _kind(p.annotation, p.name)
            pool = list(_VALUES[kind])
            if boundaries and kind in ("int", "float"):
                for c in fn.constants:
                    pool += [c - 1, c, c + 1]
            pools.append(pool)
        if not params:
            return [[]]
        combos = []
        for k in range(n):
            combos.append([repr(pool[(k + offset + 2 * i) % len(pool)]) for i, pool in enumerate(pools)])
        # dedupe, keep order
        seen, out = set(), []
        for c in combos:
            key = tuple(c)
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def _characterize(self, info, context, per_fn, offset, only=None, boundaries=False):
        classes = {c.name: c for c in info.classes}
        calls, meta = [], []
        for fn in info.functions:
            if only is not None and fn.qualname not in only:
                continue
            if fn.name.startswith("__"):
                continue
            init: list[str] = []
            if fn.class_name:
                cls = classes.get(fn.class_name)
                if cls is None:
                    continue
                # construct with a non-trivial value for each required parameter
                init = [repr(_VALUES[_kind(p.annotation, p.name)][1]) for p in cls.init_params
                        if p.default is None and not p.name.startswith("*")]
            for args in self._arg_sets(fn, per_fn, offset, boundaries):
                calls.append({"qualname": fn.qualname, "args": args, "init": init})
                meta.append((fn, args, init))
        if not calls:
            return f"import {info.module_name}\n"
        results = self._probe(info, calls)

        lines = ["import pytest", f"import {info.module_name} as m", ""]
        counter = itertools.count(1)
        for (fn, args, init), res in zip(meta, results):
            owner = f"m.{fn.class_name}({', '.join(init)})." if fn.class_name else "m."
            call = f"{owner}{fn.name}({', '.join(args)})"
            n = next(counter)
            tname = f"test_{fn.qualname.replace('.', '_').lower()}_{offset}_{n}"
            if res["ok"] and res["value"] is not None:
                value = res["value"]
                if isinstance(eval(value, {}), float):
                    value = f"pytest.approx({value})"
                lines += [f"def {tname}():", f"    assert {call} == {value}", ""]
            elif not res["ok"]:
                lines += [f"def {tname}():", f"    with pytest.raises({_exc_ref(res['exc'])}):",
                          f"        {call}", ""]
        return "\n".join(lines)

    def _probe(self, info, calls):
        work = Path(tempfile.mkdtemp(prefix="tf_probe_"))
        (work / f"{info.module_name}.py").write_text(info.source)
        probe = Path(__file__).parent / "core" / "probe.py"
        payload = json.dumps({"module": info.module_name, "calls": calls})
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(work)] + self.extra_paths)
        try:
            proc = subprocess.run([sys.executable, str(probe)], input=payload, cwd=work, env=env,
                                  capture_output=True, text=True, timeout=60)
            return json.loads(proc.stdout)
        except Exception:
            return [{"ok": False, "value": None, "exc": "Exception"} for _ in calls]


def _exc_ref(name: str) -> str:
    import builtins
    return name if hasattr(builtins, name) else f"m.{name}"


# ==========================================================================
# Ollama (free local models)
# ==========================================================================
class OllamaClient(LLM):
    """Local open-weight models served by Ollama (https://ollama.com).

    Uses Ollama's native /api/chat endpoint with ``format`` set to the JSON
    schema, so even small models are constrained to valid structured output.
    Replies that still fail validation are retried once with the error shown
    to the model.
    """

    def __init__(self, model: str = "qwen2.5-coder:3b", host: str | None = None,
                 num_ctx: int = 8192, timeout_s: int = 900, retries: int = 1) -> None:
        super().__init__()
        import os
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.num_ctx = num_ctx
        self.timeout_s = timeout_s
        self.retries = retries

    def _post(self, path: str, body: dict) -> dict:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.host + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 404 and "not found" in detail:
                raise LLMError(f"Ollama model '{self.model}' is not downloaded. "
                               f"Run: ollama pull {self.model}") from e
            raise LLMError(f"Ollama error {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"Cannot reach Ollama at {self.host}. Is Ollama running? ({e.reason})") from e
        except TimeoutError as e:
            raise LLMError(f"Ollama did not answer within {self.timeout_s}s") from e

    def complete(self, role, system, prompt, schema, context=None):
        from pydantic import ValidationError
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": prompt}]
        last_error = ""
        for _ in range(self.retries + 1):
            data = self._post("/api/chat", {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
                # temperature 0 keeps runs as repeatable as a local model allows
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
            })
            self.usage.add(role, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
            text = (data.get("message") or {}).get("content", "")
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                last_error = str(e)[:800]
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "Your reply did not match the required JSON "
                                                         f"schema:\n{last_error}\nReply again with valid JSON."}]
        raise LLMError(f"{role}: Ollama reply did not match the schema: {last_error}")


def make_llm(kind: str, model: str | None = None, effort: str = "high",
             extra_paths: list[str] | None = None, **options) -> LLM:
    from .config import DEFAULT_MODELS
    model = model or DEFAULT_MODELS.get(kind)
    if kind == "claude":
        return ClaudeClient(model=model, effort=effort)
    if kind == "ollama":
        return OllamaClient(model=model, host=options.get("ollama_host"),
                            num_ctx=options.get("num_ctx", 8192))
    if kind == "mock":
        return MockLLM(extra_paths=extra_paths)
    raise ValueError(f"unknown llm backend: {kind}")
