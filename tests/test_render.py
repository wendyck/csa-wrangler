"""Tests for the HTML renderer + grocery categorizer."""
from planner import planner, render


def test_pepper_guard_bell_and_sweet_are_produce():
    assert render.categorize("2 red bell peppers, sliced") == "produce"
    assert render.categorize("1 sweet pepper") == "produce"
    assert render.categorize("3 poblano peppers") == "produce"


def test_pepper_guard_spice_still_pantry():
    assert render.categorize("1 tsp black pepper") == "pantry"
    assert render.categorize("freshly ground pepper") == "pantry"
    assert render.categorize("1/2 tsp red pepper flakes") == "pantry"
    assert render.categorize("1 tsp whole peppercorns") == "pantry"
    assert render.categorize("salt and pepper to taste") == "pantry"
    assert render.categorize("salt & pepper (to taste)") == "pantry"


def test_csa_veggies_subtracted_from_grocery():
    r = {"title": "Carrot Soup", "ingredients": ["1 lb carrots, peeled", "2 cups water", "1 onion"],
         "veggies": ["carrot", "onion"]}
    buckets = render.build_grocery([r], week_veggies=["carrot"])
    flat = sum(buckets.values(), [])
    assert not any("carrot" in x.lower() for x in flat)   # CSA veggie removed
    assert any("onion" in x.lower() for x in flat)         # non-CSA veggie kept


def test_pinterest_link_omitted_without_pin_url():
    plan = {"recipes": [{"title": "Manual Recipe", "recipe_url": "http://x", "ingredients": [],
                         "veggies": [], "protein": "vegetarian", "is_pasta": False}]}
    out = render.render_html(plan, week_veggies=[])
    assert "Pinterest" not in out


def test_image_fallback_placeholder():
    plan = {"recipes": [{"title": "No Photo", "recipe_url": "http://x", "ingredients": [],
                         "veggies": [], "protein": "fish", "is_pasta": False}]}
    out = render.render_html(plan, week_veggies=[])
    assert "no photo" in out and "<img" not in out


def test_suggested_side_rendered():
    plan = {
        "recipes": [{"title": "Roast Chicken", "recipe_url": "http://x", "ingredients": [],
                     "veggies": [], "protein": "chicken", "is_pasta": False}],
        "sides": [{"title": "Glazed Carrots", "recipe_url": "http://y",
                   "ingredients": ["1 lb carrots", "2 tbsp butter"], "veggies": ["carrot"]}],
    }
    out = render.render_html(plan, week_veggies=["carrot"])
    assert "Suggested side" in out
    assert "Glazed Carrots" in out
    assert "uses your carrot" in out
    # side ingredient (non-CSA) flows into the grocery list; the CSA carrot is removed
    assert "butter" in out.lower()


def test_real_week_render_smoke(corpus):
    plan = planner.build_plan(["carrot", "tomato", "kale"], corpus, recent_ids=set(), nights=6)
    html = render.render_html(plan, ["carrot", "tomato", "kale"], week_label="CSA Week Test")
    assert html.startswith("<!DOCTYPE html>")
    assert html.count('<section class="day">') == 6
    assert "Grocery List" in html
