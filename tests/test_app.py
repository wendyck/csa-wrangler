"""Tests for the Lambda handler's sender-authorization gate (issue #30)."""
import pytest

from planner import app, planner, render, stores

ALLOWED = "forwarder@example.com"
VERDICTS = app._REQUIRED_VERDICTS

def _raw(date="Tue, 02 Jun 2026 08:00:00 -0700"):
    return RAW_EMAIL.replace(b"Tue, 02 Jun 2026 08:00:00 -0700", date.encode())


RAW_EMAIL = (
    b"From: farm@example.net\r\n"
    b"To: plan@example.org\r\n"
    b"Subject: CSA Week 1\r\n"
    b"Date: Tue, 02 Jun 2026 08:00:00 -0700\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Share contents: garlic scapes, sugar snap peas, and sweet salad turnips.\r\n"
)


def _event(source=ALLOWED, verdicts=None, receipt=True, message_id="msg-1",
           timestamp="2026-06-02T15:00:01.000Z", date="Tue, 02 Jun 2026 08:00:00 -0700"):
    statuses = {name: "PASS" for name in VERDICTS}
    statuses.update(verdicts or {})
    record = {
        "mail": {
            "messageId": message_id,
            "timestamp": timestamp,
            "source": source,
            "commonHeaders": {"subject": "CSA Week 1", "date": date},
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
    stub("mark_done")
    stub("release_claim")
    monkeypatch.setattr(planner, "build_plan", lambda *a, **kw: {
        "recipes": [{}], "recipe_ids": ["r1"], "veggies_uncovered": [],
        "veggies_covered": [], "forced_repeats": [], "proteins": []})
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


# ---- issue #31: plan storage keys come from SES, not the sender's Date header ----

class _ConditionalCheckFailed(Exception):
    pass


class _FakeDynamo:
    exceptions = type("E", (), {"ConditionalCheckFailedException": _ConditionalCheckFailed})


class _FakeTable:
    """Honours the exact ConditionExpressions stores.py uses; query returns sk-descending
    like DynamoDB."""
    def __init__(self):
        self.items = {}

    def _check(self, key, cond, values):
        row = self.items.get(key)
        if cond is None:
            return
        if cond == "attribute_not_exists(sk)":
            ok = row is None
        elif cond == "attribute_not_exists(sk) OR (#s = :ip AND #t < :now)":
            ok = row is None or (row.get("state") == values[":ip"] and row["ttl"] < values[":now"])
        elif cond == "#s = :ip":
            ok = row is not None and row.get("state") == values[":ip"]
        else:
            raise AssertionError(f"unexpected condition {cond!r}")
        if not ok:
            raise _ConditionalCheckFailed()

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None,
                 ExpressionAttributeValues=None):
        key = (Item["pk"], Item["sk"])
        self._check(key, ConditionExpression, ExpressionAttributeValues)
        self.items[key] = Item

    def delete_item(self, Key, ConditionExpression=None, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None):
        key = (Key["pk"], Key["sk"])
        self._check(key, ConditionExpression, ExpressionAttributeValues)
        self.items.pop(key, None)

    def query(self, KeyConditionExpression, ScanIndexForward, Limit):
        rows = sorted((v for (pk, _), v in self.items.items() if pk == "PLAN"),
                      key=lambda r: r["sk"], reverse=not ScanIndexForward)
        return {"Items": rows[:Limit]}


class _FakeS3:
    """put_object honours IfNoneMatch='*' (S3 conditional write)."""
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, IfNoneMatch=None):
        if IfNoneMatch == "*" and Key in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body


# The real write/read paths under test, captured before any fixture stubs them.
_REAL = {n: getattr(stores, n) for n in ("log_plan", "archive_html", "recent_recipe_ids",
                                         "already_processed", "mark_done", "release_claim")}


@pytest.fixture
def aws(calls, monkeypatch):
    """Real history, archive and idempotency store functions over fake DynamoDB + S3."""
    table, s3 = _FakeTable(), _FakeS3()
    for name, fn in _REAL.items():
        monkeypatch.setattr(stores, name, fn)
    monkeypatch.setattr(stores, "_table", lambda: table)
    monkeypatch.setattr(stores, "_client", lambda n: s3 if n == "s3" else _FakeDynamo())
    return table, s3


def _plan_rows(table):
    return sorted(sk for (pk, sk) in table.items if pk == "PLAN")


def test_same_claimed_date_gives_distinct_keys_and_no_overwrite(aws):
    table, s3 = aws
    first = app.lambda_handler(_event(message_id="m-a"), None)
    second = app.lambda_handler(_event(message_id="m-b"), None)
    assert first["sk"] != second["sk"]
    assert _plan_rows(table) == ["2026-06-02#m-a", "2026-06-02#m-b"]
    assert sorted(s3.objects) == ["plan-archive/2026-06-02_m-a.html",
                                  "plan-archive/2026-06-02_m-b.html"]


def test_key_ignores_date_header(aws, monkeypatch):
    table, _ = aws
    monkeypatch.setattr(stores, "read_raw_email", lambda *a: _raw("Fri, 31 Dec 9999 23:59:59 +0000"))
    out = app.lambda_handler(_event(message_id="m-far", date="Fri, 31 Dec 9999 23:59:59 +0000"), None)
    assert out["sk"] == "2026-06-02#m-far"
    assert table.items[("PLAN", out["sk"])]["claimed_date"].startswith("Fri, 31 Dec 9999")


