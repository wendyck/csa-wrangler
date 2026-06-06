"""AWS-backed data stores and email I/O (ARCHITECTURE §3, §4.7, §9).

  - corpus: read recipes_tagged.json from S3 (cached per cold start)
  - history: DynamoDB single table — plan rows (pk="PLAN", sk=ISO date) power the
    no-repeat window; idempotency rows (pk="SEEN", sk=ses_message_id, with TTL)
  - archive: write rendered HTML to S3
  - email: send the plan (and diagnostics) via SES

boto3 clients are created lazily so the pure-logic modules import without AWS.
"""
import functools
import json
import time

from . import config

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

def read_raw_email(bucket, key):
    return _client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()


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
    return ids


def log_plan(plan, sk_date, week_label, ses_message_id, html_s3_key):
    _table().put_item(Item={
        "pk": "PLAN",
        "sk": sk_date,
        "recipe_ids": plan["recipe_ids"],
        "proteins": plan["proteins"],
        "veggies_covered": plan["veggies_covered"],
        "week_label": week_label,
        "ses_message_id": ses_message_id or "",
        "html_s3_key": html_s3_key,
        "created_at": int(time.time()),
    })


# ---- idempotency ----

def already_processed(message_id):
    """True if this SES message was already handled; otherwise claim it and return False."""
    if not message_id:
        return False
    try:
        _table().put_item(
            Item={"pk": "SEEN", "sk": message_id,
                  "ttl": int(time.time()) + _NO_REPEAT_DAYS_TTL},
            ConditionExpression="attribute_not_exists(sk)",
        )
        return False
    except _client("dynamodb").exceptions.ConditionalCheckFailedException:
        return True


# ---- archive ----

def archive_html(html, sk_date):
    key = f"{config.ARCHIVE_PREFIX}{sk_date}.html"
    _client("s3").put_object(Bucket=config.BUCKET, Key=key, Body=html.encode("utf-8"),
                             ContentType="text/html; charset=utf-8")
    return key


# ---- email ----

def send_email(subject, html_body, text_body=None):
    return _client("ses").send_email(
        Source=config.get("FROM_EMAIL"),
        Destination={"ToAddresses": [config.get("RECIPIENT_EMAIL")]},
        Message={
            "Subject": {"Data": subject},
            "Body": {
                "Html": {"Data": html_body},
                **({"Text": {"Data": text_body}} if text_body else {}),
            },
        },
    ).get("MessageId")


def send_diagnostic(subject, body):
    """Plain-text heads-up to the recipient when a plan can't be built (ARCHITECTURE §9)."""
    return _client("ses").send_email(
        Source=config.get("FROM_EMAIL"),
        Destination={"ToAddresses": [config.get("RECIPIENT_EMAIL")]},
        Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
    ).get("MessageId")
