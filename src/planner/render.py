"""Render a plan to the meal-plan HTML email (ARCHITECTURE.md §5).

Ported from the gen_meal_plan.py prototype, generalized to N nights driven by the planner
output, with three fixes folded in:
  - subtract this week's CSA veggies from the grocery list (you already have those)
  - fix the categorizer's "pepper" rule so bell/sweet peppers land in Produce, not Pantry
  - image fallback: recipes with no image render a neutral placeholder
The Pinterest link is omitted when a recipe has no pin_url (covers the hand-added recipes).
"""
import html
import re

from .recipe_tagging import _VEG_C

# Day labels, starting Sunday, for up to 7 nights.
_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

STARCH = ["noodle", "pasta", "penne", "elicoidali", "spaghetti", "macaroni", "rigatoni", "ziti"]
PANTRY = [
    "salt", "olive oil", "vegetable oil", "sesame oil", "canola oil", "neutral oil",
    "cooking oil", " oil", "sugar", "brown sugar", "flour", "cornstarch", "corn starch",
    "cornflour", "baking powder", "baking soda", "soy sauce", "fish sauce", "oyster sauce",
    "rice vinegar", "vinegar", "mirin", "sake", "shaoxing", "honey", "maple syrup", "broth",
    "stock", "water", "cumin", "paprika", "turmeric", "coriander", "cinnamon", "curry powder",
    "curry paste", "chili powder", "chile flakes", "chili flakes", "red pepper flake",
    "red pepper flakes", "oregano", "dried", "thyme", "bay leaf", "bay leaves", "rice",
    "sesame seed", "dijon", "mustard", "mayonnaise", "ketchup", "worcestershire", "vanilla",
    "garlic powder", "onion powder", "panko", "bread crumb", "breadcrumb", "nutmeg",
    "cardamom", "clove", "bouillon", "msg", "star anise", "cayenne", "garam masala",
    "sriracha", "chili garlic", "gochujang", "cooking spray", "vegetable stock",
    "chicken stock", "beef stock", "stock cube",
]
PROTEIN = ["chicken", "beef", "steak", "sirloin", "pork", "sausage", "bacon", "salmon", "fish",
           "shrimp", "prawn", "tofu", "lamb", "turkey", "ground", "thigh", "breast", "fillet", "mince"]
DAIRY = ["butter", "egg", "milk", "cream", "cheese", "yogurt", "yoghurt", "parmesan", "feta",
         "mozzarella", "ricotta", "sour cream", "crème", "half-and-half", "coconut milk",
         "coconut cream", "fontina"]
PRODUCE = ["onion", "garlic", "ginger", "carrot", "potato", "tomato", "pepper", "zucchini",
           "mushroom", "spinach", "cucumber", "scallion", "green onion", "shallot", "lemon",
           "lime", "cilantro", "parsley", "basil", "broccoli", "cauliflower", "kale", "chard",
           "cabbage", "celery", "fennel", "leek", "pea", "bean", "eggplant", "squash", "herb",
           "lettuce", "dill", "mint", "jalap", "corn", "beet", "apple", "avocado", "radish",
           "arugula", "asparagus", "sprout"]

# A "pepper" mention is a pantry spice only as ground/peppercorn/flakes; bell & sweet
# peppers (and most fresh chiles) are produce.  This is the §5/§7 pepper-guard fix.
# Pantry is checked first, so "red pepper flakes" stays pantry while "red bell pepper"
# (and a bare "red pepper") is produce.
_PANTRY_PEPPER = re.compile(r"\b(?:black|white|ground|cracked|whole)\s+pepper(?:corns?)?\b"
                            r"|\bpeppercorns?\b|\bpepper\s+flakes?\b|\bflaked?\s+pepper\b")
_PRODUCE_PEPPER = re.compile(r"\b(?:bell|sweet|red|green|yellow|orange|poblano|serrano|"
                             r"jalape|anaheim|banana|cubanelle|shishito)\w*\s*pepper")


def categorize(line):
    l = line.lower()
    if any(s in l for s in STARCH):              # egg noodles / penne -> starch, not dairy
        return "other"
    if "pepper" in l:                            # resolve pepper before generic keywords
        if _PANTRY_PEPPER.search(l):             # ground/cracked pepper, peppercorns, flakes
            return "pantry"
        if _PRODUCE_PEPPER.search(l):            # bell/sweet/chile peppers are produce
            return "produce"
        # otherwise ambiguous ("salt and pepper to taste") -> fall through to keyword logic
    for kw in PANTRY:
        if kw in l:
            return "pantry"
    for kw in PROTEIN:
        if re.search(r"\b" + re.escape(kw), l):
            return "protein"
    for kw in DAIRY:
        if kw in l:
            return "dairy"
    for kw in PRODUCE:
        if kw in l:
            return "produce"
    return "other"


