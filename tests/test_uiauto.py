"""Tests for the keyword-driven UI automation engine against ShopLite."""

import json
import socket

import pytest

from testforge.uiauto.engine import (_expand, bug_detection_matrix, filter_scenarios,
                                     load_scenarios, run_scenarios)

pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    from testforge.cli import _serve_in_background
    port = _free_port()
    _serve_in_background(port)
    return f"http://127.0.0.1:{port}"


def test_fragments_expand_with_overrides():
    frags = {"f": [{"action": "fill", "target": "a", "value": "1"},
                   {"action": "fill", "target": "b", "value": "2"},
                   {"action": "check", "target": "c"}]}
    steps = _expand([{"include": "f", "with": {"b": "changed", "c": None}}], frags)
    assert steps == [{"action": "fill", "target": "a", "value": "1"},
                     {"action": "fill", "target": "b", "value": "changed"}]


def test_filter_by_tag_and_name():
    sc = load_scenarios()
    assert len(sc) == 17
    assert {s["name"] for s in filter_scenarios(sc, "cart")} >= {"Checkout empties the cart"}
    assert all("form" in s["tags"] for s in filter_scenarios(sc, "form"))


def test_all_scenarios_pass_on_the_clean_app(base_url):
    summary = run_scenarios(base_url, load_scenarios())
    failures = [(r.name, r.message) for r in summary.results if r.status != "passed"]
    assert not failures


def test_injected_bug_is_caught_with_details(base_url):
    sc = filter_scenarios(load_scenarios(), "search")
    summary = run_scenarios(base_url, sc, bugs=["search"])
    failed = [r for r in summary.results if r.status == "failed"]
    assert failed, "the case-sensitivity bug should be detected"
    r = failed[0]
    assert "Showing 0 of 8 products" in r.message          # actual value is reported
    assert r.screenshot_png_b64                             # screenshot of the failing page
    assert [s.status for s in r.steps][-1] in ("failed", "skipped")


def test_bug_hunt_detects_every_bug(base_url):
    bugs = ["search", "clear", "cart", "total", "remove", "email", "password", "phone", "terms"]
    m = bug_detection_matrix(base_url, load_scenarios(), bugs)
    assert m["clean_failures"] == []
    assert m["detected"] == m["total"] == len(bugs), m["bugs"]


def test_unknown_action_is_an_error_not_a_crash(base_url, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"name": "bad", "steps": [{"action": "goto", "path": "/demo"},
                                                          {"action": "dance", "target": "x"}]}]))
    r = run_scenarios(base_url, load_scenarios(bad)).results[0]
    assert r.status == "error" and "unknown action" in r.message
