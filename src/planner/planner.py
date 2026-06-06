"""The meal-planning algorithm (ARCHITECTURE.md §4).

Given this week's canonical CSA veggies, the recipe corpus, and the recipe_ids used in
the last NO_REPEAT_WEEKS plans, build an N-night dinner plan that:
  - covers as many CSA veggies as possible (greedy), preferring fresh (not-recent) recipes
  - respects a protein rotation cap (each meat protein <= MEAT_CAP)
  - allows a *recent* repeat only when it's the sole way to cover a still-uncovered veggie
  - fills remaining nights with variety (unused proteins, a pasta night, fresh recipes)

Pure logic, no AWS. The corpus and history are passed in by the caller.
"""
from .recipe_tagging import MEATS

MEAT_CAP = 2  # at most this many of each meat protein per week

# Aromatics are in nearly every recipe and aren't a "feature" side (no one plates a
# garlic side dish), so we don't suggest accompaniments for them.
AROMATICS = {"garlic", "onion", "shallot", "scallion"}


def recipe_id(rec):
    """Stable id for a recipe: the normalized recipe_url, falling back to title."""
    url = (rec.get("recipe_url") or "").strip().rstrip("/").lower()
    return url or "title:" + (rec.get("title") or "").strip().lower()


def _is_capped(protein, counts):
    return protein in MEATS and counts.get(protein, 0) >= MEAT_CAP


class _Selection:
    """Tracks chosen recipes, covered veggies, and protein counts as we build the plan."""

    def __init__(self, week_veggies):
        self.week = set(week_veggies)
        self.covered = set()
        self.chosen = []          # list of recipe dicts, in pick order
        self.chosen_ids = set()
        self.protein_counts = {}

    @property
    def uncovered(self):
        return self.week - self.covered

    def can_add(self, rec):
        return (recipe_id(rec) not in self.chosen_ids
                and not _is_capped(rec.get("protein", "vegetarian"), self.protein_counts))

    def add(self, rec):
        self.chosen.append(rec)
        self.chosen_ids.add(recipe_id(rec))
        p = rec.get("protein", "vegetarian")
        self.protein_counts[p] = self.protein_counts.get(p, 0) + 1
        self.covered |= (set(rec.get("veggies", [])) & self.week)


def _new_coverage(rec, sel):
    return len(set(rec.get("veggies", [])) & sel.uncovered)


def _cover_pass(sel, pool):
    """Greedy: repeatedly add the candidate covering the most still-uncovered veggies.

    Tie-break by total veggie richness, then recipe_id for determinism.
    """
    while sel.uncovered:
        best = None
        best_key = (0, 0, "")
        for rec in pool:
            if not sel.can_add(rec):
                continue
            gain = _new_coverage(rec, sel)
            if gain == 0:
                continue
            key = (gain, len(rec.get("veggies", [])), recipe_id(rec))
            if best is None or key > best_key:
                best, best_key = rec, key
        if best is None:
            break  # nothing left can cover a remaining veggie
        sel.add(best)


def _variety_key(rec, sel):
    """Higher is better when filling spare nights: reward unused protein, a first pasta
    night, and overall veggie richness. recipe_id tail keeps it deterministic."""
    protein = rec.get("protein", "vegetarian")
    unused_protein = protein not in sel.protein_counts
    has_pasta = any(r.get("is_pasta") for r in sel.chosen)
    adds_pasta = bool(rec.get("is_pasta")) and not has_pasta
    return (unused_protein, adds_pasta, len(rec.get("veggies", [])), recipe_id(rec))


