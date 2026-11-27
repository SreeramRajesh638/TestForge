"""Tests for the OpenAI-compatible backend (Groq, OpenRouter) against a
stand-in server speaking the /chat/completions protocol."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from testforge.llm import LLMError, OpenAICompatClient, _retry_after
from testforge.models import GeneratedTests, TestPlan

PLAN = {"cases": [{"target": "f", "category": "normal", "description": "d", "inputs": "1",
                   "expected": "2"}]}


class FakeService(BaseHTTPRequestHandler):
    requests: list[dict] = []
    script: list[tuple[int, dict]] = []  # responses to send, in order

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body["_auth"] = self.headers.get("Authorization")
        FakeService.requests.append(body)
        status, payload = FakeService.script.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        if status == 429:
            self.send_header("retry-after", "1")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def ok(content: dict) -> tuple[int, dict]:
    return 200, {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 50, "completion_tokens": 10}}


@pytest.fixture
def server():
    FakeService.requests, FakeService.script = [], []
    httpd = HTTPServer(("127.0.0.1", 0), FakeService)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def client(url, **kw):
    return OpenAICompatClient(provider="groq", base_url=url, api_key="test-key", **kw)


def test_sends_strict_schema_and_reasoning_effort(server):
    FakeService.script = [ok(PLAN)]
    c = client(server, effort="medium")
    assert c.complete("planner", "sys", "p", TestPlan).cases[0].target == "f"
    req = FakeService.requests[0]
    assert req["model"] == "openai/gpt-oss-120b" and req["_auth"] == "Bearer test-key"
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["reasoning_effort"] == "medium" and req["temperature"] == 0
    assert c.usage.input_tokens == 50 and c.usage.output_tokens == 10


def test_waits_out_rate_limit(server):
    FakeService.script = [(429, {"error": {"message": "Rate limit reached"}}), ok(PLAN)]
    assert client(server).complete("planner", "s", "p", TestPlan).cases
    assert len(FakeService.requests) == 2


def test_falls_back_to_json_mode_when_schema_unsupported(server):
    FakeService.script = [(400, {"error": {"message": "response_format json_schema not supported"}}),
                          ok({"code": "def test_x(): pass\n", "notes": "n"})]
    out = client(server).complete("writer", "s", "write tests", GeneratedTests)
    assert out.notes == "n"
    second = FakeService.requests[1]
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON schema" in second["messages"][1]["content"]


def test_invalid_key_is_reported(server):
    FakeService.script = [(401, {"error": {"message": "Invalid API Key"}})]
    with pytest.raises(LLMError, match="rejected the API key"):
        client(server).complete("planner", "s", "p", TestPlan)


def test_missing_key_explains_env_var(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("testforge.llm._key_from_file", lambda provider: None)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        OpenAICompatClient(provider="groq")


def test_key_file_tolerates_bom_and_spaces(tmp_path, monkeypatch):
    from testforge.llm import _key_from_file
    (tmp_path / "groq_key.txt").write_bytes(b"\xef\xbb\xbf  gsk_example  \r\n\r\n")
    monkeypatch.chdir(tmp_path)
    assert _key_from_file("groq") == "gsk_example"


def test_retry_after_parsing():
    assert _retry_after({}, "Please try again in 7.66s.") == pytest.approx(8.66)
    assert _retry_after({}, "Please try again in 1m2.5s") == pytest.approx(63.5)
    assert _retry_after({"retry-after": "4"}, "") == 4
    assert _retry_after({}, "no hint") == 20.0


def test_service_side_json_failure_retries_then_uses_json_mode(server):
    fail = (400, {"error": {"message": "Failed to validate JSON.", "code": "json_validate_failed"}})
    FakeService.script = [fail, fail, ok(PLAN)]
    c = client(server)
    assert c.complete("planner", "s", "p", TestPlan).cases
    kinds = [r["response_format"]["type"] for r in FakeService.requests]
    assert kinds == ["json_schema", "json_schema", "json_object"]
    assert c.schema_mode is True  # only that call fell back
    assert FakeService.requests[0]["max_completion_tokens"] == 32768


def test_schema_retry_does_not_echo_the_invalid_reply(server):
    long_bad = {"code": "x" * 5000}  # missing 'notes' -> invalid
    FakeService.script = [ok(long_bad), ok({"code": "def test_x(): pass\n", "notes": "n"})]
    client(server).complete("writer", "s", "p", GeneratedTests)
    retry = FakeService.requests[1]["messages"]
    assert len(retry) == 3 and "x" * 100 not in json.dumps(retry)
    assert "did not match" in retry[-1]["content"]
