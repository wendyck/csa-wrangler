"""Tests for planner/corpus.py — shared record building and URL dedup."""
import pytest

from planner import corpus

CLEANED = {"title": "Chicken & Kale Soup", "image": None,
           "ingredients": ["1 lb chicken thighs", "1 bunch kale", "4 cups water"]}


@pytest.mark.parametrize("a,b", [
    ("https://www.bonappetit.com/recipe/kale/", "http://bonappetit.com/recipe/kale"),
    ("https://smittenkitchen.com/k?utm_source=x#c", "https://SmittenKitchen.com/k"),
])
def test_norm_matches_equivalent_urls(a, b):
    assert corpus.norm(a) == corpus.norm(b)


def test_norm_keeps_distinct_paths_apart():
    assert corpus.norm("https://a.com/x") != corpus.norm("https://a.com/y")


def test_make_record_tags_and_shapes():
    rec = corpus.make_record(CLEANED, "https://a.com/soup", source="manual")
    assert rec["protein"] == "chicken"
    assert "kale" in rec["veggies"]
    assert rec["dish_type"] == "main"
    assert rec["source"] == "manual" and rec["status"] == "enriched" and rec["pin_url"] == ""
    assert rec["recipe_url"] == "https://a.com/soup"


def test_make_record_falls_back_to_url_for_missing_title():
    rec = corpus.make_record({**CLEANED, "title": None}, "https://a.com/soup", source="manual")
    assert rec["title"] == "https://a.com/soup"


def test_make_record_overrides():
    rec = corpus.make_record(CLEANED, "https://a.com/soup", source="extension",
                             overrides={"protein": "vegetarian", "dish_type": "side"})
    assert (rec["protein"], rec["dish_type"]) == ("vegetarian", "side")


@pytest.mark.parametrize("overrides", [
    {"protein": "unicorn"},
    {"dish_type": "dessert"},
    {"title": "renamed"},          # only protein/dish_type are overridable
    {"recipe_image": "https://evil.example/x.gif"},
])
def test_make_record_rejects_bad_overrides(overrides):
    with pytest.raises(ValueError):
        corpus.make_record(CLEANED, "https://a.com/soup", source="extension", overrides=overrides)


def test_proteins_cover_every_tagger_output():
    assert {"chicken", "beef", "fish", "tofu", "vegetarian"} <= set(corpus.proteins())


def test_merge_dedups_by_norm():
    c = [{"recipe_url": "https://www.a.com/soup/"}]
    assert corpus.merge(c, {"recipe_url": "http://a.com/soup"}) is False
    assert corpus.merge(c, {"recipe_url": "https://a.com/stew"}) is True
    assert len(c) == 2
