"""Shared test fixtures. Makes `import planner` work and locates the corpus."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The corpus is not committed (it lives in the parent Recipes/ dir and ships to S3).
# Tests that need it skip cleanly when it's absent.
_CORPUS_CANDIDATES = [
    os.environ.get("CSA_CORPUS"),
    ROOT.parent / "recipes_tagged.json",
]


@pytest.fixture(scope="session")
def share_fixtures():
    with open(FIXTURES / "share_contents.json") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def corpus():
    for cand in _CORPUS_CANDIDATES:
        if cand and Path(cand).exists():
            with open(cand) as f:
                return json.load(f)
    pytest.skip("recipes_tagged.json not available (set CSA_CORPUS)")
