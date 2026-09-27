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


# ---- issue #32: bounded input, no quadratic backtracking ----

import time

import pytest


def _mime(body, ctype="text/plain"):
    return (f"Subject: CSA\r\nContent-Type: {ctype}; charset=utf-8\r\n\r\n".encode()
            + body.encode())


@pytest.mark.parametrize("body,ctype", [
    ("Share contents: " + "(" * 1_000_000 + ".\n", "text/plain"),   # _PAREN_RE
    ("Share contents: kale.\n" + "<" * 1_000_000, "text/html"),     # _TAG_RE
    ("Share contents: " + " " * 1_000_000 + "and kale.\n", "text/plain"),  # split_items
    ("share contents:" * 70_000, "text/plain"),                      # _SHARE_RE, no terminator
], ids=["paren", "tag", "and-split", "share-label"])
def test_parse_email_on_1mb_adversarial_body_is_fast(body, ctype):
    start = time.perf_counter()
    try:
        parse.parse_email(_mime(body, ctype))
    except parse.NoShareLine:
        pass
    assert time.perf_counter() - start < 1.0


def test_items_and_share_line_are_capped():
    info = parse.parse_email(_mime("Share contents: kale, " + "x" * 3_990 + ".\n"))
    assert len(info["share_line"]) <= parse.MAX_SHARE_LINE
    assert all(len(i) <= parse.MAX_ITEM_CHARS for i in info["raw_items"])


def test_mime_part_count_is_capped():
    parts = "".join(f"--b\r\nContent-Type: text/plain\r\n\r\nfiller {i}\r\n" for i in range(500))
    raw = (b"Subject: CSA\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n"
           + parts.encode() + b"--b\r\nContent-Type: text/plain\r\n\r\nShare contents: kale.\n\r\n--b--\r\n")
    with pytest.raises(parse.NoShareLine):   # share line sits past MAX_PARTS, so it's never read
        parse.parse_email(raw)


def test_real_shares_still_parse_with_parentheticals():
    assert parse.normalize_item("kale (lacinato or curly)") == parse.normalize_item("kale")
