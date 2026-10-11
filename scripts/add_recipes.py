#!/usr/bin/env python3
"""
Add hand-picked recipe URLs to the corpus. Scrapes ingredients/title/image,
tags veggies+protein, merges into recipes_tagged.json (dedup by URL).

  pip install -r scripts/requirements.txt
  python3 add_recipes.py --urls urls.txt --corpus recipes_tagged.json

urls.txt: one recipe URL per line; blank lines and #comments ignored.
Re-runnable: URLs already in the corpus are skipped. Failures -> add_failures.csv.
Manually-added recipes have no pin_url (source="manual") — the planner/email
just shows the recipe link for those.
"""
import json, csv, time, argparse, sys
from pathlib import Path
import requests
from recipe_scrapers import scrape_html

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # shared planner package
from planner.corpus import make_record, merge, norm  # noqa: E402
from planner.extract import extract_structured  # noqa: E402
from planner.recipe_guard import clean_recipe, fetch_html  # noqa: E402

UA = "Mozilla/5.0 (recipe-archiver; personal use)"
DELAY = 1.5

def extract(html, url):
    try:
        s = scrape_html(html, org_url=url)
        if s.ingredients():
            return {"title": s.title(), "ingredients": s.ingredients(),
                    "image": s.image() if hasattr(s,"image") else None}
    except Exception: pass
    return extract_structured(html)

def scrape(url):
    """Fetch + extract, then gate through recipe_guard before anything reaches the corpus."""
    html = fetch_html(requests.get, url, headers={"User-Agent": UA}, timeout=20)
    return clean_recipe(extract(html, url), url)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", required=True)
    ap.add_argument("--corpus", default="recipes_tagged.json")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    have = {norm(r["recipe_url"]) for r in corpus if r.get("recipe_url")}
    urls = [l.strip() for l in open(args.urls) if l.strip() and not l.startswith("#")]

    added, skipped, fails = 0, 0, []
    for i, url in enumerate(urls, 1):
        if norm(url) in have:
            skipped += 1; print(f"[{i}/{len(urls)}] skip (already in corpus) {url[:60]}"); continue
        try:
            e = scrape(url)
            rec = make_record(e, url, source="manual")
            merge(corpus, rec); have.add(norm(url)); added += 1
            print(f"[{i}/{len(urls)}] OK [{rec['protein']}] {', '.join(rec['veggies']) or 'no veg'} | {rec['title'][:40]}")
        except Exception as ex:
            fails.append({"url": url, "error": str(ex)})
            print(f"[{i}/{len(urls)}] !! {url[:55]} -> {ex}")
        json.dump(corpus, open(args.corpus, "w"), indent=2)  # checkpoint
        time.sleep(DELAY)

    if fails:
        with open("add_failures.csv","w",newline="") as f:
            w = csv.DictWriter(f, fieldnames=["url","error"]); w.writeheader(); w.writerows(fails)
    print(f"\nDone. added={added} skipped={skipped} failed={len(fails)} | corpus now {len(corpus)} recipes")

if __name__ == "__main__":
    main()
