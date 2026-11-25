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
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Config
from ..orchestrator import Orchestrator

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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


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
    RUNS[run_id] = {"events": events, "history": [], "report": None, "status": "running"}

    def emit(kind: str, data: dict) -> None:
        item = {"kind": kind, "data": data}
        RUNS[run_id]["history"].append(item)
        events.put(item)

    def worker() -> None:
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
            _running.release()
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id}


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
    return FileResponse(STATIC / "automation.html")


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
    return f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


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
    RUNS[run_id] = {"events": events, "history": [], "report": None, "status": "running"}

    def emit(kind: str, data: dict) -> None:
        item = {"kind": kind, "data": data}
        RUNS[run_id]["history"].append(item)
        events.put(item)

    def worker() -> None:
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
    return {
        "mock": {"ready": True, "note": "offline, no AI"},
        "groq": {"ready": bool(os.environ.get("GROQ_API_KEY")),
                 "note": "key loaded" if os.environ.get("GROQ_API_KEY") else "no GROQ_API_KEY (groq_key.txt)"},
        "ollama": {"ready": ollama_up, "models": ollama_models,
                   "note": (f"{len(ollama_models)} model(s)" if ollama_up else "Ollama not running")},
        "claude": {"ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
                   "note": "key loaded" if os.environ.get("ANTHROPIC_API_KEY") else "no ANTHROPIC_API_KEY"},
    }
