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
import json, csv, time, sys
from pathlib import Path
import requests
from recipe_scrapers import scrape_html  # pip install recipe-scrapers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # shared planner package
from planner.extract import jsonld  # noqa: E402
from planner.recipe_guard import clean_recipe, fetch_html  # noqa: E402

IN   = "cooking_recipes.json"
OUT  = "recipes_enriched.json"
FAIL = "needs_manual.csv"
DELAY = 1.5          # seconds between requests (be polite)
UA = "Mozilla/5.0 (recipe-archiver; personal use)"

def enrich(url):
    """Fetch + extract, then gate through recipe_guard before anything is stored."""
    html = fetch_html(requests.get, url, headers={"User-Agent": UA}, timeout=20)
    return clean_recipe(extract(html, url), url)

def extract(html, url):
    try:
        s = scrape_html(html, org_url=url)
        ings = s.ingredients()
        if ings:
            return {"title": s.title(), "ingredients": ings,
                    "image": (s.image() if hasattr(s, "image") else None)}
    except Exception:
        pass
    fb = jsonld(html)
    if fb:
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
