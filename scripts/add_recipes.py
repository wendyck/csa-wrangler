#!/usr/bin/env python3
"""
Add hand-picked recipe URLs to the corpus. Scrapes ingredients/title/image,
tags veggies+protein, merges into recipes_tagged.json (dedup by URL).

  pip install recipe-scrapers requests
  python3 add_recipes.py --urls urls.txt --corpus recipes_tagged.json

urls.txt: one recipe URL per line; blank lines and #comments ignored.
Re-runnable: URLs already in the corpus are skipped. Failures -> add_failures.csv.
Manually-added recipes have no pin_url (source="manual") — the planner/email
just shows the recipe link for those.
"""
import json, csv, time, re, argparse
import html as ihtml
import requests
from urllib.parse import urlsplit, urlunsplit
from recipe_scrapers import scrape_html
import recipe_tagging as rt

UA = "Mozilla/5.0 (recipe-archiver; personal use)"
DELAY = 1.5

def norm(u):  # dedup key: drop query + fragment + trailing slash
    s = urlsplit(u); return urlunsplit((s.scheme, s.netloc, s.path.rstrip("/"), "", "")).lower()

def jsonld(html):
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try: data = json.loads(m.strip())
        except Exception: continue
        stack = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and data.get("@graph"): stack += data["@graph"]
        for n in stack:
            if not isinstance(n, dict): continue
            t = n.get("@type",""); t = t if isinstance(t, list) else [t]
            if "Recipe" in t:
                img = n.get("image")
                if isinstance(img, dict): img = img.get("url")
                if isinstance(img, list): img = img[0].get("url") if isinstance(img[0], dict) else img[0]
                return {"title": n.get("name"), "ingredients": n.get("recipeIngredient") or [], "image": img}
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

def scrape(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20); r.raise_for_status()
    try:
        s = scrape_html(r.text, org_url=url)
        if s.ingredients():
            return {"title": s.title(), "ingredients": s.ingredients(),
                    "image": s.image() if hasattr(s,"image") else None}
    except Exception: pass
    fb = jsonld(r.text)
    if fb and fb["ingredients"]: return fb
    fb = microdata(r.text)
    if fb: return fb
    raise ValueError("no ingredients found")

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
            rec = {"pin_url": "", "title": e["title"] or url, "recipe_url": url,
                   "pinterest_image": None, "recipe_image": e["image"],
                   "description": "", "ingredients": e["ingredients"],
                   "source": "manual", "status": "enriched"}
            rec.update(rt.tag(rec))   # veggies, protein, is_pasta, dish_type
            corpus.append(rec); have.add(norm(url)); added += 1
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
