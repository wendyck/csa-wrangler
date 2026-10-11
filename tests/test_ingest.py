"""Tests for planner/ingest.py — the browser extension's preview/save endpoint."""
import base64
import json
import logging

import pytest

from planner import ingest, recipe_guard, stores

PAGE_URL = "https://www.example-recipes.com/chicken-kale-soup"
RECIPE = {"@type": "Recipe", "name": "Chicken & Kale Soup",
          "image": "https://www.example-recipes.com/soup.jpg",
          "recipeIngredient": ["1 lb chicken thighs", "1 bunch kale", "4 cups water"]}
SIGNATURE = "AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/20261011/us-west-2/lambda/aws4_request"


def _page(recipe=RECIPE, extra=""):
    return ('<html><head><script type="application/ld+json">%s</script>%s</head>'
            "<body>secret-page-marker</body></html>" % (json.dumps(recipe), extra))


def _event(body=None, method="POST", raw=None, b64=False):
    payload = raw if raw is not None else json.dumps(
        {"action": "preview", "url": PAGE_URL, "html": _page(), **(body or {})})
    if b64:
        payload = base64.b64encode(payload.encode()).decode()
    return {"requestContext": {"http": {"method": method}},
            "headers": {"authorization": SIGNATURE, "content-type": "application/json"},
            "body": payload, "isBase64Encoded": b64}


def _call(event):
    out = ingest.lambda_handler(event, None)
    return out["statusCode"], json.loads(out["body"])


class FakeCorpus:
    """Stands in for the S3 corpus: versioned by a counter, If-Match on put."""

    def __init__(self, recipes=None, conflicts=0):
        self.recipes, self.version, self.conflicts, self.puts = list(recipes or []), 0, conflicts, 0

    def load(self):
        return list(self.recipes)

    def load_for_update(self):
        return list(self.recipes), self.version

    def put(self, recipes, etag):
        if self.conflicts:                 # someone else wrote between our read and write
            self.conflicts -= 1
            self.version += 1
            raise stores.CorpusChanged()
        assert etag == self.version
        self.recipes, self.version, self.puts = recipes, self.version + 1, self.puts + 1


@pytest.fixture
def s3(monkeypatch):
    fake = FakeCorpus([{"title": "Old", "recipe_url": "https://other.example/old"}])
    monkeypatch.setattr(stores, "load_corpus", fake.load)
    monkeypatch.setattr(stores, "load_corpus_for_update", fake.load_for_update)
    monkeypatch.setattr(stores, "put_corpus", fake.put)
    return fake


# ---- request validation ----

@pytest.mark.parametrize("method", ["GET", "PUT", None])
def test_non_post_is_405(s3, method):
    assert _call(_event(method=method))[0] == 405


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"a string"', ""])
def test_bad_json_is_400(s3, raw):
    assert _call(_event(raw=raw))[0] == 400


@pytest.mark.parametrize("body", [
    {"action": "delete"}, {"action": None}, {"url": None}, {"html": ""}, {"html": 42},
    {"url": "javascript:alert(1)"}, {"url": "chrome://newtab"},
])
def test_bad_fields_are_400(s3, body):
    assert _call(_event(body))[0] == 400


def test_base64_body_is_decoded(s3):
    assert _call(_event(b64=True))[0] == 200


def test_oversized_html_is_413(s3):
    big = _page(extra="x" * recipe_guard.MAX_PAGE_BYTES)
    assert _call(_event({"html": big}))[0] == 413


def test_page_without_recipe_markup_is_422(s3):
    status, body = _call(_event({"html": "<html><body><p>just a blog post</p></body></html>"}))
    assert status == 422 and "no recipe found" in body["error"]


def test_recipe_without_ingredients_is_422(s3):
    page = _page({**RECIPE, "recipeIngredient": ["  "]})
    assert _call(_event({"html": page}))[0] == 422


# ---- preview ----

