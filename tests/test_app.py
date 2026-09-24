"""Tests for the Lambda handler's sender-authorization gate (issue #30)."""
import pytest

from planner import app, planner, render, stores

ALLOWED = "forwarder@example.com"
VERDICTS = app._REQUIRED_VERDICTS

RAW_EMAIL = (
    b"From: farm@example.net\r\n"
    b"To: plan@example.org\r\n"
    b"Subject: CSA Week 1\r\n"
    b"Date: Tue, 02 Jun 2026 08:00:00 -0700\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Share contents: garlic scapes, sugar snap peas, and sweet salad turnips.\r\n"
)


def _event(source=ALLOWED, verdicts=None, receipt=True):
    statuses = {name: "PASS" for name in VERDICTS}
    statuses.update(verdicts or {})
    record = {
        "mail": {
            "messageId": "msg-1",
            "source": source,
            "commonHeaders": {"subject": "CSA Week 1", "date": "Tue, 02 Jun 2026 08:00:00 -0700"},
        },
    }
    if receipt:
        record["receipt"] = {name: {"status": s} for name, s in statuses.items()}
    return {"Records": [{"ses": record}]}


@pytest.fixture
def calls(monkeypatch):
    """Stub every AWS-touching store function and record each call by name."""
    log = []

    def stub(name, ret=None):
        def fn(*a, **kw):
            log.append(name)
            return ret
        monkeypatch.setattr(stores, name, fn)

    stub("already_processed", False)
    stub("read_raw_email", RAW_EMAIL)
    stub("load_corpus", [])
    stub("recent_recipe_ids", set())
    stub("get_photo", b"")
    stub("archive_html", "plan-archive/x.html")
    stub("send_email", "ses-id")
    stub("send_diagnostic")
    stub("log_plan")
    monkeypatch.setattr(planner, "build_plan", lambda *a, **kw: {
        "recipes": [{}], "recipe_ids": ["r1"], "veggies_uncovered": [],
        "veggies_covered": [], "forced_repeats": []})
    monkeypatch.setattr(render, "render_html", lambda *a, **kw: ("<html/>", []))
    monkeypatch.setenv("ALLOWED_SENDERS", f"other@example.com, {ALLOWED.upper()}")
    return log


@pytest.mark.parametrize("name", VERDICTS)
@pytest.mark.parametrize("status", ["FAIL", "GRAY", "PROCESSING_FAILED", None])
def test_any_verdict_not_pass_is_rejected_with_no_side_effects(calls, name, status):
    out = app.lambda_handler(_event(verdicts={name: status}), None)
    assert out["status"] == "rejected"
    assert calls == []


def test_missing_receipt_is_rejected(calls):
    assert app.lambda_handler(_event(receipt=False), None)["status"] == "rejected"
    assert calls == []


def test_off_allowlist_source_is_rejected(calls):
    assert app.lambda_handler(_event(source="someone@example.com"), None)["status"] == "rejected"
    assert calls == []


def test_empty_allowlist_fails_closed(calls, monkeypatch):
    monkeypatch.setenv("ALLOWED_SENDERS", "")
    assert app.lambda_handler(_event(), None)["status"] == "rejected"
    assert calls == []


def test_authorized_sender_gets_exactly_one_plan_email(calls):
    out = app.lambda_handler(_event(), None)
    assert out["status"] == "sent"
    assert calls.count("send_email") == 1
    assert calls.count("send_diagnostic") == 0
    assert calls[0] == "already_processed"
