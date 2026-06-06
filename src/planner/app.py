"""Lambda entry point (ARCHITECTURE §1, §4, §9).

SES receipt rule writes the raw MIME to S3 (S3Action) and then invokes this function
(LambdaAction). We read the MIME from S3, parse the week's veggies, build and render the
plan, email it, archive the HTML, and log the plan to history. Idempotent on the SES
message id so retries don't double-send.
"""
import email.utils
import json
import logging
from datetime import date, datetime

from . import config, parse, planner, render, stores

log = logging.getLogger()
log.setLevel(logging.INFO)


def _sk_date(date_header):
    """ISO date for the plan's sort key, from the email Date header (fallback: today)."""
    if date_header:
        try:
            return email.utils.parsedate_to_datetime(date_header).date().isoformat()
        except Exception:
            pass
    return date.today().isoformat()


def _process(message_id, subject, date_header):
    raw = stores.read_raw_email(config.BUCKET, config.RAW_PREFIX + message_id)
    info = parse.parse_email(raw)
    week_label = info["week_label"] or subject or "CSA Share"
    sk = _sk_date(info.get("date") or date_header)

    if not info["veggies"]:
        stores.send_diagnostic(
            f"[CSA planner] No veggies parsed — {week_label}",
            "Couldn't extract any plannable veggies from this week's share.\n\n"
            f"Share line: {info.get('share_line')!r}\nRaw items: {info.get('raw_items')}",
        )
        return {"status": "no-veggies", "sk": sk}

    corpus = stores.load_corpus()
    recent = stores.recent_recipe_ids(config.no_repeat_weeks())
    plan = planner.build_plan(info["veggies"], corpus, recent, config.nights_per_week())

    html = render.render_html(plan, info["veggies"], week_label=week_label)
    html_key = stores.archive_html(html, sk)
    subject_line = f"🥕 {week_label} — {len(plan['recipes'])} dinners"
    msg_id = stores.send_email(subject_line, html)
    stores.log_plan(plan, sk, week_label, msg_id, html_key)

    if plan["veggies_uncovered"]:
        log.warning("uncovered veggies %s for %s", plan["veggies_uncovered"], sk)

    log.info("sent plan %s: %s recipes, covered %s, forced %s",
             sk, len(plan["recipes"]), plan["veggies_covered"], plan["forced_repeats"])
    return {"status": "sent", "sk": sk, "ses_message_id": msg_id,
            "recipe_ids": plan["recipe_ids"], "uncovered": plan["veggies_uncovered"]}


def lambda_handler(event, context):
    record = event["Records"][0]["ses"]
    mail = record["mail"]
    message_id = mail["messageId"]
    headers = mail.get("commonHeaders", {})
    subject = headers.get("subject", "")
    date_header = headers.get("date", "")

    if stores.already_processed(message_id):
        log.info("duplicate delivery %s — skipping", message_id)
        return {"status": "duplicate", "messageId": message_id}

    try:
        return _process(message_id, subject, date_header)
    except parse.NoShareLine as exc:
        # Expected: a non-CSA email (or a format change). Send a heads-up but treat as
        # handled — returning normally avoids SES retries, the DLQ, and the error alarm.
        log.info("ignored unparseable email %s: %s", message_id, exc)
        try:
            stores.send_diagnostic(
                "[CSA planner] Ignored an email I couldn't read",
                f"No 'Share contents:' line found, so no plan was built.\n\n"
                f"messageId: {message_id}\nsubject: {subject}",
            )
        except Exception:
            log.exception("diagnostic email failed")
        return {"status": "unparseable", "messageId": message_id}
    except Exception as exc:                       # noqa: BLE001 - want the DLQ + alarm
        log.exception("planner failed for %s", message_id)
        try:
            stores.send_diagnostic(
                "[CSA planner] Error building this week's plan",
                f"messageId: {message_id}\nsubject: {subject}\nerror: {exc!r}",
            )
        except Exception:
            log.exception("diagnostic email also failed")
        raise                                      # re-raise so SES async retry + DLQ fire


# Local convenience: render a .eml to HTML without AWS.
#   python -m planner.app path/to/email.eml path/to/recipes_tagged.json [out.html]
if __name__ == "__main__":
    import sys

    raw = open(sys.argv[1], "rb").read()
    corpus = json.load(open(sys.argv[2]))
    info = parse.parse_email(raw)
    plan = planner.build_plan(info["veggies"], corpus, set(), 6)
    html = render.render_html(plan, info["veggies"], week_label=info["week_label"])
    out = sys.argv[3] if len(sys.argv) > 3 else "plan.html"
    open(out, "w").write(html)
    print(f"{info['week_label']}: veggies={info['veggies']} -> {len(plan['recipes'])} dinners")
    print(f"wrote {out}")
