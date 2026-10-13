"""EPUB OPF post-embed normalization, shared by
scripts/reconcile_file_metadata.py and the `run flush` verb (every path that
embeds EPUB metadata via calibredb). Two passes share the same machinery
(locate the OPF through container.xml, surgery confined to the <metadata>
block, whole-archive rewrite through a temp file + os.replace):

* dc:date canonicalization (normalize_epub_dates): calibredb's EPUB2 writer
  (the path any version="2.0" package takes) rewrites only the EARLIEST
  dc:date element in place and leaves every other one standing, while
  calibre's EPUB2 reader reports the MINIMUM of the dates it finds. A file
  that ever carried a second date (a fetch-era run-clock value, the
  publisher original, the modification stamp) therefore kept all of them
  forever: the embed "converged" only while the minimum happened to equal
  the database date, and any leftover date older than it read back as
  permanent pubdate drift (the #9177/#9635 live class; both files are EPUB2
  packages carrying three dc:date elements). Every embed therefore ends
  with this pass: the OPF is forced to exactly one canonical dc:date built
  by canonical_date_target from the database pubdate.

* identifier-set normalization (normalize_epub_identifiers): calibre's
  embed writer preserves the package's unique-identifier dc:identifier
  element VERBATIM (set_identifiers only reuses its text when the element's
  own scheme names a database type) and never removes scheme-less
  identifier elements, so a deleted type sitting in the package slot
  survives every embed and reads back as permanent identifier drift
  (issue #11's reopen: the OPF ends up carrying both the new type and the
  deleted one, and repeated --apply passes oscillate instead of
  converging; live-verified on calibre 9.15/9.16). This pass forces the
  file's identifier set to the database's by the same two-directional rule
  the read-back diff applies: keep elements matching a database entry
  (first match wins, later duplicates go), keep the file-only extras and
  the writer's own invisible schemes (calibre/uuid, which the read seam
  never surfaces), remove everything else, insert unsatisfied database
  entries, and rewrite a stale package unique-identifier element IN PLACE
  (id attribute and position stay; scheme and text become the book uuid
  under the writer's own calibre scheme) so the package reference never
  dangles. A default-namespace EPUB3 package — EPUB 3's alternate form,
  the OPF namespace bound as the default on <package> and dc bound there
  too, no OPF-prefix binding anywhere — is handled in place: when the
  surgery must name the OPF namespace it synthesizes the binding as a
  declaration on the <metadata> opening tag (issue #16's live class)
  instead of refusing the pass."""

import os
import re
import tempfile
import zipfile
from html import unescape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# File-side identifier types that are not drift when the database lacks
# them: every calibredb embed re-adds the book uuid (ebook-meta surfaces it
# as a `uri` id), and some publisher files keep an `ean` the library does
# not track. Anything else file-side that the DB lacks IS drift (issue #11):
# DB-is-source-of-truth runs both directions. Single source shared by the
# identifier diff (scripts/reconcile_file_metadata.py) and the identifier
# normalization pass below.
FILE_ONLY_IDENTIFIERS = frozenset({"uri", "ean"})

# Schemes calibre's own embed writer manages (application_id/uuid re-adds
# them on every embed) and the read seam never surfaces as an identifier
# type, so they can neither drift nor converge and the pass leaves them
# standing wherever it finds them.
_WRITER_SCHEMES = frozenset({"calibre", "uuid"})

_DC_DATE_SPAN = re.compile(
    r"<dc:date\b[^>]*>.*?</dc:date\s*>|<dc:date\b[^>]*/>", re.DOTALL
)
_METADATA_BLOCK = re.compile(r"<metadata\b.*?</metadata\s*>", re.DOTALL | re.IGNORECASE)
_DC_NS_BINDING = 'xmlns:dc="http://purl.org/dc/elements/1.1/"'
# The OPF-namespace declaration the identifier surgery synthesizes on the
# <metadata> opening tag when the document binds no prefix for it (a
# default-namespace EPUB3 package, issue #16's live class) and must name
# the namespace anyway: for the scheme attribute it adds to a rewritten
# package slot or to a freshly inserted identifier.
_OPF_NS_BINDING = 'xmlns:opf="http://www.idpf.org/2007/opf"'
# OPF 2.0 DTD order: title, creator*, subject*, description?, publisher?,
# contributor*, date*, ... identifier+. A freshly inserted date goes after the
# last of the elements that precede date in that order.
_DATE_PREDECESSORS = (
    "dc:title",
    "dc:creator",
    "dc:subject",
    "dc:description",
    "dc:publisher",
    "dc:contributor",
)

