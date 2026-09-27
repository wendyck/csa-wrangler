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

class NoShareLine(ValueError):
    """Email has no 'Share contents:' line — i.e. it isn't a CSA share email (or the
    CSA changed its format). Handled as a diagnostic, not an unexpected error."""


# Opening delimiter excluded from the inner class and repetition bounded, so an
# unterminated "(" or "<" can't make these rescan the rest of the input (O(n^2)).
_PAREN_RE = re.compile(r"\([^()]{0,200}\)")
_TAG_RE = re.compile(r"<[^<>]{0,200}>")

# Input bounds — a real share email is a few KB.
MAX_PARTS = 50
MAX_BODY_CHARS = 512_000
MAX_SHARE_LINE = 4_000
MAX_ITEM_CHARS = 200
MAX_SHARE_LABELS = 5   # "Share contents:" occurrences tried before giving up

# Label and payload are matched separately so each label scans at most MAX_SHARE_LINE
# chars for the terminating period, instead of the whole rest of the body.
_SHARE_LABEL_RE = re.compile(r"share\s+contents?\s*:\s*", re.I)
_SHARE_BODY_RE = re.compile(r"(.{0,%d}?)(?:\.\s|\.\*|\.$|\n\n)" % MAX_SHARE_LINE, re.S)


def _body_text(msg):
    """Best-effort plain-text body: prefer text/plain, else strip tags from text/html."""
    plain, html = [], []
    total = 0
    for n, part in enumerate(msg.walk()):
        if n >= MAX_PARTS or total >= MAX_BODY_CHARS:
            break
        ctype = part.get_content_type()
        if ctype == "text/plain":
            try:
                plain.append(part.get_content()[:MAX_BODY_CHARS - total])
                total += len(plain[-1])
            except Exception:
                pass
        elif ctype == "text/html":
            try:
                html.append(part.get_content()[:MAX_BODY_CHARS - total])
                total += len(html[-1])
            except Exception:
                pass
    if plain:
        return "\n".join(plain)
    if html:
        return _TAG_RE.sub(" ", "\n".join(html))
    return ""


def extract_share_line(text):
    """Return the raw 'Share contents:' payload (without the trailing period), or None."""
    for n, label in enumerate(_SHARE_LABEL_RE.finditer(text)):
        if n >= MAX_SHARE_LABELS:
            break
        m = _SHARE_BODY_RE.match(text, label.end())
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".")
    return None


def split_items(share_line):
    """Split the share line into individual item strings on commas and 'and'."""
    # Normalize the Oxford-comma 'and' and any bare ' and ' to a comma, then split.
    s = re.sub(r"\s*,?\s+and\s+", ",", share_line, flags=re.I)
    items = [i.strip()[:MAX_ITEM_CHARS] for i in s.split(",")]
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
        raise NoShareLine("no 'Share contents:' line found in email body")

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
