"""Corpus record helpers shared by the local scripts and the Lambdas (no AWS, no I/O).

  norm(url)                         dedup key for a recipe URL
  make_record(e, url, source, ...)  cleaned {title, ingredients, image} -> tagged corpus record
  merge(corpus, rec)                append rec unless its URL is already present
"""
from urllib.parse import urlsplit

from . import recipe_tagging as rt

DISH_TYPES = ("main", "side")


def norm(u):
    """Normalize a recipe URL for matching: ignore scheme (http/https), a leading 'www.',
    query string, fragment, trailing slash, and case."""
    s = urlsplit((u or "").strip().lower())
    host = s.netloc[4:] if s.netloc.startswith("www.") else s.netloc
    return host + s.path.rstrip("/")


def proteins():
    """Every protein value tag() can produce."""
    return sorted(set(rt.PROTEIN) | {"vegetarian"})


def make_record(e, url, source, overrides=None):
    """Build a tagged corpus record from a recipe_guard.clean_recipe() result.

    overrides may set "protein" or "dish_type" (hand corrections); any other key or an
    out-of-vocabulary value raises ValueError."""
    rec = {"pin_url": "", "title": e["title"] or url, "recipe_url": url,
           "pinterest_image": None, "recipe_image": e["image"],
           "description": "", "ingredients": e["ingredients"],
           "source": source, "status": "enriched"}
    rec.update(rt.tag(rec))   # veggies, protein, is_pasta, dish_type
    for k, v in (overrides or {}).items():
        allowed = {"protein": proteins(), "dish_type": DISH_TYPES}.get(k)
        if allowed is None:
            raise ValueError(f"can't override {k!r}")
        if v not in allowed:
            raise ValueError(f"{k} must be one of {', '.join(allowed)}")
        rec[k] = v
    return rec


def merge(corpus, rec):
    """Append rec to corpus (in place) unless a recipe with the same norm()'d URL exists.
    Returns True if it was added."""
    key = norm(rec.get("recipe_url"))
    if any(norm(r.get("recipe_url")) == key for r in corpus if r.get("recipe_url")):
        return False
    corpus.append(rec)
    return True