_DATE_ONLY = re.compile(r"(\d{4}-\d{2}-\d{2})")

_DC_IDENTIFIER_SPAN = re.compile(
    # self-closing first: the paired-first alternation the date span uses
    # would let `<dc:identifier .../>` merge with the NEXT paired element
    # into one swallowed span
    r"<dc:identifier\b[^>]*/>|<dc:identifier\b[^>]*>.*?</dc:identifier\s*>",
    re.DOTALL,
)
# Any scheme attribute, namespaced or not: the reader accepts an attribute
# whose name merely ENDS with "scheme" (opf:scheme, ns2:scheme, plain
# scheme), so the pass must too. Group 2 is the quoted value.
_SCHEME_ATTR = re.compile(r"((?:[A-Za-z_][\w.-]*:)?scheme)\s*=\s*(\"[^\"]*\"|'[^']*')")
_ID_ATTR = re.compile(r"\bid\s*=\s*(\"[^\"]*\"|'[^']*')")
_UNIQUE_IDENTIFIER = re.compile(
    r"unique-identifier\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE
)
_OPF_PREFIX = re.compile(
    r"xmlns:([A-Za-z_][\w.-]*)\s*=\s*[\"']http://www\.idpf\.org/2007/opf[\"']"
)
_URN_ISBN_PREFIX = "urn:isbn:"


def norm_date(value: str | None) -> str:
    """Reduce a date/timestamp to YYYY-MM-DD; '' for the sentinel/empty.
    The same reduction the file-metadata diff uses, so a normalization
    target always matches what the read-back compares."""
    s = re.sub(r"\s+", " ", (value or "")).strip()
    m = _DATE_ONLY.match(s)
    if not m:
        return ""
    d = m.group(1)
    return "" if d.startswith("0101-01-01") else d


def canonical_date_target(pubdate: str | None) -> str:
    """The dc:date value a normalized EPUB carries: the database pubdate's
    date at UTC midnight as a full ISO instant; '' for the sentinel/empty
    (the zero-dates case, matching how the diff compares). Full ISO, not
    date-only, because calibre's EPUB3 reader nudges a date-only value off
    month boundaries (fix_only_date: day 1 becomes day 2, a month's last
    day is pulled back one), so the date-only canonical read back one day
    off through ebook-meta for every day-1 pubdate and the read-back
    re-diff never converged (issue #4, the five-book live case; the EPUB2
    reader uses the general parser and never nudges, which is why the
    EPUB2 class converged). A full ISO instant round-trips exactly under
    both readers, the same shape calibre's own EPUB3 writer emits."""
    d = norm_date(pubdate)
    return f"{d}T00:00:00Z" if d else ""


