"""Tests for the planning algorithm, using a small synthetic corpus plus a real-corpus
season smoke test."""
from planner import planner


def mk(title, veggies, protein="vegetarian", dish_type="main", is_pasta=False):
    return {"title": title, "recipe_url": "http://x/" + title.replace(" ", "-"),
            "veggies": veggies, "protein": protein, "dish_type": dish_type,
            "is_pasta": is_pasta}


def test_greedy_prefers_max_coverage():
    corpus = [
        mk("covers-two", ["carrot", "kale"]),
        mk("covers-one-a", ["carrot"]),
        mk("covers-one-b", ["kale"]),
    ]
    plan = planner.build_plan(["carrot", "kale"], corpus, recent_ids=set(), nights=1)
    assert plan["recipes"][0]["title"] == "covers-two"
    assert plan["veggies_uncovered"] == []


def test_sides_never_chosen():
    corpus = [mk("side-dish", ["carrot"], dish_type="side")]
    plan = planner.build_plan(["carrot"], corpus, recent_ids=set(), nights=3)
    assert plan["recipes"] == []
    assert plan["veggies_uncovered"] == ["carrot"]


def test_meat_protein_cap_enforced():
    corpus = [mk(f"beef-{i}", ["carrot"], protein="beef") for i in range(5)]
    plan = planner.build_plan(["carrot"], corpus, recent_ids=set(), nights=5)
    assert plan["proteins"].count("beef") == planner.MEAT_CAP


def test_vegetarian_not_capped():
    corpus = [mk(f"veg-{i}", ["carrot"], protein="vegetarian") for i in range(5)]
    plan = planner.build_plan(["carrot"], corpus, recent_ids=set(), nights=4)
    assert plan["nights_filled"] == 4  # no cap on vegetarian


def test_recent_recipes_avoided_when_fresh_exists():
    corpus = [mk("recent", ["carrot"]), mk("fresh", ["carrot"])]
    recent = {planner.recipe_id(corpus[0])}
    plan = planner.build_plan(["carrot"], corpus, recent_ids=recent, nights=1)
    assert plan["recipes"][0]["title"] == "fresh"
    assert plan["forced_repeats"] == []


def test_forced_repeat_when_only_recent_covers():
    # Only recipe that covers turnip is recent -> must reuse it, flagged as forced.
    corpus = [mk("turnip-only", ["turnip"]), mk("filler", ["carrot"])]
    recent = {planner.recipe_id(corpus[0])}
    plan = planner.build_plan(["turnip"], corpus, recent_ids=recent, nights=1)
    assert plan["veggies_uncovered"] == []
    assert planner.recipe_id(corpus[0]) in plan["forced_repeats"]


def test_fill_to_n_nights_with_variety():
    corpus = [
        mk("carrot-veg", ["carrot"], protein="vegetarian"),
        mk("chicken-night", ["tomato"], protein="chicken"),
        mk("fish-night", ["tomato"], protein="fish"),
        mk("pasta-night", ["tomato"], protein="vegetarian", is_pasta=True),
    ]
    plan = planner.build_plan(["carrot"], corpus, recent_ids=set(), nights=3)
    assert plan["nights_filled"] == 3
    # variety fillers should bring in proteins beyond the single cover pick
    assert len(set(plan["proteins"])) >= 2


def test_deterministic():
    corpus = [mk(f"r{i}", ["carrot", "tomato"]) for i in range(6)]
    a = planner.build_plan(["carrot", "tomato"], corpus, set(), 4)
    b = planner.build_plan(["carrot", "tomato"], corpus, set(), 4)
    assert a["recipe_ids"] == b["recipe_ids"]


# ---- real-corpus season smoke test (skips if corpus not present) ----

def test_full_season_invariants(corpus, share_fixtures):
    from collections import Counter, deque
    window = deque(maxlen=3)
    for week in sorted(share_fixtures, key=lambda w: int(w[4:])):
        recent = set().union(*window) if window else set()
        veggies = share_fixtures[week]["veggies"]
        plan = planner.build_plan(veggies, corpus, recent, nights=6)

        ids = plan["recipe_ids"]
        assert len(ids) == len(set(ids)), f"{week}: duplicate within week"
        assert plan["nights_filled"] == 6, f"{week}: under-filled"
        counts = Counter(plan["proteins"])
        for p, c in counts.items():
            if p in planner.MEATS:
                assert c <= planner.MEAT_CAP, f"{week}: {p} cap exceeded"
        # any repeat of a recent recipe must be explicitly flagged as forced
        for rid in ids:
            if rid in recent:
                assert rid in plan["forced_repeats"], f"{week}: unforced repeat {rid}"
        window.append(set(ids))
