"""AWS-backed data stores and email I/O (ARCHITECTURE §3, §4.7, §9).

  - corpus: read recipes_tagged.json from S3 (cached per cold start)
  - history: DynamoDB single table — plan rows (pk="PLAN", sk="<receipt ISO date>#<ses message id>") power the
    no-repeat window; idempotency rows (pk="SEEN", sk=ses_message_id, two-phase claim with TTL)
  - archive: write rendered HTML to S3
  - email: send the plan (and diagnostics) via SES

boto3 clients are created lazily so the pure-logic modules import without AWS.
"""
import functools
import json
import logging
import time

from . import config

log = logging.getLogger(__name__)

_NO_REPEAT_DAYS_TTL = 400 * 24 * 3600  # idempotency rows expire well after the season


@functools.lru_cache(maxsize=2)
def _client(name):
    import boto3
    return boto3.client(name)


def _table():
    import boto3
    return boto3.resource("dynamodb").Table(config.TABLE)


# ---- corpus ----

@functools.lru_cache(maxsize=1)
def load_corpus():
    obj = _client("s3").get_object(Bucket=config.BUCKET, Key=config.get("CORPUS_S3_KEY"))
    return json.loads(obj["Body"].read())


# ---- raw email ----

MAX_RAW_EMAIL_BYTES = 10 * 1024 * 1024  # a real share email is KB; forwards with images a few MB


def read_raw_email(bucket, key):
    obj = _client("s3").get_object(Bucket=bucket, Key=key)
    if obj["ContentLength"] > MAX_RAW_EMAIL_BYTES:
        obj["Body"].close()
        raise ValueError(f"raw email is {obj['ContentLength']} bytes (limit {MAX_RAW_EMAIL_BYTES})")
    return obj["Body"].read()


# ---- photos ----

def get_photo(s3_key):
    """Bytes of a cookbook dish photo stored under cookbook-photos/ (for inline email embed)."""
    return _client("s3").get_object(Bucket=config.BUCKET, Key=s3_key)["Body"].read()


# ---- history (no-repeat window) ----

def recent_recipe_ids(weeks):
    """recipe_ids used across the last `weeks` plans (newest first)."""
    from boto3.dynamodb.conditions import Key
    resp = _table().query(
        KeyConditionExpression=Key("pk").eq("PLAN"),
        ScanIndexForward=False,
        Limit=weeks,
    )
    ids = set()
    for item in resp.get("Items", []):
        ids.update(item.get("recipe_ids", []))
        ids.update(item.get("side_ids", []))   # sides rotate too, so they don't repeat
    return ids


def log_plan(plan, sk, week_label, ses_message_id, html_s3_key, claimed_date=""):
    """Record a plan. Never replaces an existing row; a repeat write is a benign duplicate."""
    try:
        _table().put_item(Item={
            "pk": "PLAN",
            "sk": sk,
            "recipe_ids": plan["recipe_ids"],
            "side_ids": plan.get("side_ids", []),
            "proteins": plan["proteins"],
            "veggies_covered": plan["veggies_covered"],
            "week_label": week_label,
            "ses_message_id": ses_message_id or "",
            "html_s3_key": html_s3_key,
            "claimed_date": claimed_date,   # sender's Date header — display only, never a key
            "created_at": int(time.time()),
        }, ConditionExpression="attribute_not_exists(sk)")
    except _client("dynamodb").exceptions.ConditionalCheckFailedException:
        log.warning("plan row %s already exists — not overwriting", sk)


# ---- idempotency ----
#
# Two-phase claim: a run first writes a short-lived "in progress" row, and promotes it to
# a long-lived "done" row once the plan email is out. A run that fails releases its claim;
# one that crashes (timeout, OOM) leaves a claim that expires after _CLAIM_TTL, so SES's
# async retries and a DLQ redrive can take over instead of short-circuiting as duplicates.

_CLAIM_TTL = 90   # > the Lambda Timeout (60s) so a live run's claim is never taken over