def canonical_dc_dates(opf_text: str, target: str) -> str | None:
    """Rewrite the OPF so its metadata block carries exactly one dc:date with
    `target` as its element text, or none when target is '' (the unset/sentinel
    case, matching how the diff compares; callers build the target with
    canonical_date_target). Returns byte-identical text when the
    file is already canonical, or None when the shape cannot be safely
    rewritten: no metadata block, or dates to insert while the dc prefix is
    bound nowhere in scope — neither in the metadata block nor on an
    ancestor of it, a default-namespace EPUB3's <package> included (an
    insertion the document cannot name must not be half-edited; the
    read-back re-diff stays the arbiter).
    Surgery is confined to the metadata block — everything outside it,
    including EPUB3's dcterms:modified meta element, is untouched."""
    block_m = _METADATA_BLOCK.search(opf_text)
    if block_m is None:
        return None
    block = block_m.group(0)
    canonical = f"<dc:date>{target}</dc:date>"
    spans = list(_DC_DATE_SPAN.finditer(block))
    if not spans and not target:
        return opf_text
    if len(spans) == 1 and target and spans[0].group(0) == canonical:
        return opf_text
    if not spans:
        if (
            _DC_NS_BINDING not in block
            and _DC_NS_BINDING not in opf_text[: block_m.start()]
        ):
            return None
        anchor = None
        for tag in _DATE_PREDECESSORS:
            for m in re.finditer(
                rf"<{tag}\b[^>]*/>|<{tag}\b[^>]*>.*?</{tag}\s*>", block, re.DOTALL
            ):
                if anchor is None or m.start() > anchor.start():
                    anchor = m
        if anchor is None:
            return None
        line_start = block.rfind("\n", 0, anchor.start()) + 1
        indent = block[line_start : anchor.start()]
        if indent.strip():  # anchor shares a line with something else
            indent = ""
        return (
            opf_text[: block_m.start()]
            + block[: anchor.end()]
            + "\n"
            + indent
            + canonical
            + block[anchor.end() :]
            + opf_text[block_m.end() :]
        )
    out: list[str] = []
    pos = 0
    for i, span in enumerate(spans):
        if i == 0 and target:
            out.append(block[pos : span.start()])
            out.append(canonical)
            pos = span.end()
            continue
        start = span.start()
        while start > pos and block[start - 1] in " \t":
            start -= 1
        if start > pos and block[start - 1] == "\n":
            start -= 1
            if start > pos and block[start - 1] == "\r":
                start -= 1
        out.append(block[pos:start])
        pos = span.end()
    out.append(block[pos:])
    return opf_text[: block_m.start()] + "".join(out) + opf_text[block_m.end() :]


def _norm_ident(value: str | None) -> str:
    """The comparison form for one identifier type or value: XML entities
    decoded, whitespace collapsed, case-folded. The same shape the read-back
    diff compares (it lowercases ebook-meta's line and the database rows)."""
    return re.sub(r"\s+", " ", unescape(value or "")).strip().lower()


def _opening_tag(span_text: str) -> str:
    """The opening tag of one element span, through its first '>'. The same
    prefix the scheme and id reads below run over."""
    return span_text[: span_text.index(">") + 1]


def _identifier_parts(span_text: str) -> tuple[str | None, str, str | None]:
    """(scheme, value, id) for one dc:identifier span, read the way calibre's
    OPF reader reads them: any attribute whose name ends with 'scheme' names
    the type (calibre/uuid included; the caller decides what those mean), the
    element text is the value, and an isbn element's urn:isbn: value prefix
    is spelling, not content. A scheme-less element comes back with scheme
    None and its raw text — the reader derives an isbn only from the
    urn:isbn: form there, which the caller checks itself."""
    opening = _opening_tag(span_text)
    scheme = None
    scheme_m = _SCHEME_ATTR.search(opening)
    if scheme_m:
        scheme = _norm_ident(scheme_m.group(2)[1:-1]) or None
    id_m = _ID_ATTR.search(opening)
    span_id = id_m.group(1)[1:-1] if id_m else None
    body = re.fullmatch(
        r"<dc:identifier\b[^>]*>(.*)</dc:identifier\s*>", span_text, re.DOTALL
    )
    value = _norm_ident(body.group(1)) if body else ""
    if scheme == "isbn" and value.startswith(_URN_ISBN_PREFIX):
        value = value[len(_URN_ISBN_PREFIX) :]
    return scheme, value, span_id


