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
    "cooking oil", "oil", "sugar", "brown sugar", "flour", "cornstarch", "corn starch",
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
# Real protein names only — NOT prep words like "ground"/"minced"/"thigh", which would
# misfile "minced garlic" or "ground cumin" as a protein. "ground beef" still matches "beef".
PROTEIN = ["chicken", "beef", "steak", "sirloin", "pork", "sausage", "bacon", "prosciutto",
           "pancetta", "ham", "salmon", "tuna", "cod", "halibut", "fish", "shrimp", "prawn",
           "scallop", "tofu", "tempeh", "lamb", "turkey"]
DAIRY = ["butter", "egg", "milk", "cream", "cheese", "yogurt", "yoghurt", "parmesan", "feta",
         "mozzarella", "ricotta", "sour cream", "crème", "half-and-half", "coconut milk",
         "coconut cream", "fontina"]
PRODUCE = ["onion", "garlic", "ginger", "carrot", "potato", "tomato", "pepper", "zucchini",
           "mushroom", "spinach", "cucumber", "scallion", "green onion", "shallot", "lemon",
           "lime", "cilantro", "parsley", "basil", "broccoli", "cauliflower", "kale", "chard",
           "cabbage", "celery", "fennel", "leek", "pea", "bean", "eggplant", "squash", "herb",
           "lettuce", "dill", "mint", "jalapeno", "jalapeño", "chile", "chili pepper",
           "ginger", "corn", "beet", "apple", "avocado", "radish", "arugula", "asparagus",
           "sprout"]

# A "pepper" mention is a pantry spice only as ground/peppercorn/flakes; bell & sweet
# peppers (and most fresh chiles) are produce.  This is the §5/§7 pepper-guard fix.
# Pantry is checked first, so "red pepper flakes" stays pantry while "red bell pepper"
# (and a bare "red pepper") is produce.
_PANTRY_PEPPER = re.compile(r"\b(?:black|white|ground|cracked|whole)\s+pepper(?:corns?)?\b"
                            r"|\bpeppercorns?\b|\bpepper\s+flakes?\b|\bflaked?\s+pepper\b")
_PRODUCE_PEPPER = re.compile(r"\b(?:bell|sweet|red|green|yellow|orange|poblano|serrano|"
                             r"jalape|anaheim|banana|cubanelle|shishito)\w*\s*pepper")


def _kw_re(words):
    """Match any keyword as a whole word (allowing a trailing plural), so a keyword can't
    fire inside a longer word — e.g. 'chard' must not match 'Chardonnay', 'pea' not
    'peanut', 'egg' not 'eggplant'."""
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"\b(?:" + alts + r")(?:es|s)?\b")


_PANTRY_RE = _kw_re(PANTRY)
_PROTEIN_RE = _kw_re(PROTEIN)
_DAIRY_RE = _kw_re(DAIRY)
_PRODUCE_RE = _kw_re(PRODUCE)


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
    if _PANTRY_RE.search(l):
        return "pantry"
    if _PROTEIN_RE.search(l):
        return "protein"
    if _DAIRY_RE.search(l):
        return "dairy"
    if _PRODUCE_RE.search(l):
        return "produce"
    return "other"


def _is_csa_veggie(line, week_veggies):
    """True if an ingredient line is one of this week's CSA veggies (already on hand)."""
    if not week_veggies:
        return False
    week = set(week_veggies)
    return any(canon in week and _VEG_C[canon].search(line.lower()) for canon in _VEG_C)


# Terms used to cluster like ingredients within a category (so the two "basil leaves"
# lines, or the garlic lines, land next to each other). Longest match wins, so e.g.
# "bell pepper" beats "pepper" and "parmesan" beats "cheese". Quantities are NOT merged —
# this only sorts lines so identical ingredients are adjacent for easy manual summing.
_GROUP_TERMS = sorted(
    set(_VEG_C) | set(DAIRY) | set(PRODUCE) | set(STARCH) | {
        "chicken", "beef", "pork", "sausage", "bacon", "prosciutto", "pancetta", "ham",
        "salmon", "tuna", "cod", "halibut", "shrimp", "prawn", "tofu", "lamb", "turkey",
        "olive oil", "sesame oil", "vegetable oil", "soy sauce", "fish sauce", "vinegar",
    },
    key=len, reverse=True)

_QTY_RE = re.compile(
    r"\b(?:cups?|tbsps?|tablespoons?|tsps?|teaspoons?|oz|ounces?|lbs?|pounds?|g|grams?|kg|"
    r"ml|l|cloves?|slices?|cans?|pinch|stems?|sprigs?|handful|bunch|large|small|medium|"
    r"few|about|of|fresh|to|taste)\b|[\d/.¼-¾]+")


