"""Tests for scripts/recipe_guard.py — the scrape-to-corpus validation gate (issue #34)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recipe_guard as rg  # noqa: E402


class FakeResponse:
    def __init__(self, body, ctype="text/html; charset=utf-8", length=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.headers = {"Content-Type": ctype}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


# A crafted page: off-site image, hundreds of ingredients, one enormous line.
CRAFTED = {"title": "Kale", "image": "https://tracker.example/pixel.gif",
           "ingredients": ["1 bunch kale"] * 501 + ["x" * 4000]}


def test_crafted_page_is_rejected():
    with pytest.raises(ValueError, match="ingredients"):
        rg.clean_recipe(CRAFTED, "https://www.bonappetit.com/recipe/kale")


def test_off_site_image_is_dropped_but_recipe_kept():
    e = rg.clean_recipe({**CRAFTED, "ingredients": ["1 bunch kale", "y" * 4000]},
                        "https://www.bonappetit.com/recipe/kale")
    assert e["image"] is None
    assert len(e["ingredients"][1]) == rg.MAX_INGREDIENT_CHARS


@pytest.mark.parametrize("img,page,keep", [
    ("https://assets.bonappetit.com/photos/k.jpg", "https://www.bonappetit.com/r", True),
    ("https://static01.nyt.com/images/k.jpg", "https://cooking.nytimes.com/recipes/1", True),
    ("https://i0.wp.com/smittenkitchen.com/k.jpg", "https://smittenkitchen.com/r", True),
    ("http://www.nigella.com/assets/k.jpg", "https://www.nigella.com/recipes/k", True),
    ("https://images.immediate.co.uk/k.jpg", "https://www.bbcgoodfood.com/r", True),
    ("https://evil.co.uk/k.jpg", "https://www.bbcgoodfood.com/r", False),   # not the allowlisted host
    ("https://evil.co.uk/k.jpg", "https://www.good.co.uk/r", False),        # co.uk isn't a site
    ("https://abc.cloudfront.net/k.jpg", "https://barefootcontessa.com/r", False),
    ("javascript:alert(1)", "https://www.bonappetit.com/r", False),
    ("data:image/png;base64,AAAA", "https://www.bonappetit.com/r", False),
    ("//www.bonappetit.com/k.jpg", "https://www.bonappetit.com/r", False),   # no scheme
    (["https://www.bonappetit.com/k.jpg"], "https://www.bonappetit.com/r", False),
])
def test_clean_image(img, page, keep):
    assert (rg.clean_image(img, page) is not None) == keep


@pytest.mark.parametrize("u,ok", [
    ("https://a.com/x", True), ("http://a.com/x", True), ("javascript:alert(1)", False),
    (" JavaScript:alert(1)", False), ("data:text/html,x", False), ("", False), (None, False),
])
def test_safe_url(u, ok):
    assert bool(rg.safe_url(u)) == ok


def test_fetch_refuses_non_html():
    r = FakeResponse(b"\x89PNG", ctype="image/png")
    with pytest.raises(ValueError, match="not an HTML page"):
        rg.fetch_html(lambda url, **kw: r, "https://a.com/")
    assert r.closed


def test_fetch_refuses_oversized_declared_length():
    r = FakeResponse("<html>", length=rg.MAX_PAGE_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        rg.fetch_html(lambda url, **kw: r, "https://a.com/")


def test_fetch_refuses_oversized_streamed_body():
    r = FakeResponse(b"a" * (rg.MAX_PAGE_BYTES + 10))       # lies by omitting Content-Length
    with pytest.raises(ValueError, match="too large"):
        rg.fetch_html(lambda url, **kw: r, "https://a.com/")


def test_every_current_corpus_image_still_passes(corpus):
    """The CDN allowlist is seeded from the corpus: tightening it must not drop real photos."""
    dropped = [r["recipe_image"] for r in corpus
               if r.get("recipe_image") and r.get("recipe_url")
               and rg.clean_image(r["recipe_image"], r["recipe_url"]) is None]
    assert dropped == []


CRAFTED_PAGE = """<html><head><script type="application/ld+json">%s</script></head></html>""" % json.dumps(
    {"@type": "Recipe", "name": "Kale Salad", "image": "https://tracker.example/pixel.gif",
     "recipeIngredient": ["1 bunch kale", "2 tbsp olive oil"]})


@pytest.fixture
def add_recipes(monkeypatch):
    """Import add_recipes with requests/recipe_scrapers stubbed (neither is a test dependency)."""
    import importlib
    import types

    def scrape_html(html, org_url):
        raise ValueError("no recipe schema")        # force the JSON-LD path

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=lambda url, **kw: FakeResponse(CRAFTED_PAGE)))
    monkeypatch.setitem(sys.modules, "recipe_scrapers", types.SimpleNamespace(scrape_html=scrape_html))
    monkeypatch.delitem(sys.modules, "add_recipes", raising=False)
    return importlib.import_module("add_recipes")


def test_crafted_page_through_scrape_and_render_has_no_foreign_host(add_recipes):
    from planner import render
    e = add_recipes.scrape("https://www.bonappetit.com/recipe/kale-salad")
    assert e["image"] is None
    rec = {"title": e["title"], "recipe_url": "https://www.bonappetit.com/recipe/kale-salad",
           "recipe_image": e["image"], "ingredients": e["ingredients"], "veggies": ["kale"],
           "protein": "", "id": "r1"}
    plan = {"recipes": [rec], "sides": [], "grocery": {}, "veggies_covered": ["kale"],
            "veggies_uncovered": [], "forced_repeats": []}
    html, _ = render.render_html(plan, ["kale"])
    assert "tracker.example" not in html