def _rewrite_package_identifier(
    span_text: str, uuid: str, opf_prefix: str | None
) -> str | None:
    """The stale package unique-identifier element, rewritten in place: the
    element name, its id attribute, and its position stay; the scheme value
    becomes `calibre` (the writer's own scheme, invisible to the read seam)
    and the text the book uuid. Returns None when the rewrite cannot be
    named (no uuid, or a scheme attribute must be added and the caller
    passed no prefix — with the synthesis above the caller, that refuses
    only shapes this module never produces)."""
    if not uuid:
        return None
    opening_end = span_text.index(">")
    opening = span_text[:opening_end].rstrip()
    if opening.endswith("/"):
        # a self-closing span: drop the slash so the rewritten element can
        # carry text
        opening = opening[:-1].rstrip()
    scheme_m = _SCHEME_ATTR.search(opening)
    if scheme_m:
        opening = (
            opening[: scheme_m.start(2)] + '"calibre"' + opening[scheme_m.end(2) :]
        )
    elif opf_prefix:
        opening = opening + f' {opf_prefix}:scheme="calibre"'
    else:
        return None
    closing_m = re.search(r"</dc:identifier\s*>\s*$", span_text)
    if closing_m is None:  # a self-closing span gains its body
        return opening + ">" + xml_escape(uuid) + "</dc:identifier>"
    return opening + ">" + xml_escape(uuid) + span_text[closing_m.start() :]


def _declare_namespaces(block: str, bindings: list[str]) -> str:
    """Add namespace declarations to the <metadata> opening tag of a block:
    in scope for everything the surgery writes inside it, on an element no
    identifier span can be, and idempotent (the next pass finds the binding
    and synthesizes nothing). The self-closing shape has nothing to rewrite
    and never reaches here with bindings, but still comes out well-formed."""
    m = re.match(r"<metadata\b[^>]*>", block, re.IGNORECASE)
    if m is None:  # unreachable: the block came from _METADATA_BLOCK
        return block
    core = m.group(0)[:-1].rstrip()
    tail = ">"
    if core.endswith("/"):
        core = core[:-1].rstrip()
        tail = "/>"
    return block[: m.start()] + f"{core} {' '.join(bindings)}{tail}" + block[m.end() :]


