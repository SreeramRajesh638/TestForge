"""FastAPI backend for the TestForge dashboard.

Runs execute in a background thread; their events are pushed to the browser
over Server-Sent Events so the user can watch the agents work live.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Config
from ..orchestrator import Orchestrator

STATIC = Path(__file__).parent / "static"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

app = FastAPI(title="TestForge")
RUNS: dict[str, dict] = {}


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/examples")
def examples():
    if not EXAMPLES.exists():
        return []
    return [{"name": p.stem, "source": p.read_text()} for p in sorted(EXAMPLES.glob("*.py"))]


@app.post("/api/runs")
def start_run(req: RunRequest):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", req.module_name):
        raise HTTPException(400, "module_name must be a valid Python identifier")
    if req.llm not in ("mock", "ollama", "groq", "openrouter", "claude"):
        raise HTTPException(400, "llm must be one of mock, ollama, groq, openrouter, claude")
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
        path.write_text(req.source)
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
