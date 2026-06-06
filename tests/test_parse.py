"""Tests for the CSA email / share-line parser."""
from planner import parse


def test_all_20_weeks_normalize_as_expected(share_fixtures):
    """Every recorded week's share line normalizes to its expected canonical veggies."""
    for week, data in share_fixtures.items():
        got = parse.parse_veggies(data["share_line"])
        assert got == data["veggies"], f"{week}: {got} != {data['veggies']}"


def test_oxford_and_and_comma_split():
    items = parse.split_items("a, b, c, and d")
    assert items == ["a", "b", "c", "d"]


def test_bare_and_split():
    assert parse.split_items("basil and heirloom tomatoes") == ["basil", "heirloom tomatoes"]


def test_or_treated_as_either():
    # "heirloom OR cherry tomatoes" -> tomato (both sides normalize the same)
    assert parse.normalize_item("heirloom OR cherry tomatoes") == {"tomato"}


def test_parenthetical_stripped_and_dropped():
    # gem lettuces (mini romaines) is a salad green -> dropped entirely
    assert parse.normalize_item("gem lettuces (mini romaines)") == set()


def test_skip_set_dropped():
    for item in ("lettuce salad mix", "spicy salad mix", "microgreen mix",
                 "baby arugula", "basil", "Italian parsley", "tatsoi"):
        assert parse.normalize_item(item) == set(), item


def test_descriptors_normalize_to_canonical():
    assert parse.normalize_item("lacinato kale") == {"kale"}
    assert parse.normalize_item("baby bok choi") == {"bok choy"}
    assert parse.normalize_item("watermelon radishes") == {"radish"}
    assert parse.normalize_item("sweet salad turnips") == {"turnip"}
    assert parse.normalize_item("sugar snap peas") == {"pea"}
    assert parse.normalize_item("fresh yellow onions") == {"onion"}


def test_extract_share_line_strips_footnote_period():
    text = "Howdy!\nShare contents: carrots, radishes, and kale.*\nSee you Saturday."
    assert parse.extract_share_line(text) == "carrots, radishes, and kale"


def test_missing_share_line_returns_none():
    assert parse.extract_share_line("no share info here") is None


def test_parse_email_raises_noshareline_for_non_csa():
    import pytest
    raw = b"Subject: test\r\nFrom: a@b.com\r\nContent-Type: text/plain\r\n\r\njust a test email\r\n"
    with pytest.raises(parse.NoShareLine):
        parse.parse_email(raw)
    assert issubclass(parse.NoShareLine, ValueError)  # stays catchable as ValueError
