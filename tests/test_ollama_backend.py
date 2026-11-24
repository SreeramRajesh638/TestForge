"""Tests for the Ollama backend against a stand-in server speaking Ollama's
/api/chat protocol (no real model needed)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from testforge.config import Config
from testforge.llm import LLMError, OllamaClient
from testforge.models import GeneratedTests, TestPlan
from testforge.orchestrator import Orchestrator

SIGN_SRC = '''
def sign(x: int) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0
'''

PLAN = {"cases": [{"target": "sign", "category": "boundary", "description": "zero",
                   "inputs": "0", "expected": "0"}]}
FIRST_TESTS = "from sign import sign\n\ndef test_positive():\n    assert sign(5) == 1\n"
REFINED = ("from sign import sign\n\ndef test_zero():\n    assert sign(0) == 0\n\n"
           "def test_negative():\n    assert sign(-1) == -1\n\ndef test_one():\n    assert sign(1) == 1\n")


class FakeOllama(BaseHTTPRequestHandler):
    requests: list[dict] = []
    break_first_reply = False

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        if body["model"] == "missing-model":
            self._send(404, {"error": f"model '{body['model']}' not found"})
            return
        title = body["format"].get("title")
        if FakeOllama.break_first_reply:
            FakeOllama.break_first_reply = False
            content = '{"code": 42}'  # wrong type -> client must retry
        elif title == "TestPlan":
            content = json.dumps(PLAN)
        elif title == "RefinedTests":
            content = json.dumps({"code": REFINED, "equivalent_mutant_ids": [], "notes": "more"})
        else:
            content = json.dumps({"code": FIRST_TESTS, "notes": "first draft"})
        self._send(200, {"message": {"role": "assistant", "content": content},
                         "prompt_eval_count": 100, "eval_count": 20})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    FakeOllama.requests = []
    FakeOllama.break_first_reply = False
    httpd = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_request_uses_schema_and_deterministic_options(server):
    client = OllamaClient(model="tiny", host=server, num_ctx=4096)
    plan = client.complete("planner", "sys", "prompt", TestPlan)
    assert plan.cases[0].target == "sign"
    req = FakeOllama.requests[0]
    assert req["model"] == "tiny" and req["stream"] is False
    assert req["format"]["title"] == "TestPlan"
    assert req["options"] == {"temperature": 0, "num_ctx": 4096}
    assert req["messages"][0]["role"] == "system"
    assert client.usage.input_tokens == 100 and client.usage.output_tokens == 20


def test_invalid_reply_is_retried_with_the_error(server):
    FakeOllama.break_first_reply = True
    client = OllamaClient(model="tiny", host=server)
    out = client.complete("writer", "sys", "prompt", GeneratedTests)
    assert out.code == FIRST_TESTS
    retry = FakeOllama.requests[1]["messages"]
    assert "did not match the required JSON schema" in retry[-1]["content"]


def test_missing_model_gives_pull_hint(server):
    with pytest.raises(LLMError, match="ollama pull missing-model"):
        OllamaClient(model="missing-model", host=server).complete("planner", "s", "p", TestPlan)


def test_unreachable_server_gives_clear_error():
    with pytest.raises(LLMError, match="Is Ollama running"):
        OllamaClient(host="http://127.0.0.1:9", timeout_s=5).complete("planner", "s", "p", TestPlan)


def test_full_pipeline_on_ollama_backend(server, tmp_path):
    module = tmp_path / "sign.py"
    module.write_text(SIGN_SRC)
    cfg = Config(llm="ollama", model="tiny", ollama_host=server, max_refine_iterations=2)
    report = Orchestrator(cfg).run(str(module))
    first, last = report.iterations[0], report.iterations[-1]
    assert last.mutation_score > first.mutation_score
    assert last.tests_passing == last.tests_total == 4
    assert report.cost_usd == 0.0 and report.config["model"] == "tiny"
