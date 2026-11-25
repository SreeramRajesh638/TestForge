"""FastAPI backend for the TestForge dashboard.

Runs execute in a background thread; their events are pushed to the browser
over Server-Sent Events so the user can watch the agents work live.

When deployed on the internet, set TESTFORGE_PASSWORD: the dashboard then
asks for it (HTTP Basic auth, any user name). This matters because TestForge
executes the Python code that is submitted to it.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import queue
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import telemetry
from ..config import Config
from ..orchestrator import Orchestrator
from . import experiments as experiments_store
from . import history

STATIC = Path(__file__).parent / "static"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

app = FastAPI(title="TestForge")
RUNS: dict[str, dict] = {}
MAX_CONCURRENT_RUNS = int(os.environ.get("TESTFORGE_MAX_RUNS", "2"))
_running = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """Require TESTFORGE_PASSWORD (if set) for everything except /healthz."""
    password = os.environ.get("TESTFORGE_PASSWORD")
    if not password or request.url.path in ("/healthz", "/demo"):
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            _, _, given = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            given = ""
        if hmac.compare_digest(given.encode(), password.encode()):
            return await call_next(request)
    return Response("Password required", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="TestForge"'})


_UNTIMED = ("/api/telemetry/stream", "/healthz")


@app.middleware("http")
async def record_requests(request: Request, call_next):
    """Record real API requests (method, path, status, latency) for the System Monitor."""
    path = request.url.path
    timed = path.startswith("/api/") and path not in _UNTIMED and not path.endswith("/events")
    start = time.perf_counter()
    response = await call_next(request)
    if timed:
        telemetry.emit("http", "browser", "server", duration_ms=(time.perf_counter() - start) * 1000,
                       ok=response.status_code < 400, detail=f"{request.method} {path} → {response.status_code}",
                       method=request.method, path=path, status=response.status_code)
    return response


@app.get("/demo")
def demo_app():
    """ShopLite: the sample app the UI automation engine tests."""
    return FileResponse(STATIC / "shoplite.html")


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


class RunRequest(BaseModel):
    source: str = Field(min_length=1, max_length=200_000)
    module_name: str = "module_under_test"
    llm: str = "mock"
    model: str | None = None
    effort: str = "high"
    max_refine_iterations: int = Field(3, ge=0, le=10)
    target_mutation_score: float = Field(0.9, ge=0, le=1)
    use_planner: bool = True
    use_fixer: bool = True
    use_mutation_feedback: bool = True


def normalize_module_name(name: str) -> str:
    """Accept what people naturally type ('shipping.py', 'my-code') and turn it
    into an importable module name; explain clearly when that is impossible."""
    import keyword
    n = name.strip()
    if n.lower().endswith(".py"):
        n = n[:-3]
    n = re.sub(r"[\s\-.]+", "_", n)
    if not n:
        raise HTTPException(400, "Enter a module name, for example: shipping")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n) or keyword.iskeyword(n):
        raise HTTPException(400, f"'{name}' can't be a Python module name. Use letters, digits and "
                                 "underscores, starting with a letter, for example: shipping")
    if n.lower().startswith("test"):
        raise HTTPException(400, f"'{name}' starts with 'test', which pytest treats as a test file. "
                                 "Use a different name, for example: shipping")
    return n


SPA = STATIC / "app"           # React build output (frontend/ → npm run build)
SPA_ROUTES = ("/", "/automation", "/monitor", "/experiments", "/runs")


NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _spa_or(classic: str):
    index = SPA / "index.html"
    return FileResponse(index if index.exists() else STATIC / classic, headers=NO_CACHE)


@app.get("/")
def index():
    return _spa_or("index.html")


@app.get("/monitor")
@app.get("/experiments")
@app.get("/runs")
def spa_pages():
    return _spa_or("index.html")


@app.get("/app/assets/{name}")
def spa_asset(name: str):
    path = (SPA / "assets" / name).resolve()
    if path.parent != (SPA / "assets").resolve() or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path)


@app.get("/classic")
def classic_index():
    """The previous plain-HTML dashboard (kept as a fallback)."""
    return FileResponse(STATIC / "index.html")


@app.get("/classic/automation")
def classic_automation():
    return FileResponse(STATIC / "automation.html")


@app.get("/api/examples")
def examples():
    if not EXAMPLES.exists():
        return []
    return [{"name": p.stem, "source": p.read_text(encoding="utf-8")} for p in sorted(EXAMPLES.glob("*.py"))]


@app.post("/api/runs")
def start_run(req: RunRequest):
    req.module_name = normalize_module_name(req.module_name)
    if req.llm not in ("mock", "ollama", "groq", "openrouter", "claude"):
        raise HTTPException(400, "llm must be one of mock, ollama, groq, openrouter, claude")
    if not _running.acquire(blocking=False):
        raise HTTPException(429, f"{MAX_CONCURRENT_RUNS} runs are already in progress; try again shortly")
    run_id = uuid.uuid4().hex[:10]
    events: queue.Queue = queue.Queue()
    started = time.time()
    RUNS[run_id] = {"events": events, "history": [], "report": None, "status": "running"}

    def emit(kind: str, data: dict) -> None:
        item = {"kind": kind, "data": data, "t": round(time.time() - started, 3)}
        RUNS[run_id]["history"].append(item)
        events.put(item)

    def worker() -> None:
        telemetry.set_run(run_id)
        telemetry.emit("run", "server", "orchestrator", detail=f"generate · {req.module_name} · {req.llm}",
                       phase="start", run_kind="generate")
        work = Path(tempfile.mkdtemp(prefix="tf_web_"))
        path = work / f"{req.module_name}.py"
        path.write_text(req.source, encoding="utf-8")
        cfg = Config(llm=req.llm, model=(req.model or None), effort=req.effort,
                     max_refine_iterations=req.max_refine_iterations,
                     target_mutation_score=req.target_mutation_score,
                     use_planner=req.use_planner, use_fixer=req.use_fixer,
                     use_mutation_feedback=req.use_mutation_feedback)
        try:
            report = Orchestrator(cfg, on_event=emit).run(str(path))
            RUNS[run_id]["report"] = report.to_dict()
            RUNS[run_id]["status"] = "finished"
            emit("report", RUNS[run_id]["report"])
        except Exception as e:  # surface any failure to the UI
            RUNS[run_id]["status"] = "failed"
            emit("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            status = RUNS[run_id]["status"]
            telemetry.emit("run", "orchestrator", "server", duration_ms=(time.time() - started) * 1000,
                           ok=status == "finished", detail=f"generate finished · {status}",
                           phase="end", run_kind="generate")
            _save_history(run_id, "generate", started, req.model_dump(), RUNS[run_id]["report"],
                          status, history.summarize_generate)
            telemetry.set_run(None)
            _running.release()
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id}


def _save_history(run_id, kind, started, request_dict, report, status, summarize) -> None:
    try:
        history.save({"id": run_id, "kind": kind, "started": started, "finished": time.time(),
                      "request": request_dict, "report": report, "status": status,
                      "summary": summarize(request_dict, report, status),
                      "events": RUNS[run_id]["history"]})
    except Exception as e:  # history is a convenience; never fail a run because of it
        telemetry.emit("history", "server", "server", ok=False, detail=f"could not save run: {e}")


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")

    async def stream():
        q: queue.Queue = run["events"]
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if item is None:
                yield "event: end\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}")
def run_result(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return {"status": run["status"], "report": run["report"]}


# ======================================================================
# UI automation (Playwright engine against the ShopLite demo app)
# ======================================================================
SHOPLITE_BUGS = {
    "search": "Search is case-sensitive",
    "clear": "Clear button keeps the old results",
    "cart": "Adding the same product again does not increase its quantity",
    "total": "Cart total ignores quantities",
    "remove": "Remove button removes the wrong product",
    "email": "Email without @ is accepted",
    "password": "Password mismatch is not detected",
    "phone": "Phone numbers with 9 digits are accepted",
    "terms": "Form can be submitted without accepting the terms",
}


class UIRunRequest(BaseModel):
    names: list[str] | None = None        # scenarios to run; None = all
    bugs: list[str] = []                  # bugs injected into the demo app
    mode: str = "run"                     # "run" | "bughunt"
    timeout_ms: int = Field(3000, ge=500, le=30000)


@app.get("/automation")
def automation_page():
    return _spa_or("automation.html")


@app.get("/api/ui/scenarios")
def ui_scenarios():
    from ..uiauto.engine import describe, load_scenarios
    return [{"name": s["name"], "tags": s.get("tags", []),
             "steps": [describe(st) for st in s["steps"]]} for s in load_scenarios()]


@app.get("/api/ui/bugs")
def ui_bugs():
    return [{"id": k, "description": v} for k, v in SHOPLITE_BUGS.items()]


def _internal_base_url() -> str:
    """Where the engine's browser reaches this server. Always this server
    itself (never a user-supplied URL), so the page cannot be used to make
    the server browse arbitrary sites."""
    return f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"


@app.post("/api/ui/runs")
def start_ui_run(req: UIRunRequest):
    from ..uiauto.engine import bug_detection_matrix, filter_scenarios, load_scenarios, run_scenarios
    if req.mode not in ("run", "bughunt"):
        raise HTTPException(400, "mode must be 'run' or 'bughunt'")
    unknown = [b for b in req.bugs if b not in SHOPLITE_BUGS]
    if unknown:
        raise HTTPException(400, f"unknown bugs: {unknown}")
    scenarios = filter_scenarios(load_scenarios(), names=req.names)
    if not scenarios:
        raise HTTPException(400, "no scenarios selected")
    if not _running.acquire(blocking=False):
        raise HTTPException(429, f"{MAX_CONCURRENT_RUNS} runs are already in progress; try again shortly")
    run_id = uuid.uuid4().hex[:10]
    events: queue.Queue = queue.Queue()
    started = time.time()
    RUNS[run_id] = {"events": events, "history": [], "report": None, "status": "running"}

    def emit(kind: str, data: dict) -> None:
        item = {"kind": kind, "data": data, "t": round(time.time() - started, 3)}
        RUNS[run_id]["history"].append(item)
        events.put(item)

    def worker() -> None:
        telemetry.set_run(run_id)
        telemetry.emit("run", "server", "orchestrator", detail=f"ui {req.mode} · {len(scenarios)} scenarios",
                       phase="start", run_kind="ui")
        try:
            base = _internal_base_url()
            if req.mode == "bughunt":
                bugs = req.bugs or list(SHOPLITE_BUGS)
                report = bug_detection_matrix(base, scenarios, bugs, on_event=emit)
            else:
                report = run_scenarios(base, scenarios, bugs=req.bugs, timeout_ms=req.timeout_ms,
                                       on_event=emit).to_dict()
                for r in report["results"]:  # screenshots were already streamed
                    r.pop("screenshot_png_b64", None)
            RUNS[run_id]["report"] = report
            RUNS[run_id]["status"] = "finished"
            emit("report", {"mode": req.mode, **report})
        except Exception as e:
            RUNS[run_id]["status"] = "failed"
            emit("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            status = RUNS[run_id]["status"]
            telemetry.emit("run", "orchestrator", "server", duration_ms=(time.time() - started) * 1000,
                           ok=status == "finished", detail=f"ui {req.mode} finished · {status}",
                           phase="end", run_kind="ui")
            _save_history(run_id, "ui", started, req.model_dump(), RUNS[run_id]["report"], status,
                          history.summarize_ui)
            telemetry.set_run(None)
            _running.release()
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id, "scenarios": len(scenarios)}


# ======================================================================
# Shared static assets and backend status (for the redesigned UI)
# ======================================================================
VENDOR_FILES = {"codemirror.js", "codemirror.css", "codemirror-python.js", "highlight.min.js",
                "LICENSE-codemirror.txt", "LICENSE-highlightjs.txt"}


@app.get("/static/{name}")
def static_asset(name: str):
    if name not in ("app.css", "app.js"):
        raise HTTPException(404, "not found")
    return FileResponse(STATIC / name)


@app.get("/static/vendor/{name}")
def vendor_asset(name: str):
    if name not in VENDOR_FILES:
        raise HTTPException(404, "not found")
    return FileResponse(STATIC / "vendor" / name)


@app.get("/api/status")
def backend_status():
    """Which LLM backends are ready, so the UI can show it before a run."""
    import urllib.request
    ollama_models: list[str] = []
    ollama_up = False
    try:
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        if not host.startswith("http"):
            host = "http://" + host
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=0.4) as r:
            ollama_models = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
            ollama_up = True
    except Exception:
        pass
    from .. import __version__
    return {
        "version": __version__,
        "mock": {"ready": True, "note": "offline, no AI"},
        "groq": {"ready": bool(os.environ.get("GROQ_API_KEY")),
                 "note": "key loaded" if os.environ.get("GROQ_API_KEY") else "no GROQ_API_KEY (groq_key.txt)"},
        "ollama": {"ready": ollama_up, "models": ollama_models,
                   "note": (f"{len(ollama_models)} model(s)" if ollama_up else "Ollama not running")},
        "claude": {"ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
                   "note": "key loaded" if os.environ.get("ANTHROPIC_API_KEY") else "no ANTHROPIC_API_KEY"},
    }



# ======================================================================
# Telemetry, run history, experiments (React dashboard)
# ======================================================================
@app.get("/api/telemetry/snapshot")
def telemetry_snapshot(limit: int = 200):
    snap = telemetry.snapshot(limit=max(1, min(limit, 600)))
    snap["active_runs"] = [{"id": k, "status": v["status"]} for k, v in RUNS.items() if v["status"] == "running"]
    snap["max_runs"] = MAX_CONCURRENT_RUNS
    return snap


@app.get("/api/telemetry/stream")
async def telemetry_stream():
    q = telemetry.subscribe()

    async def stream():
        try:
            yield "event: hello\ndata: {}\n\n"
            idle = 0.0
            while True:
                try:
                    ev = q.get_nowait()
                    idle = 0.0
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    idle += 0.1
                    if idle >= 15:  # keep proxies from closing an idle stream
                        idle = 0.0
                        yield ": keep-alive\n\n"
        finally:
            telemetry.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/history")
def history_list():
    return history.list_runs()


@app.get("/api/history/{run_id}")
def history_get(run_id: str):
    try:
        rec = history.get(run_id)
    except ValueError:
        raise HTTPException(400, "bad run id")
    if rec is None:
        raise HTTPException(404, "unknown run")
    return rec


@app.delete("/api/history/{run_id}")
def history_delete(run_id: str):
    try:
        if not history.delete(run_id):
            raise HTTPException(404, "unknown run")
    except ValueError:
        raise HTTPException(400, "bad run id")
    return {"deleted": run_id}


@app.get("/api/experiments")
def experiments_list():
    return experiments_store.list_experiments()


@app.get("/api/experiments/{name}")
def experiments_get(name: str):
    exp = experiments_store.load_experiment(name)
    if exp is None:
        raise HTTPException(404, "unknown experiment")
    return exp