def test_log_plan_never_replaces_existing_row(aws):
    table, _ = aws
    plan = {"recipe_ids": ["r1"], "proteins": [], "veggies_covered": []}
    stores.log_plan(plan, "2026-06-02#m-a", "Week 1", "ses-1", "k1")
    stores.log_plan({**plan, "recipe_ids": ["evil"]}, "2026-06-02#m-a", "Week 1", "ses-2", "k2")
    assert table.items[("PLAN", "2026-06-02#m-a")]["recipe_ids"] == ["r1"]


def test_far_future_date_header_cannot_evict_no_repeat_window(aws, monkeypatch):
    table, _ = aws
    for i, day in enumerate(["2026-06-02", "2026-06-09", "2026-06-16"]):
        stores.log_plan({"recipe_ids": [f"genuine-{i}"], "proteins": [], "veggies_covered": []},
                      f"{day}#m-{i}", "Week", "ses", "k")
    monkeypatch.setattr(stores, "read_raw_email", lambda *a: _raw("Fri, 31 Dec 9999 23:59:59 +0000"))
    monkeypatch.setattr(planner, "build_plan", lambda *a, **kw: {
        "recipes": [{}], "recipe_ids": ["attacker"], "veggies_uncovered": [],
        "veggies_covered": [], "forced_repeats": [], "proteins": []})
    app.lambda_handler(_event(message_id="m-far", timestamp="2026-06-01T00:00:00Z",
                              date="Fri, 31 Dec 9999 23:59:59 +0000"), None)
    assert stores.recent_recipe_ids(3) == {"genuine-0", "genuine-1", "genuine-2"}


# ---- issue #32: oversized raw MIME is refused before it's read ----

class _Body:
    def __init__(self):
        self.read_called = self.closed = False

    def read(self):
        self.read_called = True
        return b""

    def close(self):
        self.closed = True


def test_read_raw_email_refuses_oversized_object(monkeypatch):
    body = _Body()
    s3 = type("S3", (), {"get_object": lambda self, **kw: {
        "ContentLength": stores.MAX_RAW_EMAIL_BYTES + 1, "Body": body}})()
    monkeypatch.setattr(stores, "_client", lambda n: s3)
    with pytest.raises(ValueError, match="limit"):
        stores.read_raw_email("b", "k")
    assert not body.read_called and body.closed


# ---- issue #33: a failed run releases its idempotency claim so retries can recover ----

def _seen(table, message_id="msg-1"):
    return table.items.get(("SEEN", message_id))


def test_failed_run_releases_claim_and_retry_sends(aws, calls, monkeypatch):
    table, _ = aws
    monkeypatch.setattr(stores, "load_corpus", lambda: (_ for _ in ()).throw(RuntimeError("s3 down")))
    with pytest.raises(RuntimeError):
        app.lambda_handler(_event(), None)
    assert _seen(table) is None
    assert calls.count("send_email") == 0

    monkeypatch.setattr(stores, "load_corpus", lambda: [])   # dependency healthy again
    assert app.lambda_handler(_event(), None)["status"] == "sent"
    assert calls.count("send_email") == 1
    assert _seen(table)["state"] == "done"


def test_successful_run_is_done_and_redelivery_is_duplicate(aws, calls):
    table, _ = aws
    assert app.lambda_handler(_event(), None)["status"] == "sent"
    assert _seen(table)["state"] == "done"
    assert app.lambda_handler(_event(), None)["status"] == "duplicate"
    assert calls.count("send_email") == 1


def test_concurrent_delivery_is_suppressed_while_claim_is_live(aws, calls):
    assert stores.already_processed("msg-1") is False      # another run holds the claim
    assert app.lambda_handler(_event(), None)["status"] == "duplicate"
    assert calls.count("send_email") == 0


def test_expired_in_progress_claim_is_taken_over(aws, calls, monkeypatch):
    table, _ = aws
    table.items[("SEEN", "msg-1")] = {"pk": "SEEN", "sk": "msg-1", "state": "in_progress",
                                      "ttl": 0}              # a run that crashed long ago
    assert app.lambda_handler(_event(), None)["status"] == "sent"
    assert calls.count("send_email") == 1


def test_legacy_seen_row_without_state_counts_as_done(aws, calls):
    table, _ = aws
    table.items[("SEEN", "msg-1")] = {"pk": "SEEN", "sk": "msg-1", "ttl": 0}
    assert app.lambda_handler(_event(), None)["status"] == "duplicate"


def test_failure_after_send_does_not_let_retry_resend(aws, calls, monkeypatch):
    table, _ = aws
    monkeypatch.setattr(stores, "log_plan", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ddb")))
    with pytest.raises(RuntimeError):
        app.lambda_handler(_event(), None)
    assert _seen(table)["state"] == "done"
    assert app.lambda_handler(_event(), None)["status"] == "duplicate"
    assert calls.count("send_email") == 1


def test_retry_after_archive_write_reuses_existing_object(aws, calls, monkeypatch):
    table, s3 = aws
    sends = iter([RuntimeError("ses throttled"), "ses-id"])
    def send(*a, **kw):
        r = next(sends)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(stores, "send_email", send)
    with pytest.raises(RuntimeError):
        app.lambda_handler(_event(), None)
    assert list(s3.objects) == ["plan-archive/2026-06-02_msg-1.html"]
    assert app.lambda_handler(_event(), None)["status"] == "sent"


def test_unparseable_email_is_marked_done(aws, calls, monkeypatch):
    table, _ = aws
    monkeypatch.setattr(stores, "read_raw_email", lambda *a: b"Subject: hi\r\n\r\nnot a share\r\n")
    assert app.lambda_handler(_event(), None)["status"] == "unparseable"
    assert _seen(table)["state"] == "done"
