#!/usr/bin/env python3
"""
Import hand-photographed cookbook recipes into the corpus.

Each cookbook is a folder of photos under --photos-dir (folder name = cookbook name).
Per recipe there's a page photo (`<name> recipe.heic`, required — OCR'd for ingredients)
and optionally a dish photo (`<name> photo.heic`, embedded inline in the plan email).

  pip install -r scripts/requirements.txt
  export ANTHROPIC_API_KEY=...                         # for the vision OCR
  python3 scripts/import_cookbook.py --photos-dir ~/cookbook-photos --bucket <s3-bucket>
  python3 scripts/import_cookbook.py --dry-run         # print extractions, touch nothing

With --bucket, the live corpus is pulled from S3 first (so we append, never clobber); page
and dish JPEGs are uploaded under cookbook-photos/, and the updated corpus is pushed back.
Re-runnable: a cookbook+recipe already in the corpus is skipped. HEIC is converted to JPEG
with macOS `sips` (no extra dependency). Failures -> import_failures.csv.
"""
import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
import tempfile

from anthropic import Anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recipe_tagging as rt

CORPUS_KEY = "corpus/recipes_tagged.json"
PHOTO_PREFIX = "cookbook-photos"
PAGE_SUFFIX, DISH_SUFFIX = " recipe", " photo"
DEFAULT_MODEL = "claude-opus-4-8"


def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


_ARTICLE = re.compile(r"^(?:the|a|an)\s+")


def pair_key(base):
    """Group key for matching a recipe's page + dish photo. Tolerates whitespace and a
    leading article so 'the aussie chop salad' pairs with 'aussie chop salad'."""
    return _ARTICLE.sub("", re.sub(r"\s+", " ", base.strip().lower()))


