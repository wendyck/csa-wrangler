#!/usr/bin/env python3
"""
Enrich cooking_recipes.json with ingredients, a real title, and a recipe-page image.

Run on YOUR machine (needs internet):
    pip install recipe-scrapers requests
    python3 scrape_ingredients.py

Primary extractor: recipe-scrapers (handles ~hundreds of sites via their schema.org
Recipe / JSON-LD parsing). Fallback: parse JSON-LD <script> ourselves.
Resumable: re-running skips URLs already enriched. Failures -> needs_manual.csv.
"""
import json, csv, time, re, sys
import requests
from recipe_scrapers import scrape_html  # pip install recipe-scrapers

IN   = "cooking_recipes.json"
OUT  = "recipes_enriched.json"
FAIL = "needs_manual.csv"
DELAY = 1.5          # seconds between requests (be polite)
UA = "Mozilla/5.0 (recipe-archiver; personal use)"

def jsonld_fallback(html, url):
    """Pull recipeIngredient/name/image straight from JSON-LD if recipe-scrapers misses."""
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.strip())
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]) + \
                    (data.get("@graph", []) if isinstance(data, dict) else []):
            if not isinstance(node, dict):
                continue
            t = node.get("@type", "")
            if "Recipe" in (t if isinstance(t, list) else [t]):
                ings = node.get("recipeIngredient") or node.get("ingredients") or []
                img = node.get("image")
                if isinstance(img, dict): img = img.get("url")
                if isinstance(img, list): img = img[0].get("url") if isinstance(img[0], dict) else img[0]
                return {"title": node.get("name"), "ingredients": ings, "image": img}
    return None

def enrich(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    html = r.text
    try:
        s = scrape_html(html, org_url=url)
        ings = s.ingredients()
        if ings:
            return {"title": s.title(), "ingredients": ings,
                    "image": (s.image() if hasattr(s, "image") else None)}
    except Exception:
        pass
    fb = jsonld_fallback(html, url)
    if fb and fb["ingredients"]:
        return fb
    raise ValueError("no ingredients found (likely paywalled or no recipe schema)")

def main():
    rows = json.load(open(IN))
    done = {}
    try:
        for r in json.load(open(OUT)):
            done[r["pin_url"]] = r
    except FileNotFoundError:
        pass

    out, fails = [], []
    for i, row in enumerate(rows, 1):
        if row["pin_url"] in done:                       # resume
            out.append(done[row["pin_url"]]); continue
        url = row.get("recipe_url")
        if not url:
            fails.append(row); continue
        try:
            e = enrich(url)
            row = {**row,
                   "title": e["title"] or row["title"],
                   "ingredients": e["ingredients"],
                   "recipe_image": e.get("image") or row["pinterest_image"],
                   "status": "enriched"}
            out.append(row)
            print(f"[{i}/{len(rows)}] OK  {url[:70]}")
        except Exception as ex:
            row = {**row, "status": "needs_manual", "error": str(ex)}
            fails.append(row)
            print(f"[{i}/{len(rows)}] !!  {url[:60]}  -> {ex}")
        json.dump(out, open(OUT, "w"), indent=2)         # checkpoint each item
        time.sleep(DELAY)

    if fails:
        keys = ["pin_url", "recipe_url", "title", "pinterest_image", "status", "error"]
        with open(FAIL, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader(); w.writerows(fails)
    print(f"\nDone. enriched={len(out)}  needs_manual={len(fails)}  (-> {FAIL})")

if __name__ == "__main__":
    main()
