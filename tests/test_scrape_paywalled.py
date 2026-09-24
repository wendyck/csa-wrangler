"""Tests for the Seattle Times host check in scripts/scrape_paywalled.py (CodeQL alert #1)."""
import importlib
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# A Seattle Times-style page: no JSON-LD, ingredients in a <ul> after an INGREDIENTS heading.
ST_HTML = """<html><head><meta property="og:title" content="Kale Salad"></head><body>
<h3>INGREDIENTS</h3><ul><li>1 bunch kale</li><li>2 tbsp olive oil</li></ul>
</body></html>"""


@pytest.fixture
def scraper(monkeypatch):
    """Import the script with requests/recipe_scrapers stubbed (neither is a test dependency)."""
    def scrape_html(html, org_url):
        raise ValueError("no recipe schema")  # force the fallback path

    monkeypatch.setitem(sys.modules, "requests", types.ModuleType("requests"))
    monkeypatch.setitem(sys.modules, "recipe_scrapers",
                        types.SimpleNamespace(scrape_html=scrape_html))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delitem(sys.modules, "scrape_paywalled", raising=False)
    return importlib.import_module("scrape_paywalled")


class FakeSession:
    def get(self, url, timeout):
        return types.SimpleNamespace(text=ST_HTML, raise_for_status=lambda: None)


@pytest.mark.parametrize("url", [
    "https://www.seattletimes.com/life/food/kale-salad/",
    "https://seattletimes.com/life/food/kale-salad/",
])
def test_seattletimes_host_uses_st_fallback(scraper, url):
    got = scraper.enrich(FakeSession(), url)
    assert got["title"] == "Kale Salad"
    assert got["ingredients"] == ["1 bunch kale", "2 tbsp olive oil"]


@pytest.mark.parametrize("url", [
    "https://evil.example/seattletimes.com",           # host only appears in the path
    "https://notseattletimes.com/life/food/",          # lookalike suffix
    "https://seattletimes.com.evil.example/recipe/",   # host used as a prefix
])
def test_lookalike_urls_do_not_use_st_fallback(scraper, url):
    with pytest.raises(ValueError, match="no ingredients"):
        scraper.enrich(FakeSession(), url)
