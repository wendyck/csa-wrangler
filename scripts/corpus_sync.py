#!/usr/bin/env python3
"""
Pull the recipe corpus from S3 for local editing, and push it back without clobbering
changes made in the meantime (e.g. recipes saved from the browser extension).

  python scripts/corpus_sync.py pull [--bucket B] [--corpus recipes_tagged.json]
  ... edit with add_recipes.py / apply_ratings.py / curate_recipes.py ...
  python scripts/corpus_sync.py push [--bucket B] [--corpus recipes_tagged.json]

S3 is the source of truth. `pull` saves the object's ETag next to the corpus
(.recipes_tagged.etag); `push` uploads with If-Match on that ETag, so it's refused if S3
changed since your pull. Then pull again and redo your edits. With no ETag file, push only
succeeds if S3 has no corpus yet (the first upload after deploy). Bucket versioning keeps
every overwritten copy for 90 days if you need to roll back.

Bucket comes from --bucket or $CSA_BUCKET. Requires boto3 (scripts/requirements.txt).
"""
import argparse
import json
import os
import sys
from pathlib import Path

CORPUS_KEY = "corpus/recipes_tagged.json"


def _s3():
    import boto3
    return boto3.client("s3")


def etag_path(corpus):
    p = Path(corpus)
    return p.with_name(f".{p.stem}.etag")


def pull(s3, bucket, corpus):
    obj = s3.get_object(Bucket=bucket, Key=CORPUS_KEY)
    body = obj["Body"].read()
    n = len(json.loads(body))
    Path(corpus).write_bytes(body)
    etag_path(corpus).write_text(obj["ETag"])
    print(f"pulled s3://{bucket}/{CORPUS_KEY} -> {corpus} ({n} recipes)")


def push(s3, bucket, corpus):
    from botocore.exceptions import ClientError
    body = Path(corpus).read_bytes()
    data = json.loads(body)                     # never upload a file the Lambda can't parse
    if not isinstance(data, list):
        sys.exit(f"{corpus} isn't a JSON list of recipes — not pushing")
    tag = etag_path(corpus)
    cond = {"IfMatch": tag.read_text().strip()} if tag.exists() else {"IfNoneMatch": "*"}
    try:
        resp = s3.put_object(Bucket=bucket, Key=CORPUS_KEY, Body=body,
                             ContentType="application/json", **cond)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed",
                                                             "ConditionalRequestConflict"):
            raise
        if "IfMatch" in cond:
            sys.exit("S3 changed since your last pull — run pull and redo your edits")
        sys.exit(f"S3 already has a corpus and there's no {tag.name} — run pull first")
    tag.write_text(resp["ETag"])
    print(f"pushed {corpus} -> s3://{bucket}/{CORPUS_KEY} ({len(data)} recipes)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("command", choices=["pull", "push"])
    ap.add_argument("--bucket", default=os.environ.get("CSA_BUCKET"))
    ap.add_argument("--corpus", default="recipes_tagged.json")
    args = ap.parse_args(argv)
    if not args.bucket:
        ap.error("set --bucket or $CSA_BUCKET")
    (pull if args.command == "pull" else push)(_s3(), args.bucket, args.corpus)


if __name__ == "__main__":
    main()
