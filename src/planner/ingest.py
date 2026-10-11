"""Recipe ingest Lambda: the browser extension's "save recipe" endpoint.

Invoked through a Function URL with AuthType AWS_IAM, so AWS has already checked the
caller's SigV4 signature before this code runs; there's no token check here. The
extension sends the page it rendered (logged in, JavaScript executed) and this runs the
same extraction, guard and tagging the local scripts use (extract, recipe_guard, corpus).

POST JSON {"action": "preview" | "save", "url": <tab url>, "html": <page HTML>,
           "overrides": {"protein": ..., "dish_type": ...}}   (overrides: save only)

  preview -> 200 {"record", "exists", "choices"}
  save    -> 200 {"status": "added" | "exists", "record", "corpus_size"}
  400 bad request / override · 405 not POST · 413 page too large · 422 no usable recipe
  409 the corpus kept changing under us (SAVE_ATTEMPTS conditional writes all refused)

Stateless: save re-extracts from the HTML and trusts nothing from an earlier preview.
Logs the URL and outcome only, never the page HTML or request headers.
"""
import base64
import binascii
import json
import logging

from . import corpus, extract, recipe_guard, stores

log = logging.getLogger()
log.setLevel(logging.INFO)

SAVE_ATTEMPTS = 3
# What the popup needs to show; ingredients stay server-side (a count is enough).
RECORD_FIELDS = ("title", "recipe_url", "recipe_image", "veggies", "protein", "is_pasta",
                 "dish_type")


class _Reject(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def _request(event):
    """(action, url, html, overrides) from a Function URL event, or _Reject."""
    method = event.get("requestContext", {}).get("http", {}).get("method")
    if method != "POST":
        raise _Reject(405, "POST only")
    raw = event.get("body") or ""
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        body = json.loads(raw)
    except (ValueError, binascii.Error):
        raise _Reject(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise _Reject(400, "body must be a JSON object")
    action, url, html = body.get("action"), body.get("url"), body.get("html")
    if action not in ("preview", "save"):
        raise _Reject(400, 'action must be "preview" or "save"')
    if not isinstance(url, str) or not isinstance(html, str) or not html:
        raise _Reject(400, "url and html are required strings")
    if len(html.encode("utf-8")) > recipe_guard.MAX_PAGE_BYTES:
        raise _Reject(413, f"page is larger than {recipe_guard.MAX_PAGE_BYTES} bytes")
    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise _Reject(400, "overrides must be an object")
    return action, url, html, overrides


def _record(html, tab_url, overrides):
    """The tagged corpus record for this page, or _Reject."""
    try:
        url = extract.page_url(html, tab_url)
    except ValueError as exc:
        raise _Reject(400, str(exc)) from None
    try:
        cleaned = recipe_guard.clean_recipe(extract.extract_structured(html), url)
    except ValueError as exc:
        raise _Reject(422, str(exc)) from None
    try:
        return corpus.make_record(cleaned, url, source="extension", overrides=overrides)
    except ValueError as exc:
        raise _Reject(400, str(exc)) from None


def _summary(rec):
    return {**{k: rec.get(k) for k in RECORD_FIELDS}, "ingredient_count": len(rec["ingredients"])}


def _exists(recipes, url):
    key = corpus.norm(url)
    return any(corpus.norm(r.get("recipe_url")) == key for r in recipes if r.get("recipe_url"))


def _save(rec):
    """Append rec to the S3 corpus with a conditional write, re-reading on a conflict."""
    for attempt in range(1, SAVE_ATTEMPTS + 1):
        recipes, etag = stores.load_corpus_for_update()
        if not corpus.merge(recipes, rec):
            return "exists", len(recipes)
        try:
            stores.put_corpus(recipes, etag)
            return "added", len(recipes)
        except stores.CorpusChanged:
            log.info("corpus changed during save (attempt %d/%d)", attempt, SAVE_ATTEMPTS)
    raise _Reject(409, "the recipe archive kept changing; try again")


def lambda_handler(event, context):
    url = None
    try:
        action, tab_url, html, overrides = _request(event)
        url = tab_url
        if action == "preview":
            rec = _record(html, tab_url, {})
            url = rec["recipe_url"]
            out = {"record": _summary(rec), "exists": _exists(stores.load_corpus(), url),
                   "choices": {"protein": corpus.proteins(), "dish_type": list(corpus.DISH_TYPES)}}
            log.info("preview %s exists=%s", url, out["exists"])
            return _response(200, out)
        rec = _record(html, tab_url, overrides)
        url = rec["recipe_url"]
        status, size = _save(rec)
        log.info("save %s -> %s (corpus %d)", url, status, size)
        return _response(200, {"status": status, "record": _summary(rec), "corpus_size": size})
    except _Reject as exc:
        log.info("rejected %s: %d %s", url, exc.status, exc.error)
        return _response(exc.status, {"error": exc.error})
