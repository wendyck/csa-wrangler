"""Tests for planner/extract.py — structured-data extraction shared by scrapers and ingest."""
import json

import pytest

from planner import extract


def _ld(obj):
    return '<html><head><script type="application/ld+json">%s</script></head></html>' % json.dumps(obj)


RECIPE = {"@type": "Recipe", "name": "Kale Salad", "image": "https://a.com/k.jpg",
          "recipeIngredient": ["1 bunch kale", "2 tbsp olive oil"]}


@pytest.mark.parametrize("payload", [
    RECIPE,                                                          # plain node
    [{"@type": "WebPage"}, RECIPE],                                  # top-level list
    {"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, RECIPE]},   # @graph
    {**RECIPE, "@type": ["Recipe", "NewsArticle"]},                  # multi-typed node
])
def test_jsonld_shapes(payload):
    got = extract.jsonld(_ld(payload))
    assert got == {"title": "Kale Salad", "ingredients": ["1 bunch kale", "2 tbsp olive oil"],
                   "image": "https://a.com/k.jpg"}


@pytest.mark.parametrize("image,want", [
    ({"url": "https://a.com/1.jpg"}, "https://a.com/1.jpg"),
    (["https://a.com/1.jpg", "https://a.com/2.jpg"], "https://a.com/1.jpg"),
    ([{"url": "https://a.com/1.jpg"}], "https://a.com/1.jpg"),
    ([], None),
    (None, None),
])
def test_jsonld_image_forms(image, want):
    assert extract.jsonld(_ld({**RECIPE, "image": image}))["image"] == want


def test_jsonld_accepts_legacy_ingredients_key():
    node = {"@type": "Recipe", "name": "Old", "ingredients": ["1 egg"]}
    assert extract.jsonld(_ld(node))["ingredients"] == ["1 egg"]


def test_jsonld_skips_empty_recipe_node_and_bad_json():
    page = ('<script type="application/ld+json">{not json</script>'
            + _ld({"@type": "Recipe", "name": "Empty", "recipeIngredient": []})
            + _ld(RECIPE))
    assert extract.jsonld(page)["title"] == "Kale Salad"


MICRODATA = """<html><head><meta property="og:image" content="https://sk.com/k.jpg"></head><body>
<h2 itemprop="name">Kale &amp; Beans</h2><ul>
<li itemprop="recipeIngredient">1 bunch <b>kale</b></li>
<li itemprop='recipeIngredient'>1 can beans</li></ul></body></html>"""


def test_microdata():
    assert extract.microdata(MICRODATA) == {
        "title": "Kale & Beans", "ingredients": ["1 bunch kale", "1 can beans"],
        "image": "https://sk.com/k.jpg"}


def test_extract_structured_prefers_jsonld_then_microdata():
    assert extract.extract_structured(_ld(RECIPE) + MICRODATA)["title"] == "Kale Salad"
    assert extract.extract_structured(MICRODATA)["title"] == "Kale & Beans"


def test_extract_structured_raises_without_markup():
    with pytest.raises(ValueError, match="no recipe found"):
        extract.extract_structured("<html><body><p>just a blog post</p></body></html>")


@pytest.mark.parametrize("html,tab,want", [
    # no canonical: tracking params and fragment dropped, real query kept
    ("", "https://www.bonappetit.com/recipe/kale?utm_source=x&fbclid=y&serves=4#comments",
     "https://www.bonappetit.com/recipe/kale?serves=4"),
    # same-site canonical wins
    ('<link rel="canonical" href="https://www.bonappetit.com/recipe/kale">',
     "https://bonappetit.com/recipe/kale-amp?gclid=z", "https://www.bonappetit.com/recipe/kale"),
    # attribute order and quoting vary
    ("<link href='https://cooking.nytimes.com/recipes/1-kale' rel=canonical>",
     "https://cooking.nytimes.com/recipes/1-kale?smid=share", "https://cooking.nytimes.com/recipes/1-kale"),
    # off-site canonical is ignored (a page can't redirect what we store elsewhere)
    ('<link rel="canonical" href="https://evil.example/recipe">',
     "https://smittenkitchen.com/2020/kale/", "https://smittenkitchen.com/2020/kale/"),
    # non-http canonical is ignored
    ('<link rel="canonical" href="javascript:alert(1)">',
     "https://smittenkitchen.com/k", "https://smittenkitchen.com/k"),
])
def test_page_url(html, tab, want):
    assert extract.page_url(html, tab) == want


@pytest.mark.parametrize("tab", ["chrome://newtab", "javascript:alert(1)", "", None])
def test_page_url_rejects_non_http_tab(tab):
    with pytest.raises(ValueError):
        extract.page_url("", tab)