def test_preview_returns_tags_choices_and_exists(s3):
    status, body = _call(_event())
    assert status == 200
    rec = body["record"]
    assert rec["title"] == "Chicken & Kale Soup" and rec["protein"] == "chicken"
    assert "kale" in rec["veggies"] and rec["dish_type"] == "main"
    assert rec["recipe_image"] == "https://www.example-recipes.com/soup.jpg"
    assert rec["ingredient_count"] == 3 and "ingredients" not in rec
    assert body["exists"] is False
    assert "vegetarian" in body["choices"]["protein"] and body["choices"]["dish_type"] == ["main", "side"]
    assert s3.puts == 0                                   # preview never writes


def test_preview_spots_existing_recipe_despite_url_differences(s3):
    s3.recipes.append({"title": "Soup", "recipe_url": "http://example-recipes.com/chicken-kale-soup/"})
    assert _call(_event())[1]["exists"] is True


def test_preview_ignores_overrides(s3):
    rec = _call(_event({"overrides": {"protein": "vegetarian"}}))[1]["record"]
    assert rec["protein"] == "chicken"


def test_off_site_image_is_dropped(s3):
    page = _page({**RECIPE, "image": "https://tracker.evil.example/pixel.gif"})
    assert _call(_event({"html": page}))[1]["record"]["recipe_image"] is None


def test_canonical_url_and_tracking_params(s3):
    page = _page(extra='<link rel="canonical" href="https://www.example-recipes.com/soup">')
    rec = _call(_event({"html": page, "url": PAGE_URL + "?utm_source=pin#comments"}))[1]["record"]
    assert rec["recipe_url"] == "https://www.example-recipes.com/soup"


# ---- save ----

def test_save_adds_once_then_reports_exists(s3):
    status, body = _call(_event({"action": "save"}))
    assert (status, body["status"], body["corpus_size"]) == (200, "added", 2)
    assert s3.recipes[-1]["source"] == "extension" and s3.recipes[-1]["recipe_url"] == PAGE_URL

    status, body = _call(_event({"action": "save"}))
    assert (status, body["status"], body["corpus_size"]) == (200, "exists", 2)
    assert s3.puts == 1


def test_save_applies_valid_overrides(s3):
    body = _call(_event({"action": "save", "overrides": {"protein": "vegetarian", "dish_type": "side"}}))[1]
    assert (body["record"]["protein"], body["record"]["dish_type"]) == ("vegetarian", "side")
    assert (s3.recipes[-1]["protein"], s3.recipes[-1]["dish_type"]) == ("vegetarian", "side")


@pytest.mark.parametrize("overrides", [
    {"protein": "unicorn"}, {"dish_type": "dessert"}, {"title": "renamed"},
    {"recipe_image": "https://evil.example/x.gif"}, ["protein"],
])
def test_save_rejects_bad_overrides(s3, overrides):
    assert _call(_event({"action": "save", "overrides": overrides}))[0] == 400
    assert s3.puts == 0


def test_save_retries_after_corpus_changed(s3):
    s3.conflicts = 1
    status, body = _call(_event({"action": "save"}))
    assert (status, body["status"]) == (200, "added") and s3.puts == 1


def test_save_gives_up_with_409_after_repeated_changes(s3):
    s3.conflicts = ingest.SAVE_ATTEMPTS
    assert _call(_event({"action": "save"}))[0] == 409
    assert s3.puts == 0


# ---- logging ----

@pytest.mark.parametrize("body", [{}, {"action": "save"}, {"html": "<p>no recipe</p>"}])
def test_logs_never_contain_html_or_signature(s3, caplog, body):
    caplog.set_level(logging.DEBUG)
    _call(_event(body))
    text = caplog.text
    assert caplog.records                                # it does log the outcome
    assert "secret-page-marker" not in text and "<p>" not in text
    assert "AKIAEXAMPLE" not in text and "AWS4-HMAC" not in text
