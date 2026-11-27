"""REST + Server-Sent Events API of the Blind Spot Intelligence workspace.

    GET  /api/blindspot/overview                         latest metrics (sidebar badge, dashboard card)
    GET  /api/blindspot/sessions                         live and saved sessions
    POST /api/blindspot/sessions                         new session (demo | from_run | custom) [+ analyse]
    GET  /api/blindspot/sessions/{id}                    full state (summary, heatmap, diagnoses, timeline…)
    GET  /api/blindspot/sessions/{id}/events?after=N     live events (SSE); resumes after event N
    GET  /api/blindspot/sessions/{id}/heatmap            heatmap only
    GET  /api/blindspot/sessions/{id}/history            refinement history (suite versions, scores, timeline)
    GET  /api/blindspot/sessions/{id}/mutants/{mid}      original and mutated source of one mutant
    POST /api/blindspot/sessions/{id}/analyze            mutation analysis + diagnosis of all survivors
    POST /api/blindspot/sessions/{id}/investigate        {mutant_id}: diagnose one mutant
    POST /api/blindspot/sessions/{id}/blindspots/{bs}/corrective   generate a corrective test
    POST /api/blindspot/sessions/{id}/blindspots/{bs}/execute      run it on the original program
    POST /api/blindspot/sessions/{id}/blindspots/{bs}/verify       original + mutant + regression check
    POST /api/blindspot/sessions/{id}/blindspots/{bs}/counterexample   {args?}: run inputs on both versions
    POST /api/blindspot/sessions/{id}/resolve_all        every eligible blind spot, then re-analyse
    POST /api/blindspot/sessions/{id}/cancel             stop the running action
    GET  /api/blindspot/sessions/{id}/export?format=json|csv|html
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..blindspot.session import (Busy, Registry, Session, demo_files, export_csv, export_html, save_snapshot)
from . import history

router = APIRouter(prefix="/api/blindspot")
REGISTRY = Registry()
_ID = re.compile(r"[0-9a-f]{6,32}")


def _dir() -> Path:
    d = history.data_dir().parent / "blindspot_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot(sid: str) -> dict | None:
    if not _ID.fullmatch(sid):
        return None
    p = _dir() / f"{sid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _persist(s: Session) -> None:
    try:
        save_snapshot(_dir(), s)
    except OSError:
        pass  # saving is a convenience; the live session keeps working


def _live(sid: str) -> Session:
    s = REGISTRY.get(sid)
    if s is None:
        if _snapshot(sid) is not None:
            raise HTTPException(409, "this session was saved before the server restarted and is read-only; "
                                     "start a new session to run actions")
        raise HTTPException(404, "unknown session")
    return s


def _job(s: Session, name: str, fn) -> dict:
    try:
        s.run_job(name, fn, on_done=_persist)
    except Busy as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True, "action": name, "session": s.id}


# ---------------------------------------------------------------- sessions
class NewSession(BaseModel):
    demo: bool = False
    from_run: str | None = None
    source: str | None = Field(None, max_length=200_000)
    tests: str | None = Field(None, max_length=200_000)
    module_name: str | None = None
    llm: str = "mock"
    model: str | None = None
    analyze: bool = True


@router.post("/sessions")
def create_session(req: NewSession):
    from .app import normalize_module_name  # shared validation
    if req.llm not in ("mock", "ollama", "groq", "openrouter", "claude"):
        raise HTTPException(400, "llm must be one of mock, ollama, groq, openrouter, claude")
    if req.demo:
        source, tests = demo_files()
        name, origin = "eligibility", "demo"
    elif req.from_run:
        rec = history.get(req.from_run) if _ID.fullmatch(req.from_run) else None
        if rec is None or rec.get("kind") != "generate" or not (rec.get("report") or {}).get("final_tests"):
            raise HTTPException(404, "that run has no saved test suite")
        source, tests = rec["request"]["source"], rec["report"]["final_tests"]
        name, origin = rec["request"]["module_name"], "run"
        if req.llm == "mock" and rec["request"].get("llm"):
            req.llm, req.model = rec["request"]["llm"], rec["request"].get("model")
    else:
        if not (req.source and req.tests):
            raise HTTPException(400, "give source and tests, or demo=true, or from_run")
        source, tests, origin = req.source, req.tests, "custom"
        name = normalize_module_name(req.module_name or "module_under_test")
    try:
        s = Session(module_name=name, source=source, tests=tests, llm=req.llm, model=req.model, origin=origin,
                    from_run=req.from_run)
    except SyntaxError as e:
        raise HTTPException(400, f"the module is not valid Python: {e}") from None
    except Exception as e:  # e.g. LLM backend misconfigured
        raise HTTPException(400, f"could not start the session: {e}") from None
    REGISTRY.add(s)
    _persist(s)
    if req.analyze:
        _job(s, "analyze", s.analyze)
    return {"id": s.id, "state": s.state()}


@router.get("/sessions")
def list_sessions():
    rows = {s.id: s.row() for s in REGISTRY.sessions.values()}
    for p in sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        if p.stem in rows:
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sm = st.get("summary", {})
        rows[p.stem] = {"id": p.stem, "created": st.get("created"), "module": st.get("module_name"),
                        "origin": st.get("origin"), "status": st.get("status"), "live": False,
                        "discovered": sm.get("discovered"), "resolved": sm.get("resolved"),
                        "unresolved": sm.get("unresolved"), "score_before": sm.get("score_before"),
                        "score_after": sm.get("score_after")}
    return sorted(rows.values(), key=lambda r: r.get("created") or 0, reverse=True)


@router.get("/overview")
def overview():
    """Most recent Blind Spot metrics: a live session, else the latest saved
    session, else the latest generate run that used Blind Spot Intelligence."""
    live = sorted(REGISTRY.sessions.values(), key=lambda s: s.created, reverse=True)
    if live:
        s = live[0]
        st = s.state()
        return {"source": "session", "id": s.id, "module": s.module_name, "summary": st["summary"],
                "heatmap": st["heatmap"], "status": s.status}
    rows = list_sessions()
    if rows:
        st = _snapshot(rows[0]["id"]) or {}
        return {"source": "session", "id": rows[0]["id"], "module": st.get("module_name"),
                "summary": st.get("summary"), "heatmap": st.get("heatmap"), "status": "saved"}
    for row in history.list_runs(50):
        if row.get("kind") != "generate":
            continue
        rec = history.get(row["id"]) or {}
        bs = (rec.get("report") or {}).get("blind_spot")
        if bs:
            return {"source": "run", "id": row["id"], "module": bs.get("module"), "summary": bs.get("summary"),
                    "heatmap": bs.get("heatmap"), "status": "saved"}
    return {"source": None}


@router.get("/sessions/{sid}")
def get_session(sid: str):
    s = REGISTRY.get(sid)
    if s is not None:
        return s.state()
    snap = _snapshot(sid)
    if snap is None:
        raise HTTPException(404, "unknown session")
    snap.pop("events", None)
    return snap


@router.get("/sessions/{sid}/heatmap")
def get_heatmap(sid: str):
    return get_session(sid)["heatmap"]


@router.get("/sessions/{sid}/history")
def get_history(sid: str):
    st = get_session(sid)
    return {"versions": st.get("versions", []), "scores": st.get("scores", []), "timeline": st.get("timeline", [])}


@router.get("/sessions/{sid}/events")
async def session_events(sid: str, after: int = -1):
    s = REGISTRY.get(sid)
    if s is None:
        snap = _snapshot(sid)
        if snap is None:
            raise HTTPException(404, "unknown session")

        async def replay():
            for item in snap.get("events", []):
                if item.get("seq", -1) > after:
                    yield f"data: {json.dumps(item, default=str)}\n\n"
            yield "event: end\ndata: {}\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream")

    q = s.subscribe()

    async def stream():
        try:
            for item in list(s.events):  # catch up (reconnects pass ?after=)
                if item["seq"] > after:
                    yield f"data: {json.dumps(item, default=str)}\n\n"
            idle = 0
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.12)
                    idle += 1
                    if idle % 120 == 0:
                        yield ": keep-alive\n\n"
                    continue
                if item["seq"] > after:
                    yield f"data: {json.dumps(item, default=str)}\n\n"
        finally:
            s.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/sessions/{sid}/mutants/{mid}")
def mutant(sid: str, mid: int):
    s = _live(sid)
    try:
        return s.mutant_view(mid)
    except KeyError:
        raise HTTPException(404, "unknown mutant") from None


# ---------------------------------------------------------------- actions
@router.post("/sessions/{sid}/analyze")
def analyze(sid: str):
    s = _live(sid)
    return _job(s, "analyze", s.analyze)


class Investigate(BaseModel):
    mutant_id: int


@router.post("/sessions/{sid}/investigate")
def investigate(sid: str, req: Investigate):
    s = _live(sid)
    if s.mut is None:
        raise HTTPException(409, "run the analysis first")
    return _job(s, "investigate", lambda: s.investigate(req.mutant_id))


def _bs(s: Session, bs: str):
    try:
        return s.diagnosis(bs)
    except KeyError:
        raise HTTPException(404, "unknown blind spot") from None


@router.post("/sessions/{sid}/blindspots/{bs}/corrective")
def corrective(sid: str, bs: str):
    s = _live(sid)
    _bs(s, bs)
    return _job(s, "corrective", lambda: s.generate_corrective(bs))


@router.post("/sessions/{sid}/blindspots/{bs}/execute")
def execute(sid: str, bs: str):
    s = _live(sid)
    if not _bs(s, bs).proposed_test:
        raise HTTPException(409, "generate a corrective test first")
    return _job(s, "execute", lambda: s.execute_corrective(bs))


@router.post("/sessions/{sid}/blindspots/{bs}/verify")
def verify(sid: str, bs: str):
    s = _live(sid)
    if not _bs(s, bs).proposed_test:
        raise HTTPException(409, "generate a corrective test first")
    return _job(s, "verify", lambda: s.verify(bs))


class TryInputs(BaseModel):
    args: list[str] | None = Field(None, max_length=12)


@router.post("/sessions/{sid}/blindspots/{bs}/counterexample")
def counterexample(sid: str, bs: str, req: TryInputs):
    s = _live(sid)
    _bs(s, bs)
    try:
        return s.try_inputs(bs, req.args)
    except (ValueError, SyntaxError):
        raise HTTPException(400, "arguments must be Python literals, e.g. 18, 'abc', [1, 2]") from None
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/sessions/{sid}/resolve_all")
def resolve_all(sid: str):
    s = _live(sid)
    return _job(s, "resolve_all", s.resolve_all)


@router.post("/sessions/{sid}/cancel")
def cancel(sid: str):
    s = _live(sid)
    return {"cancelled": s.cancel()}


@router.get("/runs/{run_id}/export")
def export_run(run_id: str, format: str = "html"):
    """Blind Spot Intelligence section of a finished generate run's final report."""
    rec = history.get(run_id) if _ID.fullmatch(run_id) else None
    bs = ((rec or {}).get("report") or {}).get("blind_spot")
    if not bs:
        raise HTTPException(404, "this run has no Blind Spot Intelligence section")
    state = {**bs, "id": run_id, "module_name": rec["request"].get("module_name"), "llm": rec["request"].get("llm")}
    name = f"blindspot_run_{run_id}"
    if format == "json":
        return PlainTextResponse(json.dumps(state, indent=2, default=str), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if format == "csv":
        return PlainTextResponse(export_csv(state), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if format == "html":
        return HTMLResponse(export_html(state))
    raise HTTPException(400, "format must be json, csv or html")


@router.get("/sessions/{sid}/export")
def export(sid: str, format: str = "json"):
    st = get_session(sid)
    name = f"blindspot_{st.get('module_name', 'module')}_{sid}"
    if format == "json":
        return PlainTextResponse(json.dumps(st, indent=2, default=str), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if format == "csv":
        return PlainTextResponse(export_csv(st), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if format == "html":
        return HTMLResponse(export_html(st))
    raise HTTPException(400, "format must be json, csv or html")