def _is_csa_veggie(line, week_veggies):
    """True if an ingredient line is one of this week's CSA veggies (already on hand)."""
    if not week_veggies:
        return False
    week = set(week_veggies)
    return any(canon in week and _VEG_C[canon].search(line.lower()) for canon in _VEG_C)


def build_grocery(recipes, week_veggies):
    """Union of ingredients across chosen recipes, minus this week's CSA veggies,
    categorized.  Quantities are per-recipe (no merging) in v1."""
    buckets = {"protein": [], "produce": [], "dairy": [], "pantry": [], "other": []}
    seen = set()
    for r in recipes:
        for ing in r.get("ingredients", []):
            ing = re.sub(r"\s+", " ", ing).strip()
            k = ing.lower()
            if not ing or k in seen:
                continue
            seen.add(k)
            if _is_csa_veggie(ing, week_veggies):     # you already have these from the CSA
                continue
            buckets[categorize(ing)].append(ing)
    return buckets


def _img_of(r):
    return r.get("recipe_image") or r.get("pinterest_image")


def _tag_of(r):
    bits = [r.get("protein", "").strip() or "vegetarian"]
    if r.get("is_pasta"):
        bits.append("pasta")
    return " · ".join(bits)


def _esc(s):
    return html.escape(s or "")


_CSS = """
 :root{--ink:#23201c;--muted:#7a736a;--line:#e7e1d8;--accent:#9c5b34;--bg:#faf7f2;}
 *{box-sizing:border-box}
 body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);
      background:var(--bg);margin:0;line-height:1.5;}
 .wrap{max-width:820px;margin:0 auto;padding:32px 20px 64px;}
 h1{font-size:30px;margin:0 0 4px;letter-spacing:-.4px;}
 .sub{color:var(--muted);margin:0 0 28px;font-size:15px;}
 .day{margin:0 0 26px;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;
      box-shadow:0 1px 3px rgba(0,0,0,.04);}
 .day h2{margin:0;padding:12px 18px;font-size:14px;text-transform:uppercase;letter-spacing:1.5px;
      color:#fff;background:var(--accent);}
 .card{display:flex;gap:18px;padding:18px;flex-wrap:wrap;}
 .card img{width:210px;height:150px;object-fit:cover;border-radius:10px;background:#eee;flex:0 0 auto;}
 .noimg{width:210px;height:150px;border-radius:10px;background:#efe9df;flex:0 0 auto;display:flex;
      align-items:center;justify-content:center;color:#b8ad9c;font-size:13px;letter-spacing:1px;}
 .meta{flex:1;min-width:240px;}
 .meta h3{margin:2px 0 4px;font-size:20px;}
 .tag{color:var(--muted);font-size:13px;margin:0 0 10px;}
 .links a{display:inline-block;margin:0 14px 6px 0;font-size:14px;color:var(--accent);text-decoration:none;
      border-bottom:1px solid rgba(156,91,52,.35);}
 .links a:hover{border-color:var(--accent);}
 details{margin-top:8px;}
 summary{cursor:pointer;color:var(--muted);font-size:13px;}
 .ings{margin:8px 0 0;padding-left:18px;font-size:13.5px;color:#46413a;}
 .ings li{margin:2px 0;}
 .side{margin:10px 0 0;padding:8px 12px;background:#f3eee5;border-radius:8px;font-size:13.5px;color:#5a5249;}
 .side .lbl{color:var(--accent);font-weight:600;text-transform:uppercase;letter-spacing:.5px;font-size:11.5px;}
 .side a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(156,91,52,.35);}
 .grocery{margin-top:42px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px;}
 .grocery h2{margin:0 0 4px;font-size:23px;}
 .grocery .note{color:var(--muted);font-size:13px;margin:0 0 18px;}
 .gcat{margin:0 0 18px;}
 .gcat h3{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);
      border-bottom:1px solid var(--line);padding-bottom:4px;}
 .gcat ul{margin:8px 0 0;padding-left:18px;column-count:2;column-gap:28px;font-size:13.5px;}
 .gcat li{margin:3px 0;break-inside:avoid;}
 .pantry{background:#f3eee5;border-radius:10px;padding:14px 18px;}
 .pantry h3{color:#7a5a2a;}
 @media print{body{background:#fff}.day,.grocery{box-shadow:none}details{display:none}}
 @media(max-width:560px){.gcat ul{column-count:1}.card img,.noimg{width:100%;height:200px}}
"""


