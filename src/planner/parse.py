"""Parse a forwarded CSA email into this week's canonical veggies.

The CSA email always contains a line of the form

    Share contents: lettuce salad mix, microgreen mix, carrots, rainbow chard, and cherry tomatoes.*

We extract the text after "Share contents:" up to the terminating period, split it
into items, drop the salad/lettuce/microgreen/arugula skip-set, and normalize the rest
to the same canonical vocabulary the recipe corpus was tagged with (recipe_tagging.VEG),
so CSA names and recipe ingredients share one vocabulary.
"""
import email
import re
from email import policy

from . import recipe_tagging

# Items that are never planned around (leafy salad fillers, herbs/garnish that don't
# drive a dinner). Anything here is dropped before veggie normalization. Most of these
# also simply fail to match a canonical VEG pattern, but listing them keeps intent clear.
SKIP_SUBSTRINGS = (
    "salad mix", "lettuce", "microgreen", "arugula", "romaine", "mesclun",
    "basil", "parsley", "cilantro", "dill", "mint", "tatsoi",
)

_SHARE_RE = re.compile(r"share\s+contents?\s*:\s*(.*?)(?:\.\s|\.\*|\.$|\n\n)", re.I | re.S)
_PAREN_RE = re.compile(r"\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")


def _body_text(msg):
    """Best-effort plain-text body: prefer text/plain, else strip tags from text/html."""
    plain, html = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain":
            try:
                plain.append(part.get_content())
            except Exception:
                pass
        elif ctype == "text/html":
            try:
                html.append(part.get_content())
            except Exception:
                pass
    if plain:
        return "\n".join(plain)
    if html:
        return _TAG_RE.sub(" ", "\n".join(html))
    return ""


def extract_share_line(text):
    """Return the raw 'Share contents:' payload (without the trailing period), or None."""
    m = _SHARE_RE.search(text)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".")


def split_items(share_line):
    """Split the share line into individual item strings on commas and 'and'."""
    # Normalize the Oxford-comma 'and' and any bare ' and ' to a comma, then split.
    s = re.sub(r"\s*,?\s+and\s+", ",", share_line, flags=re.I)
    items = [i.strip() for i in s.split(",")]
    return [i for i in items if i]


def normalize_item(item):
    """Map one CSA item string to the set of canonical veggies it contains.

    Handles 'X OR Y' (either satisfies), strips parentheticals and footnote markers.
    Returns a set (usually 0 or 1 canonical veggie).
    """
    cleaned = _PAREN_RE.sub(" ", item).replace("*", " ").lower()
    if any(skip in cleaned for skip in SKIP_SUBSTRINGS):
        return set()
    return {canon for canon, pat in recipe_tagging._VEG_C.items() if pat.search(cleaned)}


def parse_veggies(share_line):
    """Full share line -> sorted list of canonical veggies for the week."""
    veggies = set()
    for item in split_items(share_line):
        veggies |= normalize_item(item)
    return sorted(veggies)


def parse_email(raw_bytes):
    """Parse raw MIME bytes into a dict describing the week.

    Returns: {veggies, raw_items, share_line, week_label, date, subject}.
    Raises ValueError if no 'Share contents:' line is found.
    """
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    text = _body_text(msg)
    share_line = extract_share_line(text)
    if not share_line:
        raise ValueError("no 'Share contents:' line found in email body")

    subject = (msg.get("subject") or "").strip()
    date = msg.get("date")
    week_label = subject or "CSA Share"

    return {
        "veggies": parse_veggies(share_line),
        "raw_items": split_items(share_line),
        "share_line": share_line,
        "week_label": week_label,
        "subject": subject,
        "date": date,
    }
