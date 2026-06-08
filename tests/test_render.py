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


def test_prep_words_dont_misfile_aromatics_as_protein():
    # "minced"/"ground" must not push produce into the protein bucket
    assert render.categorize("2-3 teaspoons garlic, (minced)") == "produce"
    assert render.categorize("1 large red onion, minced") == "produce"
    assert render.categorize("1 1/2 tablespoons minced peeled fresh ginger") == "produce"
    # real proteins still classify correctly
    assert render.categorize("1/2 pound ground spicy Italian chicken sausage") == "protein"
    assert render.categorize("1 lb ground beef") == "protein"


def test_keywords_dont_match_inside_longer_words():
    # 'chard' must not fire inside 'Chardonnay', etc. (whole-word matching)
    assert render.categorize("1/4 cup dry white wine (Chardonnay or Sauvignon Blanc)") == "other"
    assert render.categorize("2 cups dry red wine") == "other"
    assert render.categorize("1 large eggplant, cubed") == "produce"      # not dairy via 'egg'
    assert render.categorize("1/4 cup peanuts, chopped") != "produce"     # not produce via 'pea'
    # plurals still classify correctly
    assert render.categorize("2 carrots, diced") == "produce"
    assert render.categorize("3 cups chopped tomatoes") == "produce"
    assert render.categorize("1 bunch rainbow chard") == "produce"        # real chard still works


def test_csa_veggies_subtracted_from_grocery():
    r = {"title": "Carrot Soup", "ingredients": ["1 lb carrots, peeled", "2 cups water", "1 onion"],
         "veggies": ["carrot", "onion"]}
    buckets = render.build_grocery([r], week_veggies=["carrot"])
    flat = sum(buckets.values(), [])
    assert not any("carrot" in x.lower() for x in flat)   # CSA veggie removed
    assert any("onion" in x.lower() for x in flat)         # non-CSA veggie kept


def test_grocery_clusters_like_ingredients():
    # two basil lines and two garlic lines arrive from different recipes, interleaved
    recipes = [
        {"title": "A", "veggies": [], "ingredients": ["A few basil leaves", "2-3 teaspoons garlic, (minced)"]},
        {"title": "B", "veggies": [], "ingredients": ["1 cup basil leaves (, loosely packed)",
                                                       "1 1/2 tsp garlic (, finely minced)"]},
    ]
    produce = render.build_grocery(recipes, week_veggies=[])["produce"]
    # same-ingredient lines must be adjacent
    basil = [i for i, x in enumerate(produce) if "basil" in x.lower()]
    garlic = [i for i, x in enumerate(produce) if "garlic" in x.lower()]
    assert basil == [basil[0], basil[0] + 1], produce
    assert garlic == [garlic[0], garlic[0] + 1], produce


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


def test_main_flags_csa_veggies_used():
    plan = {"recipes": [{"title": "Carrot Kale Stew", "recipe_url": "http://x", "ingredients": [],
                         "veggies": ["carrot", "kale", "potato"], "protein": "beef", "is_pasta": False}]}
    out = render.render_html(plan, week_veggies=["carrot", "kale"])
    assert "Uses your carrot &amp; kale" in out      # only this week's CSA veggies, not potato
    assert "potato" not in out


def test_main_with_no_csa_veggies_has_no_uses_line():
    plan = {"recipes": [{"title": "Plain Salmon", "recipe_url": "http://x", "ingredients": [],
                         "veggies": [], "protein": "fish", "is_pasta": False}]}
    out = render.render_html(plan, week_veggies=["carrot"])
    assert "Uses your" not in out


def test_real_week_render_smoke(corpus):
    plan = planner.build_plan(["carrot", "tomato", "kale"], corpus, recent_ids=set(), nights=6)
    html = render.render_html(plan, ["carrot", "tomato", "kale"], week_label="CSA Week Test")
    assert html.startswith("<!DOCTYPE html>")
    assert html.count('class="day"') == 6
    assert "Grocery List" in html
    assert render._ACCENT in html and render._PAGE_BG in html  # literal palette, not CSS vars
    assert "var(--" not in html                    # Gmail ignores CSS variables
