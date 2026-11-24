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
    "bytes": [b"", b"a", b"hello world", b"\x00\x01\xff", b"abc123"],
}
_MAGNITUDES = [999, 1000, 1023, 1024, 10**6, 2**20, 10**9, 2**30, 10**12, 2**40, 10**15, 2**50]
_TYPE_ALIASES = {"bytearray": "bytes", "sequence": "list", "iterable": "list", "tuple": "list",
                 "set": "list", "frozenset": "list", "mapping": "dict", "text": "str"}
_NAME_HINTS = [
    (("text", "word", "name", "string", "s", "sentence", "char", "prefix", "suffix", "sep",
      "owner", "title", "label", "key", "username", "email"), "str"),
    (("items", "nums", "numbers", "values", "lst", "xs", "data", "seq", "scores"), "list"),
    (("flag", "strict", "enabled", "verbose", "reverse"), "bool"),
    (("rate", "price", "amount", "ratio", "percent", "balance", "weight", "height"), "float"),
]


def _annotation_kind(annotation: str) -> str | None:
    """Kind of the first non-None option of an annotation such as
    'Optional[int]', 'Union[str, bytes]', 'int | None' or 'typing.List[int]'."""
    import re
    a = annotation.replace("typing.", "").replace("'", "").replace('"', "").strip()
    m = re.fullmatch(r"(?:Optional|Union)\[(.*)\]", a)
    options = [o.strip() for o in re.split(r"[,|]", m.group(1) if m else a)] if (m or "|" in a) else [a]
    for opt in options:
        base = opt.split("[")[0].strip().lower()
        if base in ("none", ""):
            continue
        base = _TYPE_ALIASES.get(base, base)
        if base in _VALUES:
            return base
    return None


def _default_kind(default: str | None) -> str | None:
    """Kind of a parameter's default value, if it is a plain literal."""
    import ast as _ast
    if default is None:
        return None
    try:
        value = _ast.literal_eval(default)
    except Exception:
        return None
    for kind, t in (("bool", bool), ("int", int), ("float", float), ("str", str),
                    ("bytes", bytes), ("list", (list, tuple)), ("dict", dict)):
        if isinstance(value, t):
            return kind
    return None


def _kind(annotation: str | None, name: str, default: str | None = None) -> str:
    if annotation:
        kind = _annotation_kind(annotation)
        if kind:
            return kind
    kind = _default_kind(default)
    if kind:
        return kind
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
            survivors = context.get("survivors", [])
            targets = {m.function for m in survivors}
            # mutants outside any function (module-level tables/constants) can be
            # reached through every function, so then no function is excluded
            only = None if (None in targets or not targets) else targets
            code = self._characterize(info, context, per_fn=8, offset=self._round * 3,
                                      only=only, boundaries=True)
            return RefinedTests(code=code, equivalent_mutant_ids=[], notes="mock refinement")
        raise LLMError(f"mock cannot produce {schema.__name__}")

    # -- characterization -------------------------------------------------
    def _arg_sets(self, fn, n, offset, boundaries):
        """Argument lists (as source text). Required parameters are passed
        positionally; optional ones keep their defaults, except in refinement
        rounds, where those with a literal default are varied by keyword."""
        params = [p for p in fn.params if not p.name.startswith("*")]
        chosen, pools = [], []
        for p in params:
            if p.default is not None and not (boundaries and _default_kind(p.default)):
                continue
            kind = _kind(p.annotation, p.name, p.default)
            pool = list(_VALUES[kind])
            if boundaries and kind in ("int", "float"):
                extra = []
                for c in fn.constants:
                    if isinstance(c, (int, float)):
                        extra += [c - 1, c, c + 1]
                # boundary values first, then magnitudes that reach thresholds
                # far above the small default inputs, then the defaults
                pool = extra + _MAGNITUDES + pool
            chosen.append(p)
            pools.append(pool)
        if not chosen:
            return [[]]
        combos = []
        for k in range(n):
            args = []
            for i, (p, pool) in enumerate(zip(chosen, pools)):
                value = repr(pool[(k + offset + 2 * i) % len(pool)])
                args.append(value if p.default is None else f"{p.name}={value}")
            combos.append(args)
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
            if res.get("timeout"):
                continue  # the call hangs on this input: no test can be written for it
            if res["ok"] and res["value"] is not None:
                value = res["value"]
                if isinstance(eval(value, {}), float):
                    value = f"pytest.approx({value})"
                lines += [f"def {tname}():", f"    assert {call} == {value}", ""]
            elif not res["ok"]:
                lines += [f"def {tname}():", f"    with pytest.raises({_exc_ref(res['exc'])}):",
                          f"        {call}", ""]
        return "\n".join(lines)

    CALL_TIMEOUT_S = 3.0

    def _probe(self, info, calls):
        """Run the calls in a subprocess. A call that hangs (e.g. an infinite
        loop on some input) is recorded as {"timeout": True} and the probe is
        restarted with the calls after it."""
        import os
        import shutil
        work = Path(tempfile.mkdtemp(prefix="tf_probe_"))
        (work / f"{info.module_name}.py").write_text(info.source)
        probe = Path(__file__).parent / "core" / "probe.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(work)] + self.extra_paths)
        results: list[dict] = []
        try:
            while len(results) < len(calls):
                remaining = calls[len(results):]
                payload = json.dumps({"module": info.module_name, "calls": remaining,
                                      "call_timeout_s": self.CALL_TIMEOUT_S})
                try:
                    proc = subprocess.run([sys.executable, str(probe)], input=payload, cwd=work,
                                          env=env, capture_output=True, text=True,
                                          timeout=30 + self.CALL_TIMEOUT_S * len(remaining))
                    lines = proc.stdout.splitlines()
                except subprocess.TimeoutExpired as e:
                    out = e.stdout or ""
                    lines = (out.decode(errors="replace") if isinstance(out, bytes) else out).splitlines()
                    lines.append('{"timeout": true}')
                got = []
                for line in lines:
                    try:
                        got.append(json.loads(line))
                    except ValueError:
                        continue  # stray output printed by the module under test
                    if got[-1].get("timeout"):
                        break
                if not got:  # the module itself failed to import or crashed
                    got = [{"ok": False, "value": None, "exc": "Exception"}]
                results.extend(got[: len(remaining)])
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return results


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