def already_processed(message_id):
    """True if this SES message is done or another run holds a live claim on it;
    otherwise claim it (in progress) and return False."""
    if not message_id:
        return False
    now = int(time.time())
    try:
        _table().put_item(
            Item={"pk": "SEEN", "sk": message_id, "state": "in_progress", "ttl": now + _CLAIM_TTL},
            # Rows written before the two-phase claim have no state and count as done.
            ConditionExpression="attribute_not_exists(sk) OR (#s = :ip AND #t < :now)",
            ExpressionAttributeNames={"#s": "state", "#t": "ttl"},
            ExpressionAttributeValues={":ip": "in_progress", ":now": now},
        )
        return False
    except _client("dynamodb").exceptions.ConditionalCheckFailedException:
        return True


def mark_done(message_id):
    """Promote the claim to done, so no retry or redelivery re-does the work."""
    if not message_id:
        return
    _table().put_item(Item={"pk": "SEEN", "sk": message_id, "state": "done",
                            "ttl": int(time.time()) + _NO_REPEAT_DAYS_TTL})


def release_claim(message_id):
    """Drop an in-progress claim so an async retry or DLQ redrive can re-do the work.
    A done claim is left alone — the plan email already went out."""
    if not message_id:
        return
    try:
        _table().delete_item(
            Key={"pk": "SEEN", "sk": message_id},
            ConditionExpression="#s = :ip",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":ip": "in_progress"},
        )
    except _client("dynamodb").exceptions.ConditionalCheckFailedException:
        pass


# ---- archive ----

def archive_html(html, sk):
    """Write the rendered plan under a per-message key; IfNoneMatch refuses to overwrite.

    The key embeds the SES messageId, so an existing object can only be from an earlier
    attempt at this same message (a retry) — keep it and carry on."""
    from botocore.exceptions import ClientError
    key = f"{config.ARCHIVE_PREFIX}{sk.replace('#', '_')}.html"
    try:
        _client("s3").put_object(Bucket=config.BUCKET, Key=key, Body=html.encode("utf-8"),
                                 ContentType="text/html; charset=utf-8", IfNoneMatch="*")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
        log.info("archive %s already exists (retry) — keeping it", key)
    return key


# ---- email ----

def send_email(subject, html_body, text_body=None, inline_images=None):
    """Send the plan email. With no inline_images, a plain SES SendEmail.

    inline_images is a list of {"cid": ..., "data": <bytes>} (cookbook dish photos): the
    HTML references each as <img src="cid:...">, so we build a multipart/related MIME and
    send via SendRawEmail (the only SES path that supports inline attachments)."""
    src = config.get("FROM_EMAIL")
    to = config.get("RECIPIENT_EMAIL")
    if not inline_images:
        return _client("ses").send_email(
            Source=src,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Html": {"Data": html_body},
                    **({"Text": {"Data": text_body}} if text_body else {}),
                },
            },
        ).get("MessageId")

    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    root = MIMEMultipart("related")
    root["Subject"] = subject
    root["From"] = src
    root["To"] = to
    alt = MIMEMultipart("alternative")
    root.attach(alt)
    if text_body:
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    for img in inline_images:
        part = MIMEImage(img["data"], "jpeg")   # import_cookbook always uploads JPEG
        part.add_header("Content-ID", f"<{img['cid']}>")
        part.add_header("Content-Disposition", "inline")
        root.attach(part)

    return _client("ses").send_raw_email(
        Source=src,
        Destinations=[to],
        RawMessage={"Data": root.as_bytes()},
    ).get("MessageId")


def send_diagnostic(subject, body):
    """Plain-text heads-up to the recipient when a plan can't be built (ARCHITECTURE §9)."""
    return _client("ses").send_email(
        Source=config.get("FROM_EMAIL"),
        Destination={"ToAddresses": [config.get("RECIPIENT_EMAIL")]},
        Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
    ).get("MessageId")
