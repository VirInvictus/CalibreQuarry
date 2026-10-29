#!/usr/bin/env python3
"""screen_duplicate: screen loose downloads against the library (and each other).

Phase 1 § 3's duplicate screen as a tool. Reads each candidate file's embedded
title/authors/ISBN with `ebook-meta` (Calibre's reader, read-only invocation)
and matches against the library by exact ISBN first, then by ASIN value
(the same class of edition key, matched across identifier types), then by
normalized title + first author via cquarry's search engine. The filename
appears only as a
display hint: AA/z-library filenames lie ("...Volume 1..." held Volume 3;
titles arrive word-scrambled), so the embedded metadata decides.

Matching is deliberately two-stage. An exact-ISBN hit is conclusive. The
title/author path uses cquarry's tight `=` matches and then classifies the
NORMALIZED title pair in Python: case/accent fold, strip the leading article,
and scrub subtitles (the segment before a colon) plus standalone
volume/part/book N tokens — so "Capital: Volume I" screens against
"Capital: A Critique of Political Economy" where raw equality would not.
The 2026-09-21 waves added three verdict classes to that equality:

- differing DECLARED volume annotations (token word + arabic or valid roman
  numeral on both sides) are distinct works of one series root, surfaced
  within the batch as a multi-volume set (`batch_volumes`; the 2026-09-16
  Moral Letters vols and the "Monster Vault" pair), never a refusal;
- differing real subtitles over one shared base are distinct works, full
  stop (the "Introduction to Computer Organization: ARM" vs x86-64
  siblings);
- when one full title equals the other's pre-colon base or post-colon
  subtitle, the pair is a `related` candidate for human judgment — a
  series/store prefix can MASK a true duplicate ("Mothership: Wages of
  Sin" vs the bare "Wages of Sin" download) exactly as easily as it can
  join two distinct products ("Arcana Unleashed" vs "...: Deadfall"), so
  the screen surfaces it and never auto-refuses;
- when NEITHER file carries embedded metadata (ebook-meta falls back to
  the raw filename stem as the title and Author(s) "Unknown"), the stem
  is parsed instead: the first parenthesized group is the author segment,
  the text before it the title, and the pair is judged on the parsed
  titles plus an ORDER-FREE author token set ("Stroustrup, Bjarne" ==
  "Bjarne Stroustrup"). The 2026-10-10 Stroustrup pair (issue #5) passed
  silently because the author brackets sat inside the compared stems in
  different orders; one side with real metadata never takes this path.

The 3.63.7 imprint batch, issue #17's edition batch, and issue #40's
parenthetical batch all widen what the judge treats as annotation noise
rather than work identity: the curated imprint tags ("(Oxford World's
Classics)"), the ordinal edition tags (", 3rd Edition", ": 2nd Edition",
"4th ed."), and a TRAILING parenthetical qualifier ("(A Court of Thorns
and Roses)", a translator credit) strip from every compared string, while
the volume annotations the series rules read stay on the RAW title and two
DIFFERENT trailing qualifiers stay work identity (distinct), never
stripped into a match.

Issue #28 (3.68.0) gates every identifier binding by SHAPE: the
2026-10-15 wave refused Poem of the Cid as a duplicate of Return to
Nevèrÿon because both amateur conversions carried the SAME junk
dc:identifier (their shared template's URL, typed isbn by the
converter). An identical value now binds only when it is ISBN-shaped
(10/13 ISBN characters; checksum not required) or ASIN-shaped (B + 9
alphanumerics; an ISBN-shaped value under the asin type still binds,
the pre-2001 ASINs are ISBN-10s). The same gate covers the
within-batch intersection and the library keypair searches. And when
BOTH sides of a title+author duplicate carry VALID (checksum-passing)
but DIFFERENT ISBNs, that is evidence AGAINST a duplicate: no refusal
(the 2026-10-15 seven-volume stamp collapse). Issue #30 makes every
binding carry its evidence: hit descriptors carry `basis`, within-batch
refusals record a `batch_duplicate_basis` map (isbn/asin value, or the
title+author verdict with the ISBN state named), which is the suffix
the duplicate decision's detail quotes.

Issue #33 (3.71.0) closes the same-edition-different-notation miss: the
2026-10-21 wave's A Pattern Language rip (stamped `Christopher
Alexander`, ISBN-13 9780195019193) sat next to the held #1982
(`Christopher W. Alexander`, ISBN-10 0195019199) and both doors passed
it. ISBNs now compare through their 13-form: a 10-form converts through
the 978 Bookland prefix with a recomputed check digit, the library
keypair search also tries the cross notation, and a 10/13 pair binds as
the ONE edition it is instead of reading as two valid different ISBNs
(the evidence-against rule above only fires when the canonical forms
differ). The first-author leg compares token sets with the single-letter
tokens dropped, so a middle initial present on one side and absent on
the other no longer splits one author in two; the library door supplies
its candidates through a surname search and re-checks the author tokens
itself, because the engine's exact `author:=` cannot see past the
initial either.

Report-only: nothing is deleted or moved here. Removing a library duplicate
needs Brandon's explicit OK in phase 3; loose-file removal is his too.

Exit codes: 0 = no candidates, 1 = duplicate or related candidates found,
2 = setup error.
"""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from version_guard import announce
except ImportError:  # a lone deployed copy: no siblings beside it

    def announce(stream=None):
        pass


EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}