def _side_block(side, week_veggies):
    """Render the 'suggested side' accompaniment for a night, or '' if none."""
    if not side:
        return ""
    uses = [v for v in side.get("veggies", []) if v in set(week_veggies)]
    uses_txt = (" — uses your " + " &amp; ".join(_esc(v) for v in uses)) if uses else ""
    url = side.get("recipe_url")
    title = ('<a href="' + _esc(url) + '" target="_blank">' + _esc(side.get("title")) + " &rarr;</a>"
             ) if url else _esc(side.get("title"))
    return ('<div class="side"><span class="lbl">Suggested side</span><br>' + title + uses_txt + "</div>")


def render_html(plan, week_veggies, week_label="This Week's Dinners"):
    """Build the full HTML email from a planner.build_plan() result."""
    recipes = plan["recipes"]
    sides = plan.get("sides") or [None] * len(recipes)
    P = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f"<title>{_esc(week_label)}</title><style>{_CSS}</style></head><body><div class=\"wrap\">"]
    P.append(f"<h1>{_esc(week_label)}</h1>")
    veg_line = ", ".join(week_veggies) if week_veggies else "your share"
    P.append(f'<p class="sub">{len(recipes)} dinners from your collection · using this week\'s '
             f"CSA veggies: {_esc(veg_line)}</p>")

    for i, r in enumerate(recipes):
        day = _DAYS[i] if i < len(_DAYS) else f"Night {i + 1}"
        im, url, pin = _img_of(r), r.get("recipe_url"), r.get("pin_url")
        P.append('<section class="day"><h2>' + _esc(day) + '</h2><div class="card">')
        if im:
            P.append('<img src="' + _esc(im) + '" alt="' + _esc(r.get("title")) + '">')
        else:
            P.append('<div class="noimg">no photo</div>')
        P.append('<div class="meta"><h3>' + _esc(r.get("title")) + '</h3>'
                 '<p class="tag">' + _esc(_tag_of(r)) + '</p><div class="links">')
        if url:
            P.append('<a href="' + _esc(url) + '" target="_blank">Recipe &rarr;</a>')
        if pin:
            P.append('<a href="' + _esc(pin) + '" target="_blank">Pinterest pin &rarr;</a>')
        P.append("</div>")
        ings = [re.sub(r"\s+", " ", x).strip() for x in r.get("ingredients", []) if x.strip()]
        if ings:
            P.append("<details><summary>Ingredients (" + str(len(ings)) + ")</summary><ul class=\"ings\">")
            P.extend("<li>" + _esc(x) + "</li>" for x in ings)
            P.append("</ul></details>")
        P.append(_side_block(sides[i] if i < len(sides) else None, week_veggies))
        P.append("</div></div></section>")

    # Grocery list spans the mains and the suggested sides.
    grocery_recipes = recipes + [s for s in sides if s]
    buckets = build_grocery(grocery_recipes, week_veggies)
    P.append('<section class="grocery"><h2>Grocery List</h2>')
    P.append('<p class="note">Combined from all ' + str(len(recipes)) + " dinners plus suggested sides, "
             "with this week's CSA veggies removed. Quantities are per recipe (not merged), so "
             "double-check amounts where an item appears in more than one meal.</p>")
    for key, label in [("protein", "Proteins &amp; Seafood"), ("produce", "Produce"),
                       ("dairy", "Dairy &amp; Refrigerated"), ("other", "Pasta, Bread &amp; Other")]:
        if not buckets[key]:
            continue
        P.append('<div class="gcat"><h3>' + label + "</h3><ul>")
        P.extend("<li>" + _esc(x) + "</li>" for x in buckets[key])
        P.append("</ul></div>")
    if buckets["pantry"]:
        P.append('<div class="gcat pantry"><h3>Pantry Staples (you may already have these)</h3><ul>')
        P.extend("<li>" + _esc(x) + "</li>" for x in buckets["pantry"])
        P.append("</ul></div>")
    P.append("</section></div></body></html>")
    return "\n".join(P)
