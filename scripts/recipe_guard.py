"""Validation gate between a fetched recipe page and the corpus (issue #34).

The corpus is the planner's trusted data: it ships to S3, and its image URLs are
auto-loaded by the mail client in every plan email. So a scraped page's own title,
ingredient lines and image URL are checked here before any script stores them.

  fetch_html(get, url, **kw)   size- and type-capped page fetch
  clean_recipe(e, page_url)    caps title/ingredients, drops off-site image URLs
"""
from urllib.parse import urlsplit, urlunsplit

MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_TITLE = 200
MAX_INGREDIENTS = 100          # the largest real recipe in the corpus has 35
MAX_INGREDIENT_CHARS = 500

# Image hosts that legitimately serve a recipe site's photos from a different domain.
# Exact hosts, not domains: several are shared CDNs (cloudfront.net, co.uk) where a
# domain-level match would admit anyone's images.
IMAGE_CDN_HOSTS = {
    "static01.nyt.com",                 # cooking.nytimes.com
    "i0.wp.com", "i1.wp.com", "i2.wp.com",  # WordPress/Jetpack sites (smittenkitchen, ...)
    "hips.hearstapps.com",              # thepioneerwoman.com
    "food.fnr.sndimg.com",              # foodnetwork.com
    "images.getrecipekit.com",          # giadzy.com
    "cdn.greatlifepublishing.net",      # 12tomatoes.com
    "d14iv1hjmfkv57.cloudfront.net",    # barefootcontessa.com
    "images.immediate.co.uk",           # bbcgoodfood.com
}

_TWO_LABEL_SUFFIXES = {"co", "com", "org", "net", "ac", "gov"}   # co.uk, com.au, ...


def _registrable(host):
    """Approximate registrable domain: 'www.bbcgoodfood.com' -> 'bbcgoodfood.com',
    'a.b.co.uk' -> 'b.co.uk'."""
    parts = [p for p in (host or "").lower().split(":")[0].split(".") if p]
    n = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _TWO_LABEL_SUFFIXES else 2
    return ".".join(parts[-n:])


def safe_url(u):
    """u if it's an http(s) URL with a host, else ''. For sinks that put URLs in href/src."""
    if not isinstance(u, str):
        return ""
    s = urlsplit(u.strip())
    return urlunsplit(s) if s.scheme in ("http", "https") and s.netloc else ""


def clean_image(img, page_url):
    """Keep an image URL only if it's http(s) and on the recipe's own site or a known CDN."""
    img = safe_url(img)
    if not img:
        return None
    host = (urlsplit(img).hostname or "").lower()
    if host in IMAGE_CDN_HOSTS or _registrable(host) == _registrable(urlsplit(page_url).hostname):
        return img
    return None


def clean_recipe(e, page_url):
    """Bound a scraped {title, ingredients, image}. Raises ValueError if it isn't recipe-shaped."""
    ings = e.get("ingredients") or []
    if not isinstance(ings, list):
        raise ValueError("ingredients is not a list")
    ings = [i.strip()[:MAX_INGREDIENT_CHARS] for i in ings if isinstance(i, str) and i.strip()]
    if not ings:
        raise ValueError("no ingredients found")
    if len(ings) > MAX_INGREDIENTS:
        raise ValueError(f"{len(ings)} ingredients (limit {MAX_INGREDIENTS}) — not a real recipe page?")
    title = e.get("title")
    title = title.strip()[:MAX_TITLE] if isinstance(title, str) else None
    return {"title": title or None, "ingredients": ings, "image": clean_image(e.get("image"), page_url)}


def fetch_html(get, url, **kw):
    """GET url via `get` (requests.get or a Session's .get) and return its text, refusing
    non-HTML responses and bodies over MAX_PAGE_BYTES before anything parses them."""
    r = get(url, stream=True, **kw)
    try:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            raise ValueError(f"not an HTML page ({ctype or 'no Content-Type'})")
        if int(r.headers.get("Content-Length") or 0) > MAX_PAGE_BYTES:
            raise ValueError(f"page too large ({r.headers['Content-Length']} bytes)")
        buf = bytearray()
        for chunk in r.iter_content(64 * 1024):
            buf += chunk
            if len(buf) > MAX_PAGE_BYTES:
                raise ValueError(f"page too large (> {MAX_PAGE_BYTES} bytes)")
        return buf.decode(r.encoding or "utf-8", errors="replace")
    finally:
        r.close()