def _ingredient_key(line):
    """A clustering key for a grocery line: the longest known ingredient term it contains,
    else a quantity-stripped version of the text so similar lines still group."""
    low = re.sub(r"\([^)]*\)", " ", line.lower())
    for term in _GROUP_TERMS:
        if re.search(r"\b" + re.escape(term) + r"(?:es|s)?\b", low):
            return term
    cleaned = _QTY_RE.sub(" ", low.split(",")[0])
    return re.sub(r"\s+", " ", cleaned).strip()


def build_grocery(recipes, week_veggies):
    """Union of ingredients across chosen recipes, minus this week's CSA veggies,
    categorized. Quantities are per-recipe (not merged); within each category, lines for
    the same ingredient are clustered together so you can sum them up yourself."""
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
    for items in buckets.values():
        items.sort(key=lambda ln: (_ingredient_key(ln), ln.lower()))
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


# Palette (literal hex — email clients, notably Gmail, ignore CSS `var()`).
_INK = "#23201c"
_MUTED = "#7a736a"
_LINE = "#e7e1d8"
_ACCENT = "#9c5b34"          # day headers + links
_PAGE_BG = "#faf7f2"         # cream page background
_SIDE_BG = "#f3eee5"
_LINK = f"color:{_ACCENT};text-decoration:none;border-bottom:1px solid rgba(156,91,52,.35);"

# Progressive enhancement only: stack the photo above the text on narrow screens.
# Inline styles below carry the actual look; this just improves mobile where supported.
_MOBILE_CSS = ("@media only screen and (max-width:560px){"
               ".ph,.mt{display:block!important;width:100%!important;padding-right:0!important}"
               ".ph .photo{width:100%!important;height:200px!important;margin:0 0 12px 0!important}}")


def _side_block(side, week_veggies):
    """Render the 'suggested side' accompaniment for a night, or '' if none."""
    if not side:
        return ""
    uses = [v for v in side.get("veggies", []) if v in set(week_veggies)]
    uses_txt = (" — uses your " + " &amp; ".join(_esc(v) for v in uses)) if uses else ""
    url = side.get("recipe_url")
    name = (f'<a href="{_esc(url)}" target="_blank" style="{_LINK}">{_esc(side.get("title"))} &rarr;</a>'
            if url else _esc(side.get("title")))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:12px;"><tr><td style="background:{_SIDE_BG};border-radius:8px;'
            f'padding:8px 12px;font-size:13.5px;color:#5a5249;line-height:1.5;">'
            f'<span style="color:{_ACCENT};font-weight:600;text-transform:uppercase;letter-spacing:.5px;'
            f'font-size:11.5px;">Suggested side</span><br>{name}{uses_txt}</td></tr></table>')


def _grocery_category(label, items, pantry=False):
    """One grocery category as a self-contained table (so backgrounds render in email)."""
    cell_bg = f"background:{_SIDE_BG};border-radius:10px;padding:14px 18px;" if pantry else ""
    head_color = "#7a5a2a" if pantry else _ACCENT
    parts = [f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
             f'style="margin:0 0 16px;"><tr><td style="{cell_bg}">'
             f'<h3 style="margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:1px;'
             f'color:{head_color};border-bottom:1px solid {_LINE};padding-bottom:4px;">{label}</h3>'
             '<ul style="margin:8px 0 0;padding-left:18px;font-size:13.5px;color:#46413a;">']
    parts += [f'<li style="margin:3px 0;">{_esc(x)}</li>' for x in items]
    parts.append("</ul></td></tr></table>")
    return "".join(parts)


