#!/usr/bin/env python3
"""
Pass 2: enrich the paywalled recipes (NYT Cooking, Seattle Times) using YOUR
logged-in session cookies. Personal use of subscriptions you pay for.

SETUP (one time):
  pip install recipe-scrapers requests
  Export your cookies to a Netscape-format cookies.txt while logged in to NYT/ST.
  Easiest: a browser extension like "Get cookies.txt LOCALLY", export for
  nytimes.com and seattletimes.com (one combined file is fine).

RUN:
  python3 scrape_paywalled.py --cookies cookies.txt --in paywalled_worklist.csv

Merges into recipes_enriched.json (same shape as pass 1). Anything still failing
goes to needs_manual_final.csv.
"""
import json, csv, time, re, argparse, http.cookiejar, html as ihtml
import requests
from recipe_scrapers import scrape_html

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}
DELAY = 2.0  # gentler on subscription sites

def session_with_cookies(path):
    cj = http.cookiejar.MozillaCookieJar(path)
    cj.load(ignore_discard=True, ignore_expires=True)
    s = requests.Session(); s.cookies = cj
    s.headers.update(HEADERS)
    return s

def _og(html, prop):
    m = re.search(r'og:%s"\s+content="([^"]+)"' % prop, html)
    return ihtml.unescape(m.group(1)) if m else None

def jsonld_fallback(html):
    """Pull recipeIngredient/name/image straight from JSON-LD."""
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.strip())
        except Exception:
            continue
        nodes = (data if isinstance(data, list) else [data])
        if isinstance(data, dict):
            nodes = nodes + data.get("@graph", [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            t = node.get("@type", "")
            if "Recipe" in (t if isinstance(t, list) else [t]):
                ings = node.get("recipeIngredient") or node.get("ingredients") or []
                img = node.get("image")
                if isinstance(img, dict): img = img.get("url")
                if isinstance(img, list): img = img[0].get("url") if isinstance(img[0], dict) else img[0]
                if ings:
                    return {"title": node.get("name"), "ingredients": ings, "image": img}
    return None

def seattletimes_fallback(html):
    """ST food articles list ingredients under an INGREDIENTS heading in a <ul>."""
    pos = -1
    for m in re.finditer(r'INGREDIENTS', html):
        # skip the <title>/<meta> occurrence; require a list shortly after
        seg = html[m.start():m.start() + 8000]
        ul = re.search(r'<ul[^>]*>(.*?)</ul>', seg, re.S)
        if ul:
            pos = m.start(); ul_html = ul.group(1); break
    if pos == -1:
        return None
    ings = []
    for li in re.findall(r'<li[^>]*>(.*?)</li>', ul_html, re.S):
        t = ihtml.unescape(re.sub(r'<[^>]+>', '', li)).strip()
        if t:
            ings.append(t)
    if not ings:
        return None
    return {"title": _og(html, "title"), "ingredients": ings, "image": _og(html, "image")}

def enrich(sess, url):
    r = sess.get(url, timeout=25); r.raise_for_status()
    html = r.text
    try:
        s = scrape_html(html, org_url=url)
        ings = s.ingredients()
        if ings:
            return {"title": s.title(), "ingredients": ings,
                    "image": s.image() if hasattr(s, "image") else None}
    except Exception:
        pass
    fb = jsonld_fallback(html)
    if fb:
        return fb
    if "seattletimes.com" in url:
        fb = seattletimes_fallback(html)
        if fb:
            return fb
    raise ValueError("no ingredients (still gated / no recipe markup)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookies", required=True)
    ap.add_argument("--in", dest="infile", default="paywalled_worklist.csv")
    ap.add_argument("--enriched", default="recipes_enriched.json")
    args = ap.parse_args()

    sess = session_with_cookies(args.cookies)
    work = list(csv.DictReader(open(args.infile)))
    try:
        enriched = json.load(open(args.enriched))
    except FileNotFoundError:
        enriched = []
    by_pin = {r["pin_url"]: r for r in enriched}

    fails = []
    for i, row in enumerate(work, 1):
        url = row["recipe_url"]
        if by_pin.get(row["pin_url"], {}).get("status") == "enriched":
            continue
        try:
            e = enrich(sess, url)
            by_pin[row["pin_url"]] = {
                "pin_url": row["pin_url"], "recipe_url": url,
                "title": e["title"], "ingredients": e["ingredients"],
                "recipe_image": e["image"], "status": "enriched"}
            print(f"[{i}/{len(work)}] OK  {row['site']:13} {url[:55]}")
        except Exception as ex:
            fails.append({**row, "error": str(ex)})
            print(f"[{i}/{len(work)}] !!  {row['site']:13} -> {ex}")
        json.dump(list(by_pin.values()), open(args.enriched, "w"), indent=2)
        time.sleep(DELAY)

    if fails:
        with open("needs_manual_final.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fails[0].keys())); w.writeheader(); w.writerows(fails)
    print(f"\nDone. total enriched={sum(1 for r in by_pin.values() if r['status']=='enriched')}  still_failing={len(fails)}")

if __name__ == "__main__":
    main()