def canonical_dc_identifiers(
    opf_text: str,
    identifiers: dict[str, str] | None,
    uuid: str,
) -> str | None:
    """Rewrite the OPF so its metadata block's identifier set is the
    database's: keep elements matching a database entry (the first wins;
    later duplicates go), keep the file-only extras (FILE_ONLY_IDENTIFIERS
    the database does not track) and the writer's own calibre/uuid schemes,
    remove every other element — scheme-less ones included, which the embed
    writer itself never removes — insert unsatisfied database entries after
    the last identifier, and rewrite a stale package unique-identifier
    element in place (its id attribute and position stay; scheme and text
    become the book uuid under the writer's own calibre scheme, which the
    read seam never surfaces) so the package reference never dangles.

    Returns byte-identical text when the file is already canonical, or None
    when the shape cannot be safely rewritten: no metadata block, no uuid
    for a package rewrite, a dc binding nowhere in scope for an insertion
    (neither in the metadata block nor on an ancestor of it — malformed XML
    no embed could have produced), or no anchor for an insertion. A missing
    OPF-namespace prefix never refuses: when the surgery must name one (a
    scheme-less package slot to rewrite, any insertion), it synthesizes the
    binding as a declaration on the <metadata> opening tag — in scope for
    the whole surgery and discovered by the next pass, so a converged file
    stays byte-identical (issue #16's default-namespace EPUB3 class). The
    read-back re-diff stays the arbiter.
    Surgery is confined to the metadata block."""
    block_m = _METADATA_BLOCK.search(opf_text)
    if block_m is None:
        return None
    block = block_m.group(0)
    target = {
        _norm_ident(k): _norm_ident(v)
        for k, v in (identifiers or {}).items()
        if _norm_ident(k) and _norm_ident(v)
    }
    uuid = (uuid or "").strip()
    unique_m = _UNIQUE_IDENTIFIER.search(opf_text)
    unique_id = unique_m.group(1)[1:-1] if unique_m else None
    spans = list(_DC_IDENTIFIER_SPAN.finditer(block))
    satisfied: set[str] = set()
    actions: list[tuple[re.Match, str]] = []
    changed = False
    for span in spans:
        scheme, value, span_id = _identifier_parts(span.group(0))
        is_pkg = unique_id is not None and span_id is not None and span_id == unique_id
        if (
            scheme is not None
            and scheme in target
            and value == target[scheme]
            and (is_pkg or scheme not in satisfied)
        ):
            # A database entry satisfied by its element. First, because the
            # reader's identifiers dict is last-wins and later duplicates
            # must go. Every scheme'd shape counts, calibre/uuid-typed
            # database rows included: they are invisible to the read seam,
            # but treating them as unsatisfiable grew the file by one
            # inserted duplicate on every pass.
            satisfied.add(scheme)
            actions.append((span, "keep"))
        elif scheme in _WRITER_SCHEMES:
            actions.append((span, "keep"))
        elif (
            not is_pkg
            and scheme is not None
            and scheme in FILE_ONLY_IDENTIFIERS
            and scheme not in target
        ):
            actions.append((span, "keep"))
        elif is_pkg:
            # Stale or junk content in the package slot: every scheme-less
            # element lands here too. None of them may satisfy a database
            # entry, because calibre's reader runs scheme-less urn:isbn
            # values through check_isbn (dash-stripped, check-digit
            # validated) while the diff compares the raw stored form — an
            # element kept as a satisfier there could read back a different
            # value than it stores and the book would never converge. The
            # entry is inserted fresh in its scheme'd form instead, which
            # round-trips raw.
            actions.append((span, "rewrite"))
            changed = True
        else:
            actions.append((span, "remove"))
            changed = True
    missing = [k for k in target if k not in satisfied]
    if missing:
        changed = True
    if not changed:
        return opf_text

    # What the rebuilt block must be able to name: a bound OPF prefix for
    # every scheme attribute the surgery adds (a scheme-less package slot
    # being rewritten, any insertion) and the dc binding in scope for the
    # elements it inserts. A default-namespace EPUB3 package (the EPUB 3
    # alternate form: xmlns on <package>, dc bound there too, no OPF-prefix
    # binding anywhere) is well-formed and calibre-readable, so the pass
    # may not refuse it (issue #16's live class: 13 books); it synthesizes
    # the missing OPF binding as a declaration on the <metadata> opening
    # tag instead, in scope for the whole surgery. Removals that name no
    # new element add no declaration: the byte-exact no-churn contract.
    needs_opf_prefix = bool(missing) or any(
        action == "rewrite" and _SCHEME_ATTR.search(_opening_tag(span.group(0))) is None
        for span, action in actions
    )
    prefix = _opf_prefix_of(opf_text)
    synthetic: list[str] = []
    if needs_opf_prefix and prefix is None:
        prefix = "opf"
        synthetic.append(_OPF_NS_BINDING)
    if (
        missing
        and _DC_NS_BINDING not in block
        and _DC_NS_BINDING not in opf_text[: block_m.start()]
    ):
        return None

    out: list[str] = []
    pos = 0
    for span, action in actions:
        if action == "keep":
            out.append(block[pos : span.end()])
            pos = span.end()
            continue
        if action == "rewrite":
            new_span = _rewrite_package_identifier(span.group(0), uuid, prefix)
            if new_span is None:
                return None
            out.append(block[pos : span.start()])
            out.append(new_span)
            pos = span.end()
            continue
        start = span.start()
        while start > pos and block[start - 1] in " \t":
            start -= 1
        if start > pos and block[start - 1] == "\n":
            start -= 1
            if start > pos and block[start - 1] == "\r":
                start -= 1
        out.append(block[pos:start])
        pos = span.end()
    new_block = "".join(out) + block[pos:]
    if synthetic:
        new_block = _declare_namespaces(new_block, synthetic)

    if not missing:
        return opf_text[: block_m.start()] + new_block + opf_text[block_m.end() :]
    # The dc and OPF-prefix preconditions were checked above; what is left
    # is the anchor: the last identifier, or the last DTD-predecessor when
    # the block carries no identifiers at all.
    anchor = None
    for m in _DC_IDENTIFIER_SPAN.finditer(new_block):
        anchor = m
    if anchor is None:
        for tag in (*_DATE_PREDECESSORS, "dc:date"):
            for m in re.finditer(
                rf"<{tag}\b[^>]*/>|<{tag}\b[^>]*>.*?</{tag}\s*>", new_block, re.DOTALL
            ):
                if anchor is None or m.end() > anchor.end():
                    anchor = m
    if anchor is None:
        return None
    line_start = new_block.rfind("\n", 0, anchor.start()) + 1
    indent = new_block[line_start : anchor.start()]
    if indent.strip():  # anchor shares a line with something else
        indent = ""
    insertions = "".join(
        f'\n{indent}<dc:identifier {prefix}:scheme="{xml_escape(k)}">'
        f"{xml_escape(target[k])}</dc:identifier>"
        for k in missing
    )
    return (
        opf_text[: block_m.start()]
        + new_block[: anchor.end()]
        + insertions
        + new_block[anchor.end() :]
        + opf_text[block_m.end() :]
    )


