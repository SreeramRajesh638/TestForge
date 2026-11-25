"""End-to-end tests of the dashboard API: runs, telemetry, history, experiments."""

import json
import socket
import time
import urllib.request
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    import os
    os.environ["TESTFORGE_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    from testforge.cli import _serve_in_background
    port = _free_port()
    _serve_in_background(port)
    return f"http://127.0.0.1:{port}"


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def wait_finished(base, run_id, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        r = get(base, f"/api/runs/{run_id}")
        if r["status"] != "running":
            return r
        time.sleep(0.5)
    raise AssertionError("run did not finish")


def test_generate_run_is_recorded_in_telemetry_and_history(base):
    src = (EXAMPLES / "text_utils.py").read_text(encoding="utf-8")
    run = post(base, "/api/runs", {"source": src, "module_name": "text_utils.py", "llm": "mock",
                                   "max_refine_iterations": 1})
    assert wait_finished(base, run["run_id"])["status"] == "finished"

    snap = get(base, "/api/telemetry/snapshot?limit=600")
    mine = [e for e in snap["events"] if e["run_id"] == run["run_id"]]
    kinds = {e["kind"] for e in mine}
    assert {"run", "llm_call", "pytest_run", "mutant_run"} <= kinds
    assert all(e["duration_ms"] is not None for e in mine if e["kind"] in ("pytest_run", "mutant_run"))
    assert snap["totals"]["mutation"]["calls"] > 0
    assert any(e["kind"] == "http" and e["path"] == "/api/runs" for e in snap["events"])

    rows = get(base, "/api/history")
    row = next(r for r in rows if r["id"] == run["run_id"])
    assert row["kind"] == "generate" and row["summary"]["module"] == "text_utils"
    rec = get(base, f"/api/history/{run['run_id']}")
    assert rec["events"] and all("t" in e for e in rec["events"])     # replayable
    assert rec["events"][-1]["kind"] == "report"


def test_history_rejects_bad_ids(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/history/..%2Fetc")
    assert e.value.code in (400, 404)


def test_experiments_endpoint_reads_real_csv(base, tmp_path):
    import os
    results = Path(os.environ["TESTFORGE_DATA_DIR"])
    d = results / "exp_demo"
    d.mkdir()
    (d / "runs.csv").write_text(
        "module,config,rep,status,stop_reason,tests,line_coverage,branch_coverage,mutation_score_initial,"
        "mutation_score_final,mutants_total,mutants_equivalent,refine_rounds,input_tokens,output_tokens,"
        "cost_usd,elapsed_s,error\n"
        "a,mock_baseline,0,ok,plateau,10,0.9,0.8,0.4,0.5,20,0,1,0,0,0,3,\n"
        "a,groq_full,0,ok,target_reached,12,1,1,0.8,1.0,20,1,1,100,50,0,9,\n"
        "b,groq_full,0,error,,,,,,,,,,,,,,boom\n", encoding="utf-8")
    exps = get(base, "/api/experiments")
    assert any(x["name"] == "exp_demo" and x["runs"] == 3 for x in exps)
    exp = get(base, "/api/experiments/exp_demo")
    groq = next(s for s in exp["summary"] if s["config"] == "groq_full")
    assert groq["mutation_score_final"] == 1.0 and groq["failed_runs"] == 1
    assert exp["errors"][0]["error"] == "boom"


def test_spa_routes_and_classic_fallback(base):
    for path in ("/", "/automation", "/monitor", "/experiments", "/runs", "/classic", "/classic/automation"):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            assert r.status == 200 and b"<html" in r.read().lower()
