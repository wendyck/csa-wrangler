"""Runtime configuration.

Non-secret config lives in SSM Parameter Store under a prefix (ARCHITECTURE §8); the
resource names (bucket, table) come from Lambda environment variables set by the SAM
template. Values are read once per cold start and cached. Env vars override SSM, which
makes local testing trivial.
"""
import functools
import logging
import os

log = logging.getLogger(__name__)

# Resource wiring (from the SAM template's Environment).
BUCKET = os.environ.get("BUCKET", "")
TABLE = os.environ.get("TABLE", "")
SECRET_ARN = os.environ.get("SECRET_ARN", "")
SSM_PREFIX = os.environ.get("SSM_PREFIX", "/csa-wrangler")
RAW_PREFIX = os.environ.get("RAW_PREFIX", "raw-emails/")
ARCHIVE_PREFIX = os.environ.get("ARCHIVE_PREFIX", "plan-archive/")

# SSM-backed config and their defaults (ARCHITECTURE §8).
_DEFAULTS = {
    "RECIPIENT_EMAIL": "",
    "FROM_EMAIL": "",
    "NIGHTS_PER_WEEK": "6",
    "NO_REPEAT_WEEKS": "3",
    "CORPUS_S3_KEY": "corpus/recipes_tagged.json",
}


@functools.lru_cache(maxsize=1)
def _ssm_values():
    """Fetch all params under SSM_PREFIX in one call. Empty dict if unavailable."""
    try:
        import boto3
        ssm = boto3.client("ssm")
        out = {}
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=SSM_PREFIX, Recursive=True, WithDecryption=True):
            for p in page["Parameters"]:
                out[p["Name"].rsplit("/", 1)[-1]] = p["Value"]
        return out
    except Exception:
        # Don't crash config reads, but make the failure visible — an empty result
        # silently degrades to blank defaults (e.g. an empty From address).
        log.exception("failed to read SSM parameters under %s", SSM_PREFIX)
        return {}


def get(name):
    """Resolve a config value: env var > SSM > default."""
    if name in os.environ:
        return os.environ[name]
    return _ssm_values().get(name, _DEFAULTS.get(name, ""))


def nights_per_week():
    return int(get("NIGHTS_PER_WEEK") or 6)


def no_repeat_weeks():
    return int(get("NO_REPEAT_WEEKS") or 3)