# Identifier types whose value is an edition ASIN. z-lib rips routinely
# strip the ISBN and keep the ASIN (issue #13's live miss), so the ASIN
# is matched against library identifier VALUES exactly, any type: a
# file-side `asin` meets a library-side `mobi-asin`, and the uuid/calibre
# values the writer re-adds can never collide with an ASIN-shaped string.
_ASIN_TYPES = frozenset({"asin", "mobi-asin"})

# Identifier shapes a binding may use (issue #28): the 2026-10-15 wave
# bound Poem of the Cid to Return to Nevèrÿon through the SAME junk
# dc:identifier — an amateur-conversion template's URL, typed isbn by the
# converter — because any identical identifier VALUE was a same-book
# signal. Only real identifier SHAPES bind now: a value is ISBN-shaped
# when it is 10 or 13 ISBN characters once everything outside [0-9Xx]
# strips (the house normalization; checksum NOT required — a same-typo
# pair out of one rip's source is still evidence), and ASIN-shaped when
# it is B + 9 alphanumerics.
_ISBN_SHAPE_RE = re.compile(r"^(?:[0-9]{9}[0-9Xx]|[0-9]{13})$")
_ASIN_SHAPE_RE = re.compile(r"^[Bb][A-Za-z0-9]{9}$")


def _isbn_digits(value: str) -> str:
    """The value reduced to its ISBN characters, uppercased (the house
    normalization: every non [0-9Xx] byte drops, so dashed, spaced, and
    dotted producer spellings all compare equal)."""
    return re.sub(r"[^0-9Xx]", "", (value or "").upper())


def _isbn_shaped(value: str) -> bool:
    """True for an ISBN-10/13-shaped value (producer punctuation
    tolerated, no checksum). The gate every ISBN binding passes; the
    junk-URL class fails the shape alone."""
    return bool(_ISBN_SHAPE_RE.match(_isbn_digits(value)))


def _isbn_valid(value: str) -> bool:
    """True for an ISBN-10/13 whose check digit passes (the
    validate_metadata rule, both lengths). The different-ISBN evidence
    rule needs VALID, not merely shaped: two VALID but different ISBNs
    are evidence AGAINST a duplicate, a valid-vs-junk difference is
    not. The digits are normalized first, and a non-shaped reduction is
    simply not valid — the arithmetic never sees an X outside the check
    position (issue #33's review closed that latent crash)."""
    digits = _isbn_digits(value)
    if not _ISBN_SHAPE_RE.match(digits):
        return False
    if len(digits) == 10:
        total = 0
        for i, ch in enumerate(digits):
            if ch == "X" and i == 9:
                v = 10
            else:
                v = int(ch)
            total += (10 - i) * v
        return total % 11 == 0
    if len(digits) == 13:
        stem = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits[:12]))
        return (10 - stem % 10) % 10 == int(digits[12])
    return False


def _bindable_asin(value: str) -> bool:
    """True for an ASIN-typed value that may bind: ASIN-shaped (B + 9
    alphanumerics, case-insensitive) or ISBN-shaped (the pre-2001 ASINs
    ARE ISBN-10s, and rips file ISBN-10 values under asin)."""
    return bool(_ASIN_SHAPE_RE.match(value or "")) or _isbn_shaped(value)


def _isbn13_form(value: str) -> str | None:
    """The value's ISBN-13 comparison form (issue #33): a 13-form passes
    through; a 10-form converts through the 978 Bookland prefix with a
    recomputed check digit; anything unshaped is None and can never
    bind. The issue-#28 shape gate runs first: the arithmetic below
    trusts its digits (an X outside the check position would crash
    int()), and a 13-char reduction carrying an X is junk that the old
    junk_isbn path kept off the isbn bind. No checksum requirement,
    matching that gate: mechanical conversion preserves whatever
    equality the pair already had (the same-typo rule), and a typo
    converts to a 13-form nobody holds."""
    digits = _isbn_digits(value)
    if not _ISBN_SHAPE_RE.match(digits):
        return None
    if len(digits) == 13:
        return digits
    stem = "978" + digits[:9]
    check = (
        10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(stem)) % 10
    ) % 10
    return stem + str(check)


def _isbn_cross_form(value: str) -> str | None:
    """The opposite-notation spelling of a shaped ISBN, when one exists
    (issue #33): a 10-form's 978-prefixed 13-form, or a 978-prefixed
    13-form's 10-form with the check digit recomputed. A 979-prefixed
    13-form has no 10-form (those registrations never carried one), and
    an unshaped value has neither."""
    digits = _isbn_digits(value)
    if len(digits) == 10:
        return _isbn13_form(digits)
    if len(digits) == 13 and digits.startswith("978"):
        stem = digits[3:12]
        check = (11 - sum((10 - i) * int(c) for i, c in enumerate(stem)) % 11) % 11
        return stem + ("X" if check == 10 else str(check))
    return None


def _isbn_pair_basis(a: str, b: str) -> str:
    """The basis string for a cross-notation bind (issue #33): both
    spellings quoted, the 13-form side first, order-free."""
    if len(_isbn_digits(a)) == 13:
        thirteen, ten = a, b
    else:
        thirteen, ten = b, a
    return f"isbn {thirteen}, 13-form of {ten}"


RESET = "\033[0m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
DIM = "\033[2m"