def render_html(plan, week_veggies, week_label="This Week's Dinners"):
    """Build the HTML email from a planner.build_plan() result.

    Table-based layout with inline styles and literal colors so it renders the way the
    meal_plan.html mockup looks (cream page, white rounded cards, accent day headers)
    across email clients — Gmail strips `<style>` reliance, CSS variables, body
    backgrounds, and flex `gap`.
    """
    recipes = plan["recipes"]
    sides = plan.get("sides") or [None] * len(recipes)
    veg_line = ", ".join(week_veggies) if week_veggies else "your share"

    P = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f"<title>{_esc(week_label)}</title><style>{_MOBILE_CSS}</style></head>",
         f'<body style="margin:0;padding:0;background:{_PAGE_BG};line-height:1.5;'
         'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
         f'color:{_INK};">',
         # Outer wrapper carries the page background (clients strip <body> backgrounds).
         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
         f'bgcolor="{_PAGE_BG}" style="background:{_PAGE_BG};"><tr>'
         '<td align="center" style="padding:28px 12px 56px;">',
         '<table role="presentation" width="820" cellpadding="0" cellspacing="0" border="0" '
         'style="width:100%;max-width:820px;">',
         f'<tr><td style="padding:0 4px 22px;">'
         f'<h1 style="margin:0 0 4px;font-size:28px;letter-spacing:-.4px;color:{_INK};">{_esc(week_label)}</h1>'
         f'<p style="margin:0;color:{_MUTED};font-size:15px;">{len(recipes)} dinners from your '
         f"collection · using this week's CSA veggies: {_esc(veg_line)}</p></td></tr>"]

    for i, r in enumerate(recipes):
        day = _DAYS[i] if i < len(_DAYS) else f"Night {i + 1}"
        im, url, pin = _img_of(r), r.get("recipe_url"), r.get("pin_url")
        P.append('<tr><td class="day" style="padding-bottom:22px;">'
                 '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                 f'bgcolor="#ffffff" style="background:#ffffff;border:1px solid {_LINE};border-radius:14px;'
                 'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.04);">'
                 f'<tr><td style="background:{_ACCENT};color:#ffffff;padding:12px 18px;font-size:14px;'
                 f'font-weight:600;text-transform:uppercase;letter-spacing:1.5px;">{_esc(day)}</td></tr>'
                 '<tr><td style="padding:18px;">'
                 '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')
        if im:
            photo = (f'<img class="photo" src="{_esc(im)}" width="210" height="150" '
                     f'alt="{_esc(r.get("title"))}" style="display:block;width:210px;height:150px;'
                     'object-fit:cover;border-radius:10px;background:#eee;">')
        else:
            photo = ('<div class="photo" style="width:210px;height:150px;border-radius:10px;'
                     'background:#efe9df;color:#b8ad9c;font-size:13px;text-align:center;'
                     'line-height:150px;">no photo</div>')
        P.append(f'<td class="ph" valign="top" width="210" style="width:210px;padding-right:18px;'
                 f'vertical-align:top;">{photo}</td>')
        P.append('<td class="mt" valign="top" style="vertical-align:top;">'
                 f'<h3 style="margin:0 0 4px;font-size:20px;color:{_INK};">{_esc(r.get("title"))}</h3>'
                 f'<p style="margin:0 0 10px;color:{_MUTED};font-size:13px;">{_esc(_tag_of(r))}</p>')
        links = []
        if url:
            links.append(f'<a href="{_esc(url)}" target="_blank" style="{_LINK}">Recipe &rarr;</a>')
        if pin:
            links.append(f'<a href="{_esc(pin)}" target="_blank" style="{_LINK}">Pinterest pin &rarr;</a>')
        if links:
            P.append('<p style="margin:0;font-size:14px;">' + " &nbsp;&nbsp; ".join(links) + "</p>")
        P.append(_side_block(sides[i] if i < len(sides) else None, week_veggies))
        ings = [re.sub(r"\s+", " ", x).strip() for x in r.get("ingredients", []) if x.strip()]
        if ings:
            P.append(f'<details style="margin-top:10px;"><summary style="cursor:pointer;color:{_MUTED};'
                     f'font-size:13px;">Ingredients ({len(ings)})</summary>'
                     '<ul style="margin:8px 0 0;padding-left:18px;font-size:13.5px;color:#46413a;">')
            P.extend(f'<li style="margin:2px 0;">{_esc(x)}</li>' for x in ings)
            P.append("</ul></details>")
        P.append("</td></tr></table></td></tr></table></td></tr>")

    # Grocery list spans the mains and the suggested sides.
    buckets = build_grocery(recipes + [s for s in sides if s], week_veggies)
    P.append('<tr><td class="grocery">'
             '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
             f'bgcolor="#ffffff" style="background:#ffffff;border:1px solid {_LINE};border-radius:14px;'
             'box-shadow:0 1px 3px rgba(0,0,0,.04);"><tr><td style="padding:22px 24px;">'
             f'<h2 style="margin:0 0 4px;font-size:23px;color:{_INK};">Grocery List</h2>'
             f'<p style="margin:0 0 16px;color:{_MUTED};font-size:13px;">Combined from all {len(recipes)} '
             "dinners plus suggested sides, with this week's CSA veggies removed. Quantities are per "
             "recipe (not merged), so double-check amounts where an item appears in more than one meal.</p>")
    for key, label in [("protein", "Proteins &amp; Seafood"), ("produce", "Produce"),
                       ("dairy", "Dairy &amp; Refrigerated"), ("other", "Pasta, Bread &amp; Other")]:
        if buckets[key]:
            P.append(_grocery_category(label, buckets[key]))
    if buckets["pantry"]:
        P.append(_grocery_category("Pantry Staples (you may already have these)",
                                   buckets["pantry"], pantry=True))
    P.append("</td></tr></table></td></tr>")
    P.append("</table></td></tr></table></body></html>")
    return "\n".join(P)