def _opf_prefix_of(opf_text: str) -> str | None:
    m = _OPF_PREFIX.search(opf_text)
    return m.group(1) if m else None


def _opf_entry_name(zf: zipfile.ZipFile) -> str | None:
    """The OPF path from META-INF/container.xml's first rootfile entry."""
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8-sig")
    except KeyError, ValueError:
        return None
    m = re.search(r'full-path\s*=\s*"([^"]+)"', container)
    return m.group(1) if m else None


def _rewrite_opf(path: Path, surgery) -> bool:
    """Run one OPF surgery over an EPUB and rewrite the archive when it
    changes anything. True when the file ends canonical (including when it
    already was: no rewrite then); False on any failure — a file that cannot
    be opened, an OPF the surgery refuses, a zip that cannot be rebuilt. The
    archive is rewritten whole through a temp file + os.replace, entry order
    and per-entry zip metadata preserved, so a crash never leaves a
    half-written EPUB behind."""
    try:
        with zipfile.ZipFile(path, "r", strict_timestamps=False) as zin:
            opf_name = _opf_entry_name(zin)
            if opf_name is None:
                return False
            infos = zin.infolist()
            payloads = {info: zin.read(info) for info in infos}
        opf_info = next((i for i in reversed(infos) if i.filename == opf_name), None)
        if opf_info is None:
            return False
        opf_text = payloads[opf_info].decode("utf-8-sig")
        new_text = surgery(opf_text)
        if new_text is None:
            return False
        if new_text == opf_text:
            return True
        payloads[opf_info] = new_text.encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with (
                os.fdopen(fd, "wb") as tf,
                zipfile.ZipFile(tf, "w", strict_timestamps=False) as zout,
            ):
                for info in infos:
                    zout.writestr(info, payloads[info])
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


def normalize_epub_dates(path: Path, target: str) -> bool:
    """Force one EPUB's OPF dc:date set to the canonical shape. True when the
    file ends canonical (including when it already was: no rewrite then);
    False on any failure — a file that cannot be opened, an OPF this cannot
    safely rewrite, a zip that cannot be rebuilt. The archive is rewritten
    whole through a temp file + os.replace, entry order and per-entry zip
    metadata preserved, so a crash never leaves a half-written EPUB behind."""
    return _rewrite_opf(path, lambda text: canonical_dc_dates(text, target))


def normalize_epub_identifiers(
    path: Path, identifiers: dict[str, str] | None, uuid: str
) -> bool:
    """Force one EPUB's OPF identifier set to the database's (the
    identifier-set half of the module docstring; the pass exists because the
    embed writer preserves the package unique-identifier element verbatim and
    never removes scheme-less elements, so a deleted identifier type in
    either shape survives every embed and the read-back re-diff never
    converges). Same contract as normalize_epub_dates: True when the file
    ends canonical, False on any failure, already-canonical files are not
    rewritten."""
    return _rewrite_opf(
        path, lambda text: canonical_dc_identifiers(text, identifiers, uuid)
    )
