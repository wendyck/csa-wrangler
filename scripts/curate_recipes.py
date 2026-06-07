#!/usr/bin/env python3
"""Apply curation edits to the recipe corpus: reclassify fields or remove recipes by URL.

  python3 curate_recipes.py --edits recipe_edits.json --corpus recipes_tagged.json [--dry-run]

recipe_edits.json is a JSON list of edits, each keyed by "url" plus either a removal or
fields to set:

  {"url": "https://…", "remove": true}                  # drop the recipe from the corpus
  {"url": "https://…", "dish_type": "side"}              # set one or more fields
  {"url": "https://…", "protein": "pork"}

Settable fields: dish_type, protein, is_pasta, rating, title. URLs match regardless of
query string or trailing slash. Re-runnable and idempotent — unmatched URLs are reported
(not an error), so you can keep appending edits over time. Pass --dry-run to preview.
"""
import argparse
import json
from urllib.parse import urlsplit

SETTABLE = {"dish_type", "protein", "is_pasta", "rating", "title"}


def norm(u):
    """Normalize a recipe URL for matching: ignore scheme (http/https), a leading 'www.',
    query string, fragment, trailing slash, and case."""
    s = urlsplit((u or "").strip().lower())
    host = s.netloc[4:] if s.netloc.startswith("www.") else s.netloc
    return host + s.path.rstrip("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits", required=True)
    ap.add_argument("--corpus", default="recipes_tagged.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    edits = json.load(open(args.edits))

    by_url = {}
    for r in corpus:
        by_url.setdefault(norm(r.get("recipe_url", "")), []).append(r)

    remove_keys, missing = set(), []
    removed = already_removed = reclassified = unchanged = 0
    for e in edits:
        k = norm(e["url"])
        recs = by_url.get(k)
        if not recs:
            # A remove whose recipe is already gone is idempotent, not an error; only a
            # set-edit with no match is a possible typo worth reporting.
            already_removed += bool(e.get("remove"))
            if not e.get("remove"):
                missing.append(e["url"])
            continue
        if e.get("remove"):
            remove_keys.add(k)
            removed += len(recs)
            continue
        fields = {f: e[f] for f in SETTABLE if f in e}
        for r in recs:
            changed = False
            for f, v in fields.items():
                if r.get(f) != v:
                    r[f], changed = v, True
            reclassified += changed
            unchanged += not changed

    new_corpus = [r for r in corpus if norm(r.get("recipe_url", "")) not in remove_keys]

    print(f"edits: {len(edits)} | removed {removed} (already-gone {already_removed}), "
          f"reclassified {reclassified}, already-correct {unchanged}, unmatched {len(missing)}")
    print(f"corpus: {len(corpus)} -> {len(new_corpus)} recipes")
    if missing:
        print("UNMATCHED urls (no recipe found — check the URL):")
        for u in missing:
            print("  -", u)

    if args.dry_run:
        print("dry run — corpus not written")
    else:
        json.dump(new_corpus, open(args.corpus, "w"), indent=2)
        print(f"wrote {args.corpus}")


if __name__ == "__main__":
    main()
