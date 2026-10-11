"""Structured-data recipe extraction from a page's HTML (stdlib only).

Shared by the local scrapers (as the fallback after recipe-scrapers) and anything that
receives page HTML directly. Output is raw and untrusted: callers pass it through
recipe_guard.clean_recipe before storing it.

  jsonld(html)               schema.org Recipe from <script type="application/ld+json">
  microdata(html)            schema.org Recipe from itemprop="recipeIngredient" markup
  extract_structured(html)   JSON-LD, then microdata; ValueError if neither finds a recipe
  page_url(html, tab_url)    the URL to store: same-site canonical link, minus tracking params
"""
import html as ihtml
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .recipe_guard import _registrable, safe_url

_JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)


def _first_image(img):
    if isinstance(img, dict):
        return img.get("url")
    if isinstance(img, list):
        if not img:
            return None
        return img[0].get("url") if isinstance(img[0], dict) else img[0]
    return img


def jsonld(html):
    """{title, ingredients, image} from the first JSON-LD Recipe node with ingredients,
    else None. Handles top-level lists and @graph containers."""
    for block in _JSONLD.findall(html):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        nodes = list(data) if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes += data["@graph"]
        for n in nodes:
            if not isinstance(n, dict):
                continue
            t = n.get("@type", "")
            if "Recipe" not in (t if isinstance(t, list) else [t]):
                continue
            ings = n.get("recipeIngredient") or n.get("ingredients") or []
            if ings:
                return {"title": n.get("name"), "ingredients": ings, "image": _first_image(n.get("image"))}
    return None


def microdata(html):
    """schema.org Microdata (e.g. WordPress Jetpack Recipe cards: smittenkitchen,
    nigella). Ingredients carry itemprop="recipeIngredient"."""
    ings = [ihtml.unescape(re.sub(r'<[^>]+>', '', m)).strip()
            for m in re.findall(r'itemprop=["\']recipeIngredient["\'][^>]*>(.*?)</li>', html, re.S)]
    ings = [i for i in ings if i]
    if not ings:
        return None
    mt = re.search(r'itemprop=["\']name["\'][^>]*>(.*?)<', html, re.S)
    title = ihtml.unescape(mt.group(1)).strip() if mt else None
    if not title:
        og = re.search(r'og:title["\']\s+content=["\']([^"\']+)', html)
        title = ihtml.unescape(og.group(1)) if og else None
    mi = re.search(r'og:image["\']\s+content=["\']([^"\']+)', html)
    return {"title": title, "ingredients": ings, "image": mi.group(1) if mi else None}


_CANONICAL = re.compile(r'<link\b[^>]*\brel=["\']?canonical["\']?[^>]*>', re.I)
_HREF = re.compile(r'\bhref=["\']([^"\']+)["\']', re.I)
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")


def _strip_tracking(u):
    s = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
         if not k.lower().startswith(_TRACKING)]
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q), ""))


def page_url(html, tab_url):
    """The URL to store for a page: its <link rel=canonical> when that's an http(s) URL on
    the same site as tab_url, else tab_url. Either way minus fragment and tracking params,
    so the same recipe shared via different links dedupes (and keeps one no-repeat id)."""
    tab = safe_url(tab_url)
    if not tab:
        raise ValueError("page URL must be http(s)")
    for tag in _CANONICAL.findall(html or ""):
        m = _HREF.search(tag)
        canon = safe_url(ihtml.unescape(m.group(1))) if m else ""
        if canon and _registrable(urlsplit(canon).hostname) == _registrable(urlsplit(tab).hostname):
            return _strip_tracking(canon)
        break
    return _strip_tracking(tab)


def extract_structured(html):
    """JSON-LD first, then microdata. Raises ValueError if the page has no recipe markup."""
    found = jsonld(html) or microdata(html)
    if not found:
        raise ValueError("no recipe found (no JSON-LD or microdata recipe markup)")
    return found