def _ebook_meta(path: Path) -> dict[str, str]:
    """Run Calibre's `ebook-meta` on one file and parse its report.

    The output is a flat `Key : value` listing (keys padded, values may
    themselves contain colons, so the split happens on the FIRST colon).
    Returns lowercase keys -> raw values, e.g. {"title": ..., "author(s)": ...,
    "identifiers": ...}. Missing keys simply stay absent.
    """
    proc = subprocess.run(
        ["ebook-meta", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def _embedded_fields(path: Path) -> dict[str, object]:
    """The embedded title / authors / isbn / ASIN values of one candidate
    file (the ASIN types in _ASIN_TYPES, issue #13's edition keys).

    Filename fallback direction note (roadmap :902): for a file with no
    embedded metadata, ebook-meta reports the filename as "Title - Author",
    the OPPOSITE of run.py's stamp parser ("Author - Title"). A
    metadata-less "Author - Title" download therefore screens under
    swapped fields, so the title+author path can miss a library duplicate
    for exactly those files (the exact-ISBN path is unaffected, and a
    swapped seed never false-positives). Acceptable seed quality: the
    skill's rule is that filename guesses are reviewed, and stamping or
    phase 3 replaces them.
    """
    meta = _ebook_meta(path)
    title = meta.get("title") or ""
    authors = [
        a.strip()
        for a in (meta.get("author(s)") or meta.get("authors") or "").split("&")
        if a.strip()
    ]
    isbn = ""
    asins: list[str] = []
    for pair in re.split(r"[,;]", meta.get("identifiers") or ""):
        kind, _, val = pair.strip().partition(":")
        val = val.strip()
        kind = kind.strip().lower()
        if kind == "isbn" and val:
            isbn = val
        elif kind in _ASIN_TYPES and val:
            asins.append(val)
    return {
        "file": str(path),
        "title": title,
        "authors": authors,
        "isbn": isbn,
        "asins": asins,
    }


def _fold(text: str) -> str:
    """Case- and accent-fold (NFKD, combining characters stripped)."""
    lowered = unicodedata.normalize("NFKD", text)
    return "".join(c for c in lowered if not unicodedata.combining(c)).casefold()


_EDITION_TOKEN = re.compile(r"\b(vol|volume|part|pt|book|bd)\.?\s*\d+\b", re.IGNORECASE)
# The store-front tags classics rips carry in the title (issue #13).
# Paren-scoped on purpose: an imprint named in prose is not a tag. The
# set grows as new rips surface new tags. Every title comparison in the
# judge (base, full, subtitle, core) strips them: a tag is store-front
# noise at any colon position, never work identity.
_IMPRINT_TAG = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"oxford world(?:['’]s)? classics"
    r"|penguin classics"
    r"|modern library"
    r"|everyman(?:['’]s)? library"
    r"|norton critical editions"
    r"|loeb classical library"
    r"|vintage classics"
    r"|wordsworth classics"
    r"|barnes (?:&|and) noble classics"
    r"|bantam classics"
    r"|signet classics"
    r"|dover thrift editions"
    r")\s*[\)\]]\s*",
    re.IGNORECASE,
)
# The edition tags rips and publishers bake into titles (issue #17: the
# stamp correctly strips them from new arrivals, so a held ", 3rd
# Edition" title defeated the judge on an otherwise same-edition pair).
# Ordinal + edition word only, and only where an annotation can end
# (segment end, colon, comma, semicolon, a closing bracket), so prose
# like "the 2nd edition of ..." and the "Code: Version 2.0" shape stay
# title text. Like the imprint set, this grows as new rips surface new
# shapes.
_EDITION_TAG = re.compile(
    r"\s*,?\s*\b\d+(?:st|nd|rd|th)\s+(?:edition|ed)\b\.?"
    r"(?=\s*[:;,\)\]]|\s*$)",
    re.IGNORECASE,
)
_VOLUME_SIG = re.compile(r"\b(vol|volume|part|pt|book|bd)\.?\s*(\d+)\b", re.IGNORECASE)
#: Declared volume annotations extend the arabic-only signature to the roman
#: forms ("Capital: Volume I" declares volume 1 without carrying a signature).
#: The token word anchors the match, so word look-alikes stay out.
_ANN_TOKEN = re.compile(
    r"\b(vol|volume|part|pt|book|bd)\.?\s*(\d+|[ivxlcdm]+)\b", re.IGNORECASE
)
_LEADING_ANN = re.compile(
    r"^\s*(vol|volume|part|pt|book|bd)\.?\s*(\d+|[ivxlcdm]+)\b\s*", re.IGNORECASE
)
_TRAILING_ORDINAL = re.compile(r"\s+\d+$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
#: vol/volume, part/pt and book/bd are one declaration spelled three ways;
#: the signature keeps the raw token (its shape is pinned), annotations
#: canonicalize so "Vol. 1" and "Volume 1" cannot read as a contradiction.
_ANN_KIND = {
    "vol": "volume",
    "volume": "volume",
    "bd": "volume",
    "part": "part",
    "pt": "part",
    "book": "book",
}


def _roman_to_int(text: str) -> int | None:
    """Lenient subtractive roman parsing; None when letters remain unparsed."""
    total = 0
    prev = 0
    for ch in reversed(text.casefold()):
        value = _ROMAN_VALUES.get(ch)
        if value is None:
            return None
        total += value if value >= prev else -value
        prev = max(prev, value)
    return total


def _volume_signature(title: str) -> frozenset:
    """The volume tokens of a title, e.g. "Book 8" -> {("book", "8")}.

    Serialized fiction lives and dies by this number: scrubbing it (as the
    subtitle scrub does) collapses every Wandering Inn volume into one title.
    A match therefore requires equal volume signatures whenever BOTH sides
    carry one; roman numerals ("Volume I") leave no signature and stay in the
    scrub bucket, which is the edition-annotation case the scrub exists for.
    """
    return frozenset(
        (token.casefold(), number) for token, number in _VOLUME_SIG.findall(title or "")
    )


def _annotations(title: str) -> frozenset:
    """Declared volume annotations as (canonical kind, value) pairs, arabic
    and roman. "The Wandering Inn: Book 8" -> {("book", 8)}; "Capital:
    Volume I" -> {("volume", 1)}. Two titles that both declare and disagree
    are distinct volumes of one work; the arabic-only signature cannot see
    the roman half of that rule."""
    out: set[tuple[str, int]] = set()
    for token, value in _ANN_TOKEN.findall(title or ""):
        kind = _ANN_KIND[token.casefold()]
        if value.isdigit():
            out.add((kind, int(value)))
        else:
            roman = _roman_to_int(value)
            if roman is not None:
                out.add((kind, roman))
    return frozenset(out)


def _scrub_tail(text: str) -> str:
    """Punctuation to spaces, squeeze, strip the leading article."""
    text = re.sub(r"[^0-9a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article) :].strip()
    return text


def _strip_imprint(text: str) -> str:
    """The known imprint tags removed, wherever the judge compares."""
    return _IMPRINT_TAG.sub(" ", text or "")


def _strip_edition(text: str) -> str:
    """The edition annotations removed, wherever the judge compares."""
    return _EDITION_TAG.sub(" ", text or "")


def _strip_noise(text: str) -> str:
    """Imprint tags then edition tags: storefront noise at any colon
    position, never work identity (issues #13 and #17). Imprint first so
    an edition tag the tag's removal exposes to a bracket or end of
    string still strips."""
    return _strip_edition(_strip_imprint(text))


# The trailing parenthetical qualifier rips bake into titles (issue #40:
# the series annotation "A Court of Splintered Harmony (A Court of Thorns
# and Roses)" defeated the judge on the held bare-form title). One group,
# anchored at the end; nested or mid-title brackets are title text. The
# content is judged, not blindly dropped: two DIFFERENT qualifiers are
# work identity (the Plantz ARM-vs-x86 rule) and stay distinct.
_TRAILING_PAREN = re.compile(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]\s*$")


def _trailing_paren(text: str) -> str:
    """The trailing parenthetical group's content ("Foo (Series)" ->
    "series"), or "" when the title does not end in one. Feed it the
    noise-stripped title, so a known imprint/edition tag's bracket never
    counts as content."""
    m = _TRAILING_PAREN.search(text or "")
    return m.group(1) if m else ""


def _strip_trailing_paren(text: str) -> str:
    """The trailing parenthetical annotation removed, wherever the judge
    compares (issue #40)."""
    return _TRAILING_PAREN.sub(" ", text or "", count=1)


def _judge_form(text: str) -> str:
    """The string every title comparison starts from: imprint and edition
    tags, then the trailing parenthetical, stripped (issues #13/#17/#40).
    Imprint and edition first, so a known tag's bracket strips with its
    set and never reads as parenthetical content."""
    return _strip_trailing_paren(_strip_noise(text))


def normalize_title(title: str) -> str:
    """Fold, drop the subtitle (anything before a colon), edition tokens,
    known imprint tags, edition annotations, trailing parenthetical,
    article.

    "Capital: Volume I" and "Capital: A Critique of Political Economy" both
    normalize to "capital"; "The Left Hand of Darkness" loses its article;
    "The Mabinogion (Oxford World's Classics)" loses the store-front tag
    and normalizes to "mabinogion" (issue #13: classics rips carry these,
    and the tag defeated the title judge on an otherwise exact duplicate);
    "The Rust Programming Language, 3rd Edition" loses the edition
    annotation and normalizes next to the bare title (issue #17: the stamp
    strips the tag from new arrivals, so a held edition-tagged title
    defeated the judge on a same-edition pair); "A Court of Splintered
    Harmony (A Court of Thorns and Roses)" loses the series annotation and
    normalizes next to the held bare-form title (issue #40). Noise outside
    the known sets and a non-trailing bracket compare as title text, as
    before.
    """
    text = _fold(_judge_form(title))
    text = text.split(":", 1)[0]
    text = _EDITION_TOKEN.sub(" ", text)
    return _scrub_tail(text)


def _full_title(title: str) -> str:
    """The whole title, folded and scrubbed, subtitles and volume tokens
    kept — the string the containment check compares."""
    return _scrub_tail(_fold(_judge_form(title)))


def _subtitle(title: str) -> str:
    """The post-colon segment, folded and scrubbed ("" when no colon)."""
    text = _fold(_judge_form(title))
    if ":" not in text:
        return ""
    return _scrub_tail(text.split(":", 1)[1])


def _core_title(title: str) -> str:
    """The subtitle minus ONE leading declared volume annotation: "book 8
    blood of liscor" -> "blood of liscor"; "" when the subtitle is a pure
    annotation ("volume i")."""
    sub = _subtitle(title)
    if not sub:
        return ""
    return _scrub_tail(_LEADING_ANN.sub("", sub, count=1))


def classify_titles(a: str, b: str) -> str | None:
    """The work-identity verdict for two titles (the author gate is the
    caller's). One of:

    - "duplicate": same work and edition (equal base and core subtitle).
    - "related": one full title equals the other's pre-colon base or
      post-colon subtitle — containment at a colon boundary, which is a
      masked duplicate exactly as often as it is two products of one
      series. A candidate for human judgment, never an auto-refusal.
    - "volume_sibling": distinct declared volumes of one series root
      (Moral Letters 1 vs 2; "Monster Vault" vs "Monster Vault 2").
    - "distinct": shared base, differing real subtitles (the ARM vs
      x86-64 sibling editions), or differing trailing parentheticals
      (the same rule in its parenthetical form, issue #40).
    - None: no opinion (the caller treats it as no match).

    The arabic-signature gate is today's 3.25.0 rule unchanged: one side
    declaring an arabic volume the other does not mention stays no-match
    (a Wandering Inn "Book 8" next to the bare series title is a gap-fill;
    surfacing every sibling flooded the 2026-08-30 screen). Roman-only
    declarations carry no signature and ride the annotation rules instead,
    which is what keeps the Capital fixture matching. The trailing-
    parenthetical strip is symmetrical and judged (issue #40): one side
    carrying a qualifier (a series annotation, a translator credit) strips
    and the pair binds the bare held copy, but two DIFFERENT qualifiers
    are work identity, never stripped into a match.
    """
    base_a, base_b = normalize_title(a), normalize_title(b)
    if not base_a or not base_b:
        return None
    ann_a, ann_b = _annotations(a), _annotations(b)
    sig_a, sig_b = _volume_signature(a), _volume_signature(b)
    if ann_a and ann_b and ann_a != ann_b:
        # Declared volumes disagree: "Volume 1" vs "Volume 2", "Vol I" vs
        # "Vol II". Distinct books of one series root, never a duplicate.
        return "volume_sibling" if base_a == base_b else None
    if (sig_a or sig_b) and sig_a != sig_b:
        return None
    paren_a = _scrub_tail(_fold(_trailing_paren(_strip_noise(a))))
    paren_b = _scrub_tail(_fold(_trailing_paren(_strip_noise(b))))
    if paren_a and paren_b and paren_a != paren_b and base_a == base_b:
        # Both sides carry a trailing qualifier and they disagree: real
        # content, not storefront noise (the parenthetical Plantz rule).
        # A disagreement over DIFFERENT bases is the bases' own business
        # and falls through to the containment check unchanged.
        return "distinct"
    if base_a != base_b:
        full_a, full_b = _full_title(a), _full_title(b)
        sub_a, sub_b = _subtitle(a), _subtitle(b)
        if (
            full_a
            and full_b
            and full_a != full_b
            and (full_a in (base_b, sub_b) or full_b in (base_a, sub_a))
        ):
            return "related"
        return None
    core_a, core_b = _core_title(a), _core_title(b)
    if core_a == core_b:
        return "duplicate"
    if not core_a or not core_b:
        # One side's subtitle was a pure annotation ("Capital: Volume I" vs
        # the full-subtitle edition): the annotation declares the edition,
        # nothing contradicts it, same work. A side with no subtitle at all
        # ("Arcana Unleashed" vs "Arcana Unleashed: Deadfall") is bare-base
        # containment: related, not identity.
        for sub, core in ((_subtitle(a), core_a), (_subtitle(b), core_b)):
            if sub and not core:
                return "duplicate"
        return "related"
    if _TRAILING_ORDINAL.sub("", core_a) == _TRAILING_ORDINAL.sub("", core_b):
        return "volume_sibling"
    return "distinct"


def normalize_author(author: str) -> str:
    """Fold, and drop bracket annotations ("Pirateaba [Pirateaba]" downloads)."""
    cleaned = re.sub(r"\s*\[[^\]]*\]\s*", " ", author or "")
    return _fold(cleaned).strip()


def normalize_author_brackets(author: str) -> str:
    """Strip bracket annotations but keep the original casing for searching."""
    return re.sub(r"\s*\[[^\]]*\]\s*", " ", author or "").strip()


_AUTHOR_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


def _author_tokens(author: str) -> frozenset:
    """The author's folded tokens with the single-letter ones dropped (the
    middle initials, issue #33): "Christopher W. Alexander" and
    "Christopher Alexander" share the comparison set. Every multi-letter
    token stays, so genuinely different names stay different."""
    return frozenset(
        t for t in _AUTHOR_TOKEN_SPLIT.split(normalize_author(author)) if len(t) > 1
    )


def _same_author_tokens(a: str, b: str) -> bool:
    """The first-author gate one step looser than string equality (issue
    #33): identical folded names always match (a name with no ASCII
    tokens — CJK, Cyrillic — has nothing for the token form to compare,
    and 3.70.0's string equality matched those pairs), and otherwise the
    token sets with the single-letter initials dropped must agree. An
    initials-only name therefore matches only its own spelling."""
    folded_a, folded_b = normalize_author(a), normalize_author(b)
    if folded_a and folded_a == folded_b:
        return True
    tokens = _author_tokens(a)
    return bool(tokens) and tokens == _author_tokens(b)


def _author_surname(author: str) -> str:
    """The author's last token, folded, when it is a real word (more
    than one letter): the relaxed candidate supply for the library door
    (issue #33 — the engine's exact `author:=` cannot supply a held book
    whose author carries a middle initial the file's stamp lacks, and a
    one-letter search term would substring-scan half the library).
    Empty when there is no usable token (initials-only, non-ASCII
    names); the caller covers that with the exact search."""
    tokens = [t for t in _AUTHOR_TOKEN_SPLIT.split(normalize_author(author)) if t]
    if tokens and len(tokens[-1]) > 1:
        return tokens[-1]
    return ""


_FILENAME_PARENS = re.compile(r"^(.*?)\s*\(([^)]+)\)")


def _filename_seed(rec: dict[str, object]) -> dict[str, object] | None:
    """The title/author a filename-shaped record carries, when the read is
    really a filename fallback.

    For a file with no embedded metadata, ebook-meta reports the whole
    filename stem as the title and Author(s) "Unknown" (verified live on
    calibre 9.15). When BOTH records of a pair are in that state, the
    within-batch screen compares the parsed seeds instead of the raw
    stems: the first parenthesized group as the author segment, the text
    before it as the title, and the author as an order-free token set so
    "Last, First" matches "First Last" (issue #5). The gate additionally
    requires the WHOLE stems' token sets to agree, so a marker the parse
    mistook for an author (a site-first "(z-library.sk)" group, a
    publisher bracket) cannot over-match two different works; pure
    word-order differences keep matching. A record that could be real
    metadata (any author other than the placeholder) returns None, so a
    genuine title is never rewritten; a pair whose token sets disagree
    falls through to the raw stem comparison, unchanged.
    """
    authors = rec.get("authors") or []
    if authors and not all(str(a).strip().casefold() == "unknown" for a in authors):
        return None
    stem = str(rec.get("title") or "").strip()
    m = _FILENAME_PARENS.match(stem)
    if not m:
        return None
    seed_title, author_seg = m.group(1).strip(), m.group(2).strip()
    tokens = frozenset(t for t in re.split(r"[^0-9a-z]+", _fold(author_seg)) if t)
    stem_tokens = frozenset(t for t in re.split(r"[^0-9a-z]+", _fold(stem)) if t)
    if not seed_title or not tokens:
        return None
    return {"title": seed_title, "tokens": tokens, "stem_tokens": stem_tokens}


def _search_escape(value: str) -> str:
    """Quote a value for Calibre's search grammar, escaping inner quotes."""
    return '"' + value.replace('"', r"\"") + '"'


def _library_hits(
    db, fields: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Existing library copies of one candidate file, as (duplicates,
    related). Exact ISBN first (the search engine's `isbn:X` is an exact
    keypair lookup), then the cross notation of that ISBN when one exists
    (issue #33: the library may hold the 10-form of the file's 13-form or
    the reverse), then ASIN values (the same class of edition key: an
    exact `identifiers:=VALUE` lookup matches ANY identifier type, so a
    file-side `asin` meets a library-side `mobi-asin`), then normalized
    title + first author: candidates come from the tight `=` pair search
    AND a surname search (issue #33: the exact `author:=` cannot supply a
    held book whose author carries a middle initial the file's stamp
    lacks, so the relaxed supply feeds the classifier and the author gate
    below re-checks the tokens itself), and classify_titles() judges the
    pair. Related hits are the containment candidates a series/store
    prefix can mask (the 2026-09-21 "Mothership: Wages of Sin" vs "Wages
    of Sin" silent pass): reported for judgment, never as duplicates.
    Every identifier keypair search is shape-gated (issue #28): a junk
    URL in the isbn or asin slot searches nothing. Each hit descriptor
    carries `basis`: the field that matched (the first route that found
    the book, in search order). Hydrated list fields stay lists (never
    comma-split) per the cquarry contract.
    """
    hits: list[dict[str, object]] = []
    related: list[dict[str, object]] = []
    seen: set[int] = set()

    isbn = fields.get("isbn") or ""
    if isbn and _isbn_shaped(isbn):
        searches = [(isbn, False)]
        alt = _isbn_cross_form(isbn)
        if alt:
            searches.append((alt, True))
        for value, cross in searches:
            for b in db.search_books(f"isbn:{_search_escape(value)}"):
                if b["id"] not in seen:
                    seen.add(b["id"])
                    basis = _isbn_pair_basis(isbn, value) if cross else f"isbn {isbn}"
                    hits.append({**b, "basis": basis})

    for asin in fields.get("asins") or []:
        if not _bindable_asin(asin):
            continue
        for b in db.search_books(f"identifiers:={_search_escape(asin)}"):
            if b["id"] not in seen:
                seen.add(b["id"])
                hits.append({**b, "basis": f"asin {asin}"})

    title = fields.get("title") or ""
    authors = fields.get("authors") or []
    if title and authors:
        first = normalize_author_brackets(authors[0])
        strict = db.search_books(
            f"title:{_search_escape('=' + title)} AND author:{_search_escape('=' + first)}"
        )
        surname = _author_surname(first)
        if surname:
            relaxed = db.search_books(f"author:{_search_escape(surname)}")
        else:
            # No usable surname token (initials-only, or a name with no
            # ASCII tokens): the exact form is the supply, the 3.70.0
            # behavior, and the author gate passes it on string equality.
            relaxed = db.search_books(f"author:{_search_escape('=' + first)}")
        for b in relaxed:
            if b["id"] in seen:
                continue
            # The surname matched by construction; the rest of the name
            # must agree modulo middle initials (issue #33), against ANY
            # of the book's authors (the engine's author search matches
            # any author value, and the gate keeps that semantics).
            if not any(_same_author_tokens(first, a) for a in (b.get("authors") or [])):
                continue
            verdict = classify_titles(title, b["title"] or "")
            if verdict == "duplicate":
                seen.add(b["id"])
                hits.append({**b, "basis": "title+author"})
            elif verdict == "related":
                seen.add(b["id"])
                related.append(b)
        # The strict pair match joins the set even when the scrub rules would
        # disagree with the embedded title's exact spelling.
        for b in strict:
            if b["id"] not in seen:
                seen.add(b["id"])
                hits.append({**b, "basis": "title+author"})
    return hits, related


def _scan_files(paths: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in EBOOK_EXTENSIONS:
                    files.append(child)
        elif p.is_file() and p.suffix.lower() in EBOOK_EXTENSIONS:
            files.append(p)
        else:
            skipped.append(raw)
    return files, skipped


def _screen(files: list[Path], db) -> list[dict[str, object]]:
    """Per-file candidate report: library hits, related library candidates,
    and the within-batch duplicate / related / volume-set cross-reference."""
    records: list[dict[str, object]] = []
    for f in files:
        rec = _embedded_fields(f)
        hits, related = _library_hits(db, rec)
        rec["library_hits"] = [_describe(b) for b in hits]
        if related:
            rec["related_hits"] = [_describe(b) for b in related]
        records.append(rec)

    titles = {str(r["file"]): r.get("title") or "" for r in records}
    seeds = {str(r["file"]): _filename_seed(r) for r in records}
    for i, rec in enumerate(records):
        for other in records[i + 1 :]:
            key_a, key_b = str(rec["file"]), str(other["file"])
            # Issue #33: the first-author leg compares token sets with the
            # single-letter tokens (the middle initials) dropped, so
            # "Christopher W. Alexander" and "Christopher Alexander" are
            # one author; multi-letter tokens all compare, so different
            # names stay different.
            same_author = bool(
                rec.get("authors")
                and other.get("authors")
                and _same_author_tokens(rec["authors"][0], other["authors"][0])
            )
            seed_a, seed_b = seeds[key_a], seeds[key_b]
            if (
                seed_a
                and seed_b
                and seed_a["tokens"] == seed_b["tokens"]
                and seed_a["stem_tokens"] == seed_b["stem_tokens"]
            ):
                # No embedded metadata on either side (issue #5): the parsed
                # filename seeds are the evidence. The author token sets
                # already agree; the parsed titles are judged on their own,
                # free of the bracket segments that made the raw stems
                # disagree ("Bjarne Stroustrup" vs "Stroustrup, Bjarne").
                same_author = True
                verdict = classify_titles(str(seed_a["title"]), str(seed_b["title"]))
            else:
                verdict = classify_titles(titles[key_a], titles[key_b])
            a_isbn = str(rec.get("isbn") or "")
            b_isbn = str(other.get("isbn") or "")
            a_can, b_can = _isbn13_form(a_isbn), _isbn13_form(b_isbn)
            # Issue #33: one edition in two notations is ONE ISBN (the
            # 10-form's 978-prefixed 13 conversion), binding evidence,
            # not a conflict. Unshaped values carry no 13-form and stay
            # unbindable (issue #28).
            isbn_bound = (
                bool(a_isbn) and bool(b_isbn) and a_can is not None and a_can == b_can
            )
            # Issue #28: only a real identifier shape may bind. The ASIN
            # binds within the batch exactly like the ISBN: same edition
            # key, and rips that strip ISBNs keep ASINs (issue #13).
            rec_asins = {
                a.casefold(): a for a in rec.get("asins") or [] if _bindable_asin(a)
            }
            other_asins = {
                a.casefold() for a in other.get("asins") or [] if _bindable_asin(a)
            }
            shared_asin = sorted(set(rec_asins) & other_asins)
            # Issue #28's second half: two VALID but different ISBNs are
            # evidence AGAINST a duplicate, not neutral. The 2026-10-15
            # mint refused seven distinct series volumes whose stamps had
            # collapsed to one identical title; their real, differing
            # ISBNs said distinct all along. A valid-vs-junk difference is
            # NOT evidence (one side is garbage), so only two checksum-
            # passing ISBNs suppress the title judge. Cross-notation pairs
            # never reach this state: they bound above (issue #33).
            isbn_conflict = (
                bool(a_isbn)
                and bool(b_isbn)
                and a_isbn != b_isbn
                and _isbn_valid(a_isbn)
                and _isbn_valid(b_isbn)
            )
            basis = None
            if isbn_bound:
                # Digit-identical spellings (pure producer punctuation
                # variants) quote one value; genuinely mixed notations
                # name both, 13-form first (issue #33).
                basis = (
                    _isbn_pair_basis(a_isbn, b_isbn)
                    if _isbn_digits(a_isbn) != _isbn_digits(b_isbn)
                    else f"isbn {a_isbn}"
                )
            elif shared_asin:
                basis = f"asin {rec_asins[shared_asin[0]]}"
            elif same_author and verdict == "duplicate":
                if a_isbn and b_isbn and a_isbn != b_isbn:
                    basis = f"title+author, isbn differs: {a_isbn} vs {b_isbn}"
                elif not a_isbn or not b_isbn:
                    basis = "title+author, isbn absent on one side"
                else:
                    # Identical values that the shape gate refused to
                    # bind on (junk): the titles and author are the
                    # evidence, the identifier equality is not.
                    basis = "title+author"
            if (
                isbn_bound
                or shared_asin
                or (same_author and verdict == "duplicate" and not isbn_conflict)
            ):
                rec.setdefault("batch_duplicates", []).append(other["file"])
                other.setdefault("batch_duplicates", []).append(rec["file"])
                rec.setdefault("batch_duplicate_basis", {})[str(other["file"])] = basis
                other.setdefault("batch_duplicate_basis", {})[str(rec["file"])] = basis
            elif not same_author:
                continue
            elif verdict == "related":
                rec.setdefault("batch_related", []).append(other["file"])
                other.setdefault("batch_related", []).append(rec["file"])
            elif verdict == "volume_sibling":
                rec.setdefault("batch_volumes", []).append(other["file"])
                other.setdefault("batch_volumes", []).append(rec["file"])
    return records


def _describe(b: dict) -> dict[str, object]:
    """The comparison columns the phase-1 skill says to report per copy,
    plus `basis`: the field that matched (issue #30), always present for
    descriptors _library_hits built, absent for anything else."""
    out = {
        "id": b["id"],
        "title": b["title"],
        "authors": b["authors"],
        "formats": b["formats"],
        "size": b.get("size"),
        "pages": b.get("pages"),
    }
    if b.get("basis"):
        out["basis"] = b["basis"]
    return out


def _print_report(records: list[dict[str, object]], skipped: list[str]) -> None:
    candidates = 0
    for rec in records:
        hits = rec["library_hits"]
        related = rec.get("related_hits") or []
        batch = rec.get("batch_duplicates") or []
        batch_rel = rec.get("batch_related") or []
        volumes = rec.get("batch_volumes") or []
        flag = YELLOW if (hits or batch or related or batch_rel) else ""
        status = "CANDIDATE" if (hits or batch or related or batch_rel) else "clean"
        print(f"{flag}[{status:9}] {rec['file']}{RESET}")
        if rec.get("title"):
            print(f"           embedded: {rec['title']}")
        if rec.get("authors"):
            print(f"           authors:  {' & '.join(rec['authors'])}")
        if rec.get("isbn"):
            print(f"           isbn:     {rec['isbn']}")
        if rec.get("asins"):
            print(f"           asins:    {', '.join(rec['asins'])}")
        for h in hits:
            candidates += 1
            fmts = ", ".join(h["formats"]) or "no formats"
            size = f", {h['size']:,} bytes" if h.get("size") else ""
            pages = f", {h['pages']} pages" if h.get("pages") else ""
            basis = f" on {h['basis']}" if h.get("basis") else ""
            print(
                f"           {RED}vs library #{h['id']}{RESET}{basis}: {h['title']}"
                f": {' & '.join(h['authors'])} ({fmts}{size}{pages})"
            )
            print(
                f"{DIM}             compare size/pages and quality before judging.{RESET}"
            )
        for h in related:
            candidates += 1
            fmts = ", ".join(h["formats"]) or "no formats"
            print(
                f"           {YELLOW}vs library #{h['id']} (related title, judge): "
                f"{h['title']}: {' & '.join(h['authors'])} ({fmts}){RESET}"
            )
            print(
                f"{DIM}             one title is the other's series prefix or subtitle: "
                f"masked duplicate or two products? decide by hand.{RESET}"
            )
        batch_basis = rec.get("batch_duplicate_basis") or {}
        for other in batch:
            candidates += 1
            suffix = f" ({batch_basis[other]})" if other in batch_basis else ""
            print(f"           {YELLOW}duplicate within batch{RESET}: {other}{suffix}")
        for other in batch_rel:
            candidates += 1
            print(f"           {YELLOW}related within batch (judge){RESET}: {other}")
        for other in volumes:
            print(
                f"{DIM}           multi-volume set member (distinct volume): {other}{RESET}"
            )
    if skipped:
        print(f"\n{DIM}Skipped (not ebook files): {', '.join(skipped)}{RESET}")
    print(f"\n{len(records)} file(s) screened, {candidates} candidate pair(s).")


def _resolve_db(path: str | None) -> Path | None:
    """Accept a library directory or a metadata.db path, like the sibling scripts."""
    p = Path(path).expanduser() if path else Path.cwd()
    if p.is_dir():
        candidate = p / "metadata.db"
        return candidate if candidate.exists() else None
    return p if p.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Screen loose ebook files against the Calibre library "
        "(and within the batch) for duplicates. Read-only; report-only."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="files or directories to screen (EPUB/PDF/MOBI/AZW3)",
    )
    parser.add_argument(
        "--db",
        help="library directory or metadata.db (default: saved config / discovery)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (json is the batch-report shape)",
    )
    args = parser.parse_args()

    announce()

    files, skipped = _scan_files(args.paths)
    if not files:
        print("ERROR: no ebook files to screen.", file=sys.stderr)
        return 2

    from cquarry.db import CalibreDB
    from cquarry.helpers import find_db

    try:
        db_path = _resolve_db(args.db) or find_db()
        db = CalibreDB(str(db_path))
    except Exception as e:
        print(f"ERROR: cannot open the library: {e}", file=sys.stderr)
        return 2

    try:
        records = _screen(files, db)
    finally:
        db.close()

    if args.format == "json":
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        _print_report(records, skipped)

    found = any(
        r["library_hits"]
        or r.get("batch_duplicates")
        or r.get("related_hits")
        or r.get("batch_related")
        for r in records
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