# ==========================================================================
# OpenAI-compatible services (Groq, OpenRouter, ...)
# ==========================================================================
PROVIDERS = {
    # name: (base URL, API-key environment variable, default model)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-oss-120b:free"),
}


class OpenAICompatClient(LLM):
    """Any service with an OpenAI-style /chat/completions endpoint.

    Asks for a JSON-schema constrained reply (``response_format``); if the
    service or model rejects that, falls back to plain JSON mode with the
    schema written into the prompt. Free tiers have tight per-minute limits,
    so HTTP 429 replies are waited out using the service's retry-after hint.
    """

    def __init__(self, provider: str = "groq", model: str | None = None, effort: str = "medium",
                 base_url: str | None = None, api_key: str | None = None,
                 timeout_s: int = 300, max_wait_s: int = 900) -> None:
        super().__init__()
        import os
        default_url, key_env, default_model = PROVIDERS.get(provider, (None, "OPENAI_API_KEY", None))
        self.provider = provider
        self.base_url = (base_url or default_url or "").rstrip("/")
        if not self.base_url:
            raise LLMError(f"unknown provider '{provider}' and no base URL given")
        self.api_key = api_key or os.environ.get(key_env)
        if not self.api_key:
            raise LLMError(f"Set the {key_env} environment variable to your {provider} API key")
        self.model = model or default_model
        # gpt-oss style reasoning models accept low | medium | high
        self.reasoning_effort = {"xhigh": "high", "max": "high"}.get(effort, effort)
        self.timeout_s = timeout_s
        self.max_wait_s = max_wait_s
        self.schema_mode = True  # switched off if the service rejects json_schema

    def _post(self, body: dict) -> dict:
        import time
        import urllib.error
        import urllib.request
        waited = 0.0
        while True:
            req = urllib.request.Request(
                self.base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                         "User-Agent": "testforge/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:600]
                if e.code == 429 and _is_daily_limit(detail):
                    raise DailyQuotaExceeded(
                        f"{self.provider}: daily free-tier quota used up; try again after it resets. "
                        f"{detail}") from e
                if e.code == 429:
                    delay = _retry_after(e.headers, detail)
                    if waited + delay > self.max_wait_s:
                        raise LLMError(f"{self.provider} rate limit: waited {waited:.0f}s, still limited. "
                                       f"Free-tier daily quota may be used up. {detail}") from e
                    time.sleep(delay)
                    waited += delay
                    continue
                if e.code in (401, 403):
                    raise LLMError(f"{self.provider} rejected the API key ({e.code}): {detail}") from e
                if e.code == 413:
                    raise LLMError(f"{self.provider}: request too large for this model/tier: {detail}") from e
                raise _HTTPStatus(e.code, detail) from e
            except urllib.error.URLError as e:
                raise LLMError(f"Cannot reach {self.base_url}: {e.reason}") from e

    def complete(self, role, system, prompt, schema, context=None):
        from pydantic import ValidationError
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        last_error = ""
        use_schema = self.schema_mode
        json_failures = 0
        for attempt in range(4):
            body = {"model": self.model, "messages": messages, "temperature": 0,
                    # room for long test files after the model's reasoning
                    "max_completion_tokens": 32768}
            if self.reasoning_effort in ("low", "medium", "high"):
                body["reasoning_effort"] = self.reasoning_effort
            if use_schema:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}
            else:
                body["response_format"] = {"type": "json_object"}
                body["messages"] = messages[:1] + [{"role": "user", "content": prompt +
                    "\n\nReply with only a JSON object matching this JSON schema:\n" +
                    json.dumps(schema.model_json_schema())}] + messages[2:]
            try:
                data = self._post(body)
            except _HTTPStatus as e:
                if e.code == 400 and "json_validate_failed" in e.detail:
                    # the service checked the reply against the schema and it failed:
                    # retry once, then ask for plain JSON for this call
                    json_failures += 1
                    last_error = "service-side JSON validation failed"
                    if json_failures >= 2:
                        use_schema = False
                    continue
                if e.code == 400 and use_schema and ("response_format" in e.detail
                                                     or "json_schema" in e.detail):
                    self.schema_mode = use_schema = False  # service can't do schemas
                    continue
                if e.code == 400 and "reasoning_effort" in e.detail:
                    self.reasoning_effort = None
                    continue
                raise LLMError(f"{self.provider} error {e.code}: {e.detail}") from e
            usage = data.get("usage") or {}
            self.usage.add(role, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            choice = (data.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise LLMError(f"{role}: reply truncated by {self.provider} (output limit)")
            text = (choice.get("message") or {}).get("content") or ""
            try:
                return schema.model_validate_json(_strip_json(text))
            except ValidationError as e:
                last_error = str(e)[:800]
                # Re-ask with only the error, not the invalid reply itself: echoing a long
                # reply back can push the request over a service's size limit.
                messages = messages[:2] + [{"role": "user", "content":
                    "Your previous reply did not match the required JSON schema:\n"
                    f"{last_error}\nReply again, with valid JSON only."}]
        raise LLMError(f"{role}: {self.provider} reply did not match the schema: {last_error}")


class DailyQuotaExceeded(LLMError):
    """The service's per-day limit is used up: waiting minutes will not help."""


def _is_daily_limit(detail: str) -> bool:
    d = detail.lower()
    return "per day" in d or "(tpd)" in d or "(rpd)" in d


class _HTTPStatus(Exception):
    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _retry_after(headers, detail: str) -> float:
    """Seconds to wait after a 429, from the Retry-After header or the message
    ('Please try again in 7.66s' / '1m2.5s'), defaulting to 20s."""
    import re
    value = headers.get("retry-after") if headers else None
    try:
        if value:
            return min(max(float(value), 1.0), 300.0)
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", detail)
    if m:
        return min(int(m.group(1) or 0) * 60 + float(m.group(2)) + 1.0, 300.0)
    return 20.0


def _strip_json(text: str) -> str:
    """Remove markdown fences some models wrap around JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def make_llm(kind: str, model: str | None = None, effort: str = "high",
             extra_paths: list[str] | None = None, **options) -> LLM:
    from .config import DEFAULT_MODELS
    model = model or DEFAULT_MODELS.get(kind)
    if kind == "claude":
        return ClaudeClient(model=model, effort=effort)
    if kind in PROVIDERS:
        return OpenAICompatClient(provider=kind, model=model, effort=effort)
    if kind == "ollama":
        return OllamaClient(model=model, host=options.get("ollama_host"),
                            num_ctx=options.get("num_ctx", 8192))
    if kind == "mock":
        return MockLLM(extra_paths=extra_paths)
    raise ValueError(f"unknown llm backend: {kind}")
