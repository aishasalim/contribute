import json

from api import notify


def test_discovers_single_existing_hermes_dm(tmp_path, monkeypatch):
    (tmp_path / "channel_directory.json").write_text(json.dumps({
        "platforms": {
            "discord": [
                {"id": "123456", "name": "aisha", "type": "dm", "thread_id": None}
            ]
        }
    }))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert notify._discord_target() == "discord:123456"


def test_formats_dm_message():
    text = notify._message({
        "title": "Hermes applied — Example",
        "role": "Software Intern",
        "url": "https://example.com/job",
        "detail": "Resume: swe",
        "dashboard": "https://example.com/radar",
    })
    assert "Hermes applied" in text
    assert "Software Intern" in text
    assert "Resume: swe" in text


def test_formats_applied_message_with_detail_link():
    text = notify._message({
        "title": "Hermes applied — Example",
        "event_type": "applied",
        "role": "Software Intern",
        "company": "Example",
        "track": "SWE",
        "url": "https://example.com/job",
        "detail": "Resume: swe",
        "detail_url": "https://review.example/details/token",
        "dashboard": "https://example.com/radar#role=1",
    })
    assert "I applied to the SWE role Software Intern at Example" in text
    assert "Contribute: https://example.com/radar#role=1" in text
    assert "Private details: https://review.example/details/token" in text


def test_formats_short_answer_review_message():
    text = notify._message({
        "title": "Quick answer needed",
        "event_type": "short_answer",
        "role": "Software Intern",
        "company": "Example",
        "track": "SWE",
        "detail": "Relocation preference",
        "detail_url": "https://review.example/review/token",
    })
    assert "confirm a short answer" in text
    assert "Review and respond:" in text


def test_failed_message_hides_internal_exception_dump():
    text = notify._message({
        "title": "Application failed",
        "event_type": "failed",
        "role": "Software Intern",
        "company": "Example",
        "track": "SWE",
        "detail": "ApiError: secret internal stack dump",
        "dashboard": "https://example.com/radar#role=1",
    })
    assert "No application was submitted" in text
    assert "ApiError" not in text