def _suggest_sides(week_veggies, corpus, recent_ids, mains):
    """Pair a veggie side dish with a night (ARCHITECTURE §4.2): for each CSA veggie,
    suggest a `side` recipe that uses it — e.g. CSA carrots -> glazed carrots alongside a
    protein main. Returns (side_by_night, side_ids) where side_by_night is aligned to
    `mains` (a side recipe or None per night).

    Heuristics: prefer veggies no main already uses (use up the share first); prefer fresh
    sides over recently-used; pair to a meat-protein night whose main doesn't already carry
    that veggie, so each suggestion adds variety rather than doubling up.
    """
    week = set(week_veggies)
    sides = [r for r in corpus if r.get("dish_type") == "side" and set(r.get("veggies", [])) & week]
    side_by_night = [None] * len(mains)
    if not sides or not mains:
        return side_by_night, []

    main_veg = set().union(*[set(m.get("veggies", [])) for m in mains])
    # Feature veggies only (skip aromatics). Veggies the mains miss come first; nights
    # with a meat main come first.
    targets = week - AROMATICS
    veg_order = sorted(targets, key=lambda v: (v in main_veg, v))
    night_order = sorted(range(len(mains)),
                         key=lambda i: (mains[i].get("protein") not in MEATS, i))

    used = set()
    for veg in veg_order:
        cands = [s for s in sides if veg in s.get("veggies", []) and recipe_id(s) not in used]
        if not cands:
            continue
        fresh = [s for s in cands if recipe_id(s) not in recent_ids]
        # Any side that uses this veggie qualifies (it needn't be the star); among them
        # prefer one that uses the most of this week's CSA veggies, to use up the share.
        side = max(fresh or cands, key=lambda s: (len(set(s.get("veggies", [])) & week), recipe_id(s)))
        # Prefer a free night whose main lacks this veggie; else any free night.
        target = next((i for i in night_order if side_by_night[i] is None
                       and veg not in set(mains[i].get("veggies", []))), None)
        if target is None:
            target = next((i for i in night_order if side_by_night[i] is None), None)
        if target is None:
            break  # every night already has a suggested side
        side_by_night[target] = side
        used.add(recipe_id(side))
    return side_by_night, [recipe_id(s) for s in side_by_night if s]


def build_plan(week_veggies, corpus, recent_ids, nights):
    """Build the plan. Returns a dict (see module docstring / ARCHITECTURE §4).

    recent_ids: set of recipe_ids used in the last NO_REPEAT_WEEKS plans.
    """
    recent_ids = set(recent_ids or ())
    mains = [r for r in corpus if r.get("dish_type") == "main"]

    relevant = [r for r in mains if set(r.get("veggies", [])) & set(week_veggies)]
    fresh = [r for r in relevant if recipe_id(r) not in recent_ids]
    recent = [r for r in relevant if recipe_id(r) in recent_ids]

    sel = _Selection(week_veggies)

    # 1. Cover veggies with fresh recipes.
    _cover_pass(sel, fresh)

    # 2. Forced-repeat exception: a still-uncovered veggie that ONLY recent recipes touch.
    forced = []
    if sel.uncovered:
        coverable_recent = [r for r in recent if _new_coverage(r, sel) > 0]
        _before = set(sel.chosen_ids)
        _cover_pass(sel, coverable_recent)
        forced = [rid for rid in sel.chosen_ids - _before]

    # 3. Fill remaining nights with variety. Prefer fresh mains; fall back to recent if short.
    def fill_from(candidates):
        while len(sel.chosen) < nights:
            options = [r for r in candidates if sel.can_add(r)]
            if not options:
                break
            sel.add(max(options, key=lambda r: _variety_key(r, sel)))

    chosen_now = sel.chosen_ids
    fill_from([r for r in mains if recipe_id(r) not in recent_ids and recipe_id(r) not in chosen_now])
    if len(sel.chosen) < nights:  # corpus exhausted of fresh mains; allow recent for variety
        fill_from([r for r in mains if recipe_id(r) not in sel.chosen_ids])

    # 4. Suggest a veggie side dish per night (protein main + veg side).
    sides, side_ids = _suggest_sides(week_veggies, corpus, recent_ids, sel.chosen)
    side_veg = set().union(*[set(s.get("veggies", [])) & sel.week for s in sides if s]) if any(sides) else set()
    covered = sel.covered | side_veg  # a veggie used by a suggested side counts as used

    return {
        "recipes": sel.chosen,
        "recipe_ids": [recipe_id(r) for r in sel.chosen],
        "proteins": [r.get("protein", "vegetarian") for r in sel.chosen],
        "sides": sides,
        "side_ids": side_ids,
        "veggies_week": sorted(sel.week),
        "veggies_covered": sorted(covered),
        "veggies_uncovered": sorted(sel.week - covered),
        "veggies_covered_by_main": sorted(sel.covered),
        "forced_repeats": forced,
        "nights_requested": nights,
        "nights_filled": len(sel.chosen),
    }