def to_jpeg(path):
    """Bytes of `path` as JPEG (converts HEIC and normalizes anything else) via macOS sips."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        out = tmp.name
    try:
        subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", out],
                       check=True, capture_output=True)
        return open(out, "rb").read()
    finally:
        os.unlink(out)


def find_recipes(photos_dir):
    """Yield (cookbook, display_name, page_path, dish_path_or_None) for each recipe folder."""
    for cookbook in sorted(os.listdir(photos_dir)):
        folder = os.path.join(photos_dir, cookbook)
        if not os.path.isdir(folder):
            continue
        groups = {}   # key -> {"display":, "recipe":, "photo":}
        for fname in os.listdir(folder):
            stem, ext = os.path.splitext(fname)
            if ext.lower() not in (".heic", ".jpg", ".jpeg", ".png"):
                continue
            low = stem.lower()
            if low.endswith(PAGE_SUFFIX):
                kind, base = "recipe", stem[:-len(PAGE_SUFFIX)]
            elif low.endswith(DISH_SUFFIX):
                kind, base = "photo", stem[:-len(DISH_SUFFIX)]
            else:
                print(f"  ?? skip unrecognized file: {cookbook}/{fname}")
                continue
            g = groups.setdefault(pair_key(base), {"display": base.strip()})
            g[kind] = os.path.join(folder, fname)
            if kind == "recipe":
                g["display"] = base.strip()   # prefer the page's name as the OCR hint
        for g in groups.values():
            if "recipe" not in g:
                print(f"  ?? skip {cookbook}/{g['display']}: no page photo (.* recipe.*)")
                continue
            yield cookbook, g["display"], g["recipe"], g.get("photo")


def _json_payload(text):
    """The JSON object in a model reply, tolerating ```json fences or surrounding prose."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model reply: {text[:120]!r}")
    return json.loads(m.group(0))


def extract(client, model, page_jpeg, name_hint):
    """OCR the page photo into {"name", "ingredients"} via Claude vision."""
    b64 = base64.standard_b64encode(page_jpeg).decode()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": (
                "This is a photo of a cookbook recipe page. Reply with ONLY a JSON object: "
                '{"name": <recipe title>, "ingredients": [<one entry per ingredient line, '
                "exactly as written, including quantities>]}. "
                f"The filename suggests the title is '{name_hint}' — use that if the page "
                "title is unclear. No prose, no markdown fences.")},
        ]}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _json_payload(text)
    name = (data.get("name") or name_hint).strip()
    ingredients = [str(x).strip() for x in (data.get("ingredients") or []) if str(x).strip()]
    if not ingredients:
        raise ValueError("no ingredients extracted")
    return name, ingredients


def load_corpus(s3, bucket, path):
    """Live corpus: pulled from S3 when --bucket is set (cached to `path`), else local/empty."""
    if s3 and bucket:
        try:
            data = json.loads(s3.get_object(Bucket=bucket, Key=CORPUS_KEY)["Body"].read())
            json.dump(data, open(path, "w"), indent=2)
            return data
        except s3.exceptions.NoSuchKey:
            return []
    return json.load(open(path)) if os.path.exists(path) else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos-dir", default=os.path.expanduser("~/cookbook-photos"))
    ap.add_argument("--corpus", default="recipes_tagged.json")
    ap.add_argument("--bucket", help="S3 bucket; required unless --dry-run")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true",
                    help="print extracted name + ingredients; no S3/corpus writes")
    args = ap.parse_args()

    if not args.dry_run and not args.bucket:
        ap.error("--bucket is required unless --dry-run")

    client = Anthropic()
    s3 = None
    if not args.dry_run:
        import boto3
        s3 = boto3.client("s3")

    corpus = load_corpus(s3, args.bucket, args.corpus)
    have = {(r.get("cookbook"), r.get("title"))
            for r in corpus if r.get("source") == "cookbook"}

    added, skipped, fails = 0, 0, []
    for cookbook, display, page_path, dish_path in find_recipes(args.photos_dir):
        try:
            page_jpeg = to_jpeg(page_path)
            name, ingredients = extract(client, args.model, page_jpeg, display)
            if (cookbook, name) in have:
                skipped += 1
                print(f"  skip (already in corpus) {cookbook} / {name}")
                continue

            cb_slug, name_slug = slug(cookbook), slug(name)
            page_key = f"{PHOTO_PREFIX}/{cb_slug}/{name_slug}-page.jpg"
            rec = {"title": name, "source": "cookbook", "cookbook": cookbook,
                   "recipe_url": "", "pin_url": "",
                   "recipe_image": None, "pinterest_image": None,
                   "page_s3_key": page_key, "description": "", "status": "enriched",
                   "ingredients": ingredients}
            dish_jpeg = to_jpeg(dish_path) if dish_path else None
            if dish_jpeg is not None:
                rec["photo_s3_key"] = f"{PHOTO_PREFIX}/{cb_slug}/{name_slug}-food.jpg"
            rec.update(rt.tag(rec))

            if args.dry_run:
                print(f"  [DRY] {cookbook} / {name}  [{rec['protein']}] "
                      f"{', '.join(rec['veggies']) or 'no veg'}  "
                      f"({len(ingredients)} ingredients, "
                      f"{'with' if dish_jpeg else 'no'} dish photo)")
                print(json.dumps({"ingredients": ingredients}, indent=2))
                continue

            s3.put_object(Bucket=args.bucket, Key=page_key, Body=page_jpeg,
                          ContentType="image/jpeg")
            if dish_jpeg is not None:
                s3.put_object(Bucket=args.bucket, Key=rec["photo_s3_key"], Body=dish_jpeg,
                              ContentType="image/jpeg")
            corpus.append(rec)
            have.add((cookbook, name))
            added += 1
            json.dump(corpus, open(args.corpus, "w"), indent=2)   # checkpoint
            print(f"  OK {cookbook} / {name}  [{rec['protein']}] "
                  f"{', '.join(rec['veggies']) or 'no veg'}")
        except Exception as ex:                                   # noqa: BLE001
            fails.append({"cookbook": cookbook, "recipe": display, "error": str(ex)})
            print(f"  !! {cookbook} / {display} -> {ex}")

    if not args.dry_run and added:
        s3.put_object(Bucket=args.bucket, Key=CORPUS_KEY,
                      Body=json.dumps(corpus, indent=2).encode(),
                      ContentType="application/json")
        print(f"uploaded corpus -> s3://{args.bucket}/{CORPUS_KEY}")
    if fails:
        with open("import_failures.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["cookbook", "recipe", "error"])
            w.writeheader()
            w.writerows(fails)
    print(f"\nDone. added={added} skipped={skipped} failed={len(fails)} | "
          f"corpus now {len(corpus)} recipes")


if __name__ == "__main__":
    main()
