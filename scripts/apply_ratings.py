#!/usr/bin/env python3
"""Merge a ratings.json (from the make_rating_page.py page) into the recipe corpus.

  python3 apply_ratings.py --ratings ratings.json --corpus recipes_tagged.json

ratings.json maps each recipe's key -> "up" | "down" | "none". This sets
recipe["rating"] = "up"/"down"; "none" (or a missing key) clears any existing rating.
Re-runnable and idempotent — run it again after each rating pass, then re-upload the
corpus to S3 (no redeploy needed). Pass --dry-run to preview without writing.
"""
import argparse
import json


def key_of(rec):
    """Stable per-recipe key (matches make_rating_page.py): recipe_url, else title."""
    url = (rec.get("recipe_url") or "").strip()
    return url or "title::" + (rec.get("title") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--corpus", default="recipes_tagged.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    ratings = json.load(open(args.ratings))

    up = down = cleared = unchanged = 0
    matched = set()
    for rec in corpus:
        k = key_of(rec)
        if k not in ratings:
            continue
        matched.add(k)
        want = ratings[k] if ratings[k] in ("up", "down") else None
        have = rec.get("rating")
        if want == have:
            unchanged += 1
            continue
        if want is None:
            rec.pop("rating", None)
            cleared += 1
        else:
            rec["rating"] = want
            up += want == "up"
            down += want == "down"

    unknown = [k for k in ratings if k not in matched]
    total_up = sum(1 for r in corpus if r.get("rating") == "up")
    total_down = sum(1 for r in corpus if r.get("rating") == "down")

    print(f"applied: +{up} 👍, +{down} 👎, {cleared} cleared, {unchanged} unchanged")
    print(f"corpus now: {total_up} 👍 · {total_down} 👎 · "
          f"{len(corpus) - total_up - total_down} unrated · {len(corpus)} total")
    if unknown:
        print(f"warning: {len(unknown)} rating keys matched no recipe (skipped), e.g. {unknown[:3]}")

    if args.dry_run:
        print("dry run — corpus not written")
    else:
        json.dump(corpus, open(args.corpus, "w"), indent=2)
        print(f"wrote {args.corpus}")


if __name__ == "__main__":
    main()
