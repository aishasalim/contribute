from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

import hermes.browser as browser
from hermes.adapters.greenhouse import Greenhouse

FIXTURES = Path(__file__).parent / "fixtures" / "forms"
PROFILE = {
    "identity": {"first_name": "Aisha", "email": "aisha@example.com"},
    "resumes": {},
}


@pytest.fixture
def fixture_server():
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def role(url):
    return {
        "id": "fixture",
        "url": url,
        "best_track": "swe",
        "company": "Fixture",
        "title": "Software Intern",
    }


def test_simple_fixture_submits_after_callback(
    fixture_server, tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HEADLESS", "true")
    monkeypatch.setattr(browser, "validate_url", lambda _: None)
    monkeypatch.setattr(browser, "adapter_for", lambda _: Greenhouse())
    seen = []
    result = browser.run_application(
        role(f"{fixture_server}/greenhouse_simple.html"),
        PROFILE,
        dry_run=False,
        browser_state=tmp_path / "browser",
        before_submit=lambda questions: seen.extend(questions),
    )
    assert result["state"] == "submitted"
    assert {question["disposition"] for question in seen} == {
        "filled", "skipped_optional"
    }


def test_required_free_text_fixture_stops(
    fixture_server, tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HEADLESS", "true")
    monkeypatch.setattr(browser, "validate_url", lambda _: None)
    monkeypatch.setattr(browser, "adapter_for", lambda _: Greenhouse())
    result = browser.run_application(
        role(f"{fixture_server}/greenhouse_free_text.html"),
        PROFILE,
        dry_run=True,
        browser_state=tmp_path / "browser",
    )
    assert result["state"] == "pending"
    assert result["questions"][0]["category"] == "free_text"


def test_multiple_choice_is_captured_and_approved_answer_replays(
    fixture_server, tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HEADLESS", "true")
    monkeypatch.setattr(browser, "validate_url", lambda _: None)
    monkeypatch.setattr(browser, "adapter_for", lambda _: Greenhouse())
    url = f"{fixture_server}/greenhouse_multiple_choice.html"

    pending = browser.run_application(
        role(url), PROFILE, dry_run=True, browser_state=tmp_path / "pending"
    )
    assert pending["state"] == "pending"
    assert "Full-time" in pending["questions"][0]["options"]

    approved = browser.run_application(
        role(url),
        PROFILE,
        dry_run=True,
        browser_state=tmp_path / "approved",
        answer_overrides={"preferred work schedule": "Full-time"},
    )
    assert approved["state"] == "dry_run"
    assert approved["questions"][0]["category"] == "approved"
