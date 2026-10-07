"""The two-level help surface: a compact overview on bare `--help` and
focused deep-dive pages on `--help TOPIC` (plus per-verb pages under `run`).
On a terminal the output is ANSI-colored with the exact CPython 3.14
argparse theme (the colorization argparse itself would have printed before
the custom renderer took over); piped output stays plain.

The grammar is untouched: this module runs as a pre-parse intercept in
cli.main() and only ever prints. Flag tables are generated from the live
parser's argument groups, so a flag added to a group shows up in its topic
by construction; the curated layer is the prose and the per-verb claim map
(which run flag belongs to which verb), and tests/test_help.py pins that
map against build_parser() so an undocumented flag cannot land quietly."""

import json
import os
import re
import sys
import textwrap

from cquarry_cli import VERSION

# Group titles, shared with cli.py's add_argument_group calls so topic
# rendering can find the groups without knowing their positions.
MODES_TITLE = "modes (pick exactly one)"
SCOPING_TITLE = "scoping"
OUTPUT_TITLE = "output"
DISPLAY_TITLE = "display tweaks"
MODIFIERS_TITLE = "mode modifiers"
WRITE_TITLE = "write verbs (Calibre must be closed)"
SET_TITLE = (
    "set writes (dry-run by default; --apply requires --backup-dir and Calibre closed)"
)

TOPICS = ("read", "write", "set", "run", "examples", "all", "json")

# The color theme mirrors the CPython 3.14 argparse default (_colorize's
# argparse theme: bold blue headings and usage, bold magenta prog, bold cyan
# long options, bold green short options, bold yellow metavars/labels), so
# cquarry's help reads like `python --help` in the same terminal. The
# renderer lays out PLAIN text and paints the finished lines, so column
# math never sees the (zero-width) codes and one layout serves both faces.
_THEME = {
    "heading": "\x1b[1;34m",
    "prog": "\x1b[1;35m",
    "long_option": "\x1b[1;36m",
    "short_option": "\x1b[1;32m",
    "label": "\x1b[1;33m",
    "reset": "\x1b[0m",
}

_CSI = re.compile(r"\x1b\[[0-9;]*m")
_LONG_OPT = re.compile(r"(?<![\w/-])--[\w][\w-]*")
_SHORT_OPT = re.compile(r"(?<![\w-])-([A-Za-z])\b")
_LABEL = re.compile(r"\b[A-Z][A-Z0-9]*\b")
_PROG = re.compile(r"(?<![\w~./])cquarry\b")


def _can_color(stream=None) -> bool:
    """FORCE_COLOR wins, then NO_COLOR and PYTHON_COLORS=0 suppress, then a
    TTY check: piped output (tests, README embedding) stays plain. The
    same contract lattice-music's help ships."""
    if stream is None:
        stream = sys.stdout
    force = os.environ.get("FORCE_COLOR")
    if force and force != "0":
        return True
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("PYTHON_COLORS") == "0":
        return False
    try:
        return stream.isatty()
    except ValueError:
        return False


def _paint_line(line: str, style: dict[str, str]) -> str:
    """Paint the command-shaped tokens of one finished layout line: long
    options, short options, ALL-CAPS metavars/labels, and the prog name.
    Prose stays plain."""
    line = _LONG_OPT.sub(style["long_option"] + r"\g<0>" + style["reset"], line)
    line = _SHORT_OPT.sub(style["short_option"] + r"\g<0>" + style["reset"], line)
    line = _LABEL.sub(style["label"] + r"\g<0>" + style["reset"], line)
    line = _PROG.sub(style["prog"] + r"\g<0>" + style["reset"], line)
    return line


def paint(text: str, enabled: bool | None = None) -> str:
    """Colorize a finished help page (layout already done on plain text).
    Disabled (piped, NO_COLOR, PYTHON_COLORS=0) returns the text unchanged;
    FORCE_COLOR forces. Stripped of codes, the painted render is identical
    to the plain one."""
    if enabled is None:
        enabled = _can_color()
    if not enabled:
        return text
    style = _THEME
    return "\n".join(_paint_line(line, style) for line in text.splitlines())


# The run verbs, staged the way the acquisition pathway thinks about them:
# (verb, one-liner). Order is the run overview's order.
RUN_STAGES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "the acquisition pathway (vet -> import -> curate)",
        (
            ("phase1", "vet a downloads dir into a review manifest"),
            ("sign", "seal the reviewed manifest for phase 2"),
            ("approve", "re-derive the approved set from the per-file verdicts"),
            ("phase2", "import the signed manifest as one batch"),
            ("phase3", "curate the import, then the mechanical pass"),
        ),
    ),
    (
        "library operations (dry-run by default; --apply executes)",
        (
            ("convert", "ebook-convert targets to another format"),
            ("polish", "ebook-polish EPUBs (smarten, subset, jacket, ...)"),
            ("cover", "set or remove covers"),
            ("export", "copy format files out with calibredb export"),
            ("merge", "fold a duplicate into a keeper book"),
            ("flush", "embed the OPF-dirty queue (calibredb embed_metadata)"),
            ("backfill", "fetch title/authors/publisher/isbn from metadata sources"),
            ("trash", "list, empty, or expire .caltrash"),
        ),
    ),
    (
        "headless calibre verbs (Phase 20 parity)",
        (
            ("backup-metadata", "regenerate sidecar OPFs over the dirtied queue"),
            ("restore-database", "rebuild a metadata.db from stored OPFs"),
            ("clone", "create a fresh-schema empty library"),
            ("fts-index", "drive Calibre's full-text extractor / report status"),
            ("catalog-epub", "calibredb's EPUB catalog plugin"),
            ("catalog-bibtex", "calibredb's BIBTEX catalog plugin"),
            ("customize", "manage Calibre plugins without the GUI"),
            ("debug-tools", "explode / implode / diff / kepubify / inspect-mobi"),
            ("device", "list, read, and (at --apply) change files on a reader"),
            ("news", "fetch news editions via Calibre's recipes (network at --apply)"),
        ),
    ),
)
RUN_VERBS: tuple[str, ...] = tuple(verb for _, verbs in RUN_STAGES for verb, _ in verbs)

# Which run flags each verb consumes. Shared flags every verb accepts
# (--apply where it mutates, --db, --format, --quiet) live in
# SHARED_RUN_FLAGS instead, so the claim map stays per-verb truth.
SHARED_RUN_FLAGS = ("apply", "db", "format", "quiet")
# The verbs that actually consult --apply (dry run by default). The
# acquisition-pathway verbs do NOT: phase1's file-side writes are the
# separate --stamp/--apply-lossy/--quarantine opt-ins, and
# sign/approve/phase2/phase3 execute the manifest exactly as their pages
# document, so their pages must not advertise --apply.
APPLY_VERBS = frozenset(
    {
        "convert",
        "polish",
        "cover",
        "export",
        "merge",
        "flush",
        "backfill",
        "trash",
        "backup-metadata",
        "restore-database",
        "clone",
        "fts-index",
        "catalog-epub",
        "catalog-bibtex",
        "customize",
        "debug-tools",
        "device",
        "news",
    }
)


def shared_flags_for(verb: str) -> tuple[str, ...]:
    if verb in APPLY_VERBS:
        return SHARED_RUN_FLAGS
    return ("db", "format", "quiet")


VERB_FLAGS: dict[str, tuple[str, ...]] = {
    "phase1": (
        "dir",
        "bindery_report",
        "stamp",
        "apply_lossy",
        "quarantine",
    ),
    "sign": ("manifest",),
    "approve": ("manifest",),
    "phase2": ("manifest", "backup_dir", "audience"),
    "phase3": ("manifest", "answer_file"),
    "convert": ("search", "ids", "to", "from_format", "backup_dir"),
    "polish": ("search", "ids", "polish_ops", "backup_dir"),
    "cover": ("search", "ids", "cover", "remove_cover", "backup_dir"),
    "export": ("search", "ids", "dest", "template"),
    "merge": ("keeper", "duplicate", "backup_dir"),
    "flush": ("search", "ids", "chunk", "backup_dir"),
    "backfill": ("search", "ids", "fields", "backup_dir"),
    "trash": ("empty", "expire"),
    "backup-metadata": ("all", "backup_dir"),
    "restore-database": ("target", "force"),
    "clone": ("target",),
    "fts-index": ("enable", "fts_status"),
    "catalog-epub": ("dest",),
    "catalog-bibtex": ("dest",),
    "customize": (
        "list_plugins",
        "add_plugin",
        "remove_plugin",
        "enable_plugin",
        "disable_plugin",
    ),
    "debug-tools": (
        "explode",
        "implode",
        "diff",
        "kepubify",
        "un_kepubify",
        "inspect_mobi",
    ),
    "device": (
        "device_ls",
        "device_cat",
        "device_df",
        "device_books",
        "device_mkdir",
        "device_rm",
        "device_touch",
        "device_cp",
    ),
    "news": ("recipe", "all", "list", "dest", "timeout", "force"),
}

# Per-verb prose: what it does, what it needs, what it writes. The guard
# vocabulary is the house one: dry-run plans by default; --apply needs
# Calibre closed; mutating verbs need --backup-dir outside the library;
# usage problems exit 2, an open Calibre exits 1.
VERB_PROSE: dict[str, str] = {
    "phase1": (
        "Vet a downloads directory into a review manifest ({date}-batch.json,\n"
        "written under the library's .claude/manifests/ directory; the run\n"
        "prints the path): bindery repairs (consent-gated when lossy), the\n"
        "DRM audit, the duplicate screen, filename stamps, provenance seeds.\n"
        "metadata.db is only read. Sign that printed path with `run sign`.\n"
        "This verb takes no --manifest: the output path is derived, never\n"
        "supplied. The file-side writes are separate opt-ins: --stamp drives\n"
        "stamp_pdf, --apply-lossy takes bindery's gated lossy repairs, and\n"
        "--quarantine MOVES true DRM hits into _quarantine/ (without it the\n"
        "verdict is recorded and the file stays put)."
    ),
    "sign": (
        "Seal the reviewed manifest for phase 2: structure checks (no seal\n"
        "check, so re-signing after a deliberate edit works), then an HMAC\n"
        "seal over the approved set, stamps, provenance, lossy flags, and\n"
        "decisions. Sign via `run sign --manifest FILE`, never by hand."
    ),
    "approve": (
        "Re-derive the approved set from the per-file verdicts: the sanctioned\n"
        "propagation when a reviewer flips a verdict. The derived set must\n"
        "match what the verdicts imply in both directions or the load is\n"
        "refused."
    ),
    "phase2": (
        "Import the SIGNED manifest as ONE batch() transaction: bindery\n"
        "consent first, then add_book per approved file with the #source and\n"
        "#audience stamps (--audience, default: the constant house default).\n"
        "Requires\n"
        "--backup-dir (outside the library) and Calibre closed; metadata\n"
        "downloads defer if Calibre opens mid-run. A failure rolls back the\n"
        "whole import."
    ),
    "phase3": (
        "Curate the freshly imported books (tags, comments, fixes) from\n"
        "--answer-file JSON or TTY prompts (unattended runs always pass\n"
        "--answer-file; the prompts need a terminal), then drive the\n"
        "mechanical pass (bindery + reconcile). Re-validates to 0 errors or\n"
        "exits 1 (curation rolls back; a mechanical-pass failure can leave\n"
        "applied file-side work behind, reported in the batch record)."
    ),
    "convert": (
        "Convert the targeted books to --to FORMAT with ebook-convert\n"
        "(--from-format overrides the default largest-other-format source).\n"
        "Books already carrying the target format are skipped at plan time.\n"
        "--apply needs Calibre closed and --backup-dir."
    ),
    "polish": (
        "Run ebook-polish on the targeted EPUBs (--polish-ops: comma list of\n"
        "smarten,unused-css,compress-images,subset-fonts,jacket,kepubify).\n"
        "--apply needs Calibre closed and --backup-dir."
    ),
    "cover": (
        "Set --cover FILE on every targeted book, or remove covers with\n"
        "--remove-cover. --apply needs Calibre closed and --backup-dir."
    ),
    "export": (
        "Copy the targeted books' format files out with calibredb export into\n"
        "--dest DIR (--template is calibredb's save template). The library\n"
        "itself is never written and no backup is taken, but --apply still\n"
        "observes the house closed-Calibre guard (exit 1 while Calibre runs)."
    ),
    "merge": (
        "Fold --duplicate ID into --keeper ID: the keeper gains every format\n"
        "it lacks from the duplicate (nothing else carries over -- the\n"
        "duplicate's tags, identifiers, and comments are discarded), and the\n"
        "duplicate's files move to .caltrash, never deleted. --apply needs\n"
        "Calibre closed and --backup-dir."
    ),
    "flush": (
        "Embed current database metadata into every book in the OPF-dirty\n"
        "queue (the default target; --search/--ids are optional filters on\n"
        "that queue) via calibredb embed_metadata, chunked (--chunk, default\n"
        "50, ids sent space-separated: a hyphen range would embed every book\n"
        "between the endpoints). The EPUB dc:date normalization rides along.\n"
        "--apply needs Calibre closed and --backup-dir; the queue is re-read\n"
        "afterwards and any remainder reported."
    ),
    "backfill": (
        "Fetch missing metadata (--fields: comma list of\n"
        "title,authors,publisher,isbn) for the targeted books and apply it to\n"
        "the database at --apply. Network-touching like phase2's download\n"
        "segment. --apply needs Calibre closed and --backup-dir."
    ),
    "trash": (
        "The .caltrash lifecycle: the dry run lists entries (category, book\n"
        "id, age, files); --empty with --apply permanently deletes EVERYTHING\n"
        "in .caltrash; --expire DAYS with --apply deletes entries older than\n"
        "DAYS (without the flag, nothing is deleted: --apply just lists).\n"
        "metadata.db never changes: no backup. --apply still observes the\n"
        "house closed-Calibre guard (exit 1 while Calibre runs)."
    ),
    "backup-metadata": (
        "Regenerate the sidecar metadata.opf files over the dirtied queue\n"
        "(--all: every book). --apply needs Calibre closed and --backup-dir."
    ),
    "restore-database": (
        "Rebuild a metadata.db from the library's stored OPFs into --target\n"
        "DIR: this verb CREATES a database, so --target is required and the\n"
        "destination is never guessed. An existing metadata.db there is\n"
        "replaced only under --force (upstream keeps it as\n"
        "metadata_pre_restore.db). No --backup-dir: the source library is\n"
        "only read."
    ),
    "clone": (
        "Create a fresh-schema EMPTY library at --target DIR (schema and\n"
        "custom columns only, no books). The target must not exist or must\n"
        "be empty, and never collides with the source. The source is only\n"
        "read."
    ),
    "fts-index": (
        "Drive Calibre's full-text extractor over the dirtied queue\n"
        "(--fts-status reports Calibre's own indexing state; --enable turns\n"
        "FTS indexing on for the library, a preference write backed up at\n"
        "--apply). Note the upstream seam: reindex only QUEUES extraction;\n"
        "headless completion needs Calibre's --wait-for-completion or\n"
        "Calibre's next start."
    ),
    "catalog-epub": (
        "Write an EPUB catalog of the library (or a --search/--ids subset) to\n"
        "--dest FILE: calibredb catalog through its EPUB_MOBI plugin. The\n"
        "extension is enforced (a wrong one would silently fall back to the\n"
        "EPUB plugin). Read-only: no backup."
    ),
    "catalog-bibtex": (
        "Write a BIBTEX catalog to --dest FILE (.bib enforced). Upstream's\n"
        "plugin silently omits any book missing title, authors, publisher,\n"
        "or pubdate. Read-only: no backup."
    ),
    "customize": (
        "Calibre plugin management without the GUI: --list-plugins (read\n"
        "only), --add-plugin ZIP, --remove-plugin NAME (builtins unaffected),\n"
        "--enable-plugin / --disable-plugin NAME. Needs no library. Installs\n"
        "and changes happen at --apply and modify your REAL Calibre config\n"
        "(there is no sandbox; Calibre must be closed)."
    ),
    "debug-tools": (
        "Calibre's file-level debugging pair-tools, no library needed:\n"
        "--explode FILE DIR (book into editable parts), --implode DIR FILE\n"
        "(rebuild), --diff OLD NEW, --kepubify / --un-kepubify FILES, and\n"
        "--inspect-mobi FILES. Existence rules are per action: explode checks\n"
        "its FILE, implode its DIR; the other operand is the output, created\n"
        "on demand."
    ),
    "device": (
        "Talk to a connected ebook reader: --device-ls / --device-cat PATH,\n"
        "--device-df, --device-books, and the write verbs --device-mkdir /\n"
        "--device-rm / --device-touch PATH and --device-cp SRC DST (dry-run\n"
        "by default; --apply executes). Needs no library. Without hardware\n"
        "the upstream no-device answer is surfaced as-is."
    ),
    "news": (
        "Fetch news editions through Calibre's builtin recipes\n"
        '(ebook-convert "<Recipe Title>.recipe" out.epub), into --dest DIR\n'
        "(default ./news/<date>/; one <title>.epub per recipe). The default\n"
        "batch is a CURATED SUBSET of free, subscription-free sources, not\n"
        "--all: 1,099 recipes would be hours of fetches against sites that\n"
        "actively block automation. --recipe TITLE (exact; repeatable)\n"
        "overrides; --all floods deliberately; --list prints every title.\n"
        "ANTI-BOT CAVEATS: news sites treat scripted fetchers as bots;\n"
        "upstream fights that war for you (recipes carry browser\n"
        "user-agents and calibre refreshes recipe sources from its own\n"
        "server at fetch time), but expect SOME recipes to fail anyway\n"
        "(bot walls, paywalls, redesigns). Failures are isolated: one dead\n"
        "recipe is one report row (its partial output is deleted), the\n"
        "batch continues, exit 1 iff anything failed. Fetches run\n"
        "sequentially: no hammering. Re-running the same command skips the\n"
        "editions already on disk (--force re-fetches). Recipes are\n"
        "addressed by exact title; the handful of titles calibre ships\n"
        "more than once (Deutsche Welle, The Economist, ...) are refused\n"
        "(use Calibre's GUI scheduler for those). Needs no library; no\n"
        "database, no backup, no closed-Calibre guard. The fetched EPUBs\n"
        "are NOT imported: vet them like any downloads dir with\n"
        "`run phase1 <dest>`.\n"
        "Network at --apply only (plus the recipe-source refresh calibre\n"
        "itself performs). --timeout SECONDS bounds each fetch (default\n"
        "1200: a full edition can be a 100+ MB download)."
    ),
}


def _parser():
    from cquarry_cli.cli import build_parser

    return build_parser()


def _groups(parser):
    return parser._action_groups


def _group_by_title(parser, title):
    for g in _groups(parser):
        if g.title == title:
            return g
    return None


def _flag_token(action):
    if action.option_strings:
        token = "/".join(action.option_strings)
    else:
        token = action.dest
    if action.nargs == 0:
        return token
    met = action.metavar
    if met is None:
        if action.choices:
            met = "{" + ",".join(str(c) for c in action.choices) + "}"
        else:
            met = action.dest.upper()
    if isinstance(met, tuple):
        met = " ".join(met)
    elif action.nargs == "?":
        met = f"[{met}]"
    elif isinstance(action.nargs, int) and action.nargs > 1:
        met = " ".join([met] * action.nargs)
    elif action.nargs in ("+", "*"):
        met = f"{met} ..."
    return f"{token} {met}" if met else token


def _truncate(help_text, limit):
    """One line, word-boundary ellipsis."""
    text = " ".join((help_text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " ..."


def _flag_rows(actions, detail_limit, pad=26, indent=2, width=88):
    """Aligned one-line-per-flag block; tokens longer than pad wrap the
    help underneath instead of breaking the column."""
    lines = []
    for action in actions:
        if action.dest == "help":
            continue
        token = _flag_token(action)
        if len(token) < pad:
            detail = _truncate(action.help, width - indent - pad)
            if not detail:
                lines.append(" " * indent + token)
                continue
            lines.append(" " * indent + token.ljust(pad) + detail)
        else:
            lines.append(" " * indent + token)
            detail = " ".join((action.help or "").split())
            for chunk in textwrap.wrap(detail, width - indent - 4):
                lines.append(" " * (indent + 4) + chunk)
    return lines


def _wrapped_flag_block(actions, indent=2, width=88):
    """Token on its own line when tight, help text wrapped beneath."""
    lines = []
    for action in actions:
        if action.dest == "help":
            continue
        token = _flag_token(action)
        detail = " ".join((action.help or "").split())
        if not detail:
            lines.append(" " * indent + token)
            continue
        if len(token) < 28 and len(token) + indent + 2 + len(detail) <= width:
            lines.append(" " * indent + token.ljust(28) + detail)
        else:
            lines.append(" " * indent + token)
            for chunk in textwrap.wrap(detail, width - indent - 4):
                lines.append(" " * (indent + 4) + chunk)
    return lines


def overview(parser) -> str:
    modes = _group_by_title(parser, MODES_TITLE)
    scoping = _group_by_title(parser, SCOPING_TITLE)
    lines: list[str] = [
        "cquarry - Calibre library toolkit: catalog, stats, audit, export,",
        "the acquisition run pathway, and headless Calibre verbs.",
        "",
        "usage: cquarry [--db DB] [--restrict SEARCH] MODE [mode flags]",
        "       cquarry run VERB [verb flags]",
        "",
    ]
    lines.append(f"{modes.title}:")
    lines.extend(_flag_rows(modes._group_actions, detail_limit=52))
    lines.append("")
    lines.append(f"{scoping.title}:")
    lines.extend(_flag_rows(scoping._group_actions, detail_limit=60))
    lines.append("")
    lines.append("also:")
    lines.append(
        "  write verbs   --set-title, --add-tag, ... per-book edits"
        "            (see: --help write)"
    )
    lines.append(
        "  set writes    --from-search/--ids + --batch-* edits"
        "          (see: --help set)"
    )
    lines.append(
        "  run VERB      the acquisition pathway + headless calibre verbs"
        "   (see: --help run)"
    )
    lines.append("")
    lines.append("also:")
    lines.append("  cquarry (no arguments)        the interactive TUI")
    lines.append("")
    lines.append("deep dives:")
    lines.append(
        "  cquarry --help read|write|set|run|examples|all|json  (-h works too)"
    )
    lines.append("  cquarry --version")
    lines.append("  cquarry run --help VERB        e.g. run --help phase2")
    lines.append("")
    lines.append("example:")
    lines.append(
        '  cquarry --catalog --wing "The Tabletop" --show-tags --db ~/Calibre/metadata.db'
    )
    return "\n".join(lines)


def run_overview() -> str:
    parser = _parser()
    for action in parser._actions:
        if action.dest == "subcommand" and hasattr(action, "choices"):
            break
    lines: list[str] = [
        "cquarry run - the acquisition pathway and the headless Calibre verbs",
        "",
        "usage: cquarry run VERB [verb flags]",
        "",
    ]
    for stage, verbs in RUN_STAGES:
        lines.append(stage + ":")
        for verb, blurb in verbs:
            lines.append("  " + verb.ljust(20) + blurb)
        lines.append("")
    lines.append(
        "shared flags: --apply (library operations execute at --apply; the"
        " acquisition-pathway verbs have no --apply), --db, --format json, --quiet"
    )
    lines.append("")
    lines.append(
        "exit codes: 0 clean; 1 failed apply or a live-Calibre refusal; "
        "2 usage or trouble-found per verb (each page states which)"
    )
    lines.append("")
    lines.append("per-verb detail: cquarry run --help VERB   (e.g. run --help phase1)")
    return "\n".join(lines)


def verb_page(verb: str) -> str:
    parser = _parser()
    run_actions = {}
    for action in parser._actions:
        if action.dest == "subcommand" and hasattr(action, "choices"):
            run_p = action.choices["run"]
            run_actions = {a.dest: a for a in run_p._actions}
    claimed = list(VERB_FLAGS.get(verb, ()))
    lines: list[str] = [f"run {verb}", "", VERB_PROSE[verb].rstrip(), ""]
    if claimed:
        lines.append("flags:")
        for dest in claimed:
            action = run_actions.get(dest)
            if action is None:
                continue
            token = _flag_token(action)
            detail = " ".join((action.help or "").split())
            if len(token) + 6 + len(detail) <= 88:
                lines.append("  " + token.ljust(26) + detail)
            else:
                lines.append("  " + token)
                for chunk in textwrap.wrap(detail, 78):
                    lines.append("      " + chunk)
        lines.append("")
    footer = "shared: " + ", ".join(
        "--apply (execute; dry run without it)" if f == "apply" else f"--{f}"
        for f in shared_flags_for(verb)
    )
    if verb not in APPLY_VERBS:
        footer += " (this verb ignores --apply: it executes as documented)"
    lines.append(footer)
    return "\n".join(lines)


def topic_read(parser) -> str:
    lines = [
        "cquarry read modes - every mode flag, its modifiers, and scoping",
        "",
        "Modes are mutually exclusive: pick exactly one per invocation.",
        "Everything reads metadata.db read-only; nothing here writes.",
        "",
    ]
    for title in (
        MODES_TITLE,
        MODIFIERS_TITLE,
        DISPLAY_TITLE,
        OUTPUT_TITLE,
        SCOPING_TITLE,
    ):
        group = _group_by_title(parser, title)
        if group is None:
            continue
        lines.append(group.title + ":")
        lines.extend(_wrapped_flag_block(group._group_actions))
        lines.append("")
    lines.append(
        "composition notes:\n"
        "  --book --untagged          select every untagged book\n"
        "  --health --fail-on-findings  turn the dashboard into a gate (exit 1)\n"
        "  --analytics genres --genre-depth N   deeper tag-hierarchy levels\n"
        "  --restrict SEARCH          scopes every read mode except --trash\n"
        "                             (library-shape); refused with write verbs,\n"
        "                             the run verbs, --book/--id, and bare --untagged\n"
        "  two modes in one invocation  a usage error (exit 2); the error\n"
        "                             names the flags involved (a bare --book\n"
        "                             alongside another mode is the one silent\n"
        "                             case: it contributes no id list)\n"
        "exit codes: 0 clean, 1 findings (mode-dependent), 2 usage."
    )
    return "\n".join(lines)


def topic_write(parser) -> str:
    group = _group_by_title(parser, WRITE_TITLE)
    lines = [
        "cquarry write verbs - per-book edits, one flag per edit",
        "",
        "Opt-in and dispatched only when a write flag is present. THESE VERBS",
        "COMMIT IMMEDIATELY: no dry run, no --apply, no backup (only",
        "--remove-book gates, behind --confirm-remove). Calibre must be",
        "closed (a lock is exit 1; a usage problem is exit 2). Every edit",
        "funnels through one batch() transaction per invocation, so an",
        "interrupt mid-verb cannot commit a torn edit. (Empty-string values",
        "are refused on the set and schema doors, not here.) Combine freely:",
        "--set-title + --add-tag in one call; --remove-book cannot combine",
        "with any other verb.",
        "",
        group.title + ":",
    ]
    lines.extend(_wrapped_flag_block(group._group_actions))
    return "\n".join(lines)


def topic_set(parser) -> str:
    group = _group_by_title(parser, SET_TITLE)
    lines = [
        "cquarry set writes - one target set, id-less --batch-* verbs",
        "",
        "Pick exactly one target source (--ids, --from-search,",
        "--from-untagged, --from-manifest), then any number of --batch-*",
        "verbs. Dry run by default: the plan prints per book and verb.",
        "--apply demands --backup-dir (outside the library) and Calibre",
        "closed, then commits ONE transaction: any per-(book, verb) failure",
        "rolls the whole pass back (exit 1, committed: false).",
        "--commit-per-book relaxes that to one transaction per book.",
        "",
        group.title + ":",
    ]
    lines.extend(_wrapped_flag_block(group._group_actions))
    return "\n".join(lines)


def topic_run() -> str:
    return run_overview()


def topic_examples() -> str:
    return "\n".join(
        [
            "cquarry examples",
            "",
            "# Catalog a wing, tags instead of ratings, ids for scripting",
            '  cquarry --catalog --wing "The Tabletop" --show-tags --show-id \\',
            "      --db ~/Calibre/metadata.db",
            "",
            "# One catalog per virtual library, into a directory",
            "  cquarry --all-wings --db ~/Calibre --outdir ~/docs/catalogs",
            "",
            "# Search + export JSON",
            '  cquarry --search "tags:Fic.SciFi and rating:>=4" --format json \\',
            "      --output scifi.json --db ~/Calibre/metadata.db",
            "",
            "# Health digest as a gate",
            "  cquarry --health --fail-on-findings --db ~/Calibre/metadata.db",
            "",
            "# A scoped audit",
            "  cquarry --audit --restrict 'vl:Unsorted' --output audit.csv \\",
            "      --db ~/Calibre/metadata.db",
            "",
            "# Full-text search over the books' actual text",
            '  cquarry --fts "dread empire" --db ~/Calibre/metadata.db',
            "",
            "# Per-book dossier",
            "  cquarry --book 6688 --db ~/Calibre/metadata.db",
            "",
            "# A batch edit over a search, dry run first",
            "  cquarry --from-search 'tags:\"To Sort\"' --batch-add-tag Curated \\",
            "      --db ~/Calibre/metadata.db",
            "",
            "# The acquisition pathway (phase1 prints its manifest path; sign THAT)",
            "  cquarry run phase1 ~/Downloads",
            "  cquarry run sign --manifest <library>/.claude/manifests/2026-10-06-batch.json",
            "  cquarry run phase2 --manifest <same-path> --backup-dir ~/bak",
            "  cquarry run phase3 --manifest <same-path> --answer-file a.json",
            "",
            "# Headless calibre verbs",
            "  cquarry run flush --apply --backup-dir ~/bak --db ~/Calibre/metadata.db",
            "  cquarry run fts-index --fts-status --db ~/Calibre/metadata.db",
        ]
    )


def topic_all(parser) -> str:
    lines = ["cquarry - the complete flag reference", ""]
    for group in _groups(parser):
        if not group.title:
            continue
        lines.append(group.title + ":")
        lines.extend(_wrapped_flag_block(group._group_actions))
        lines.append("")
    lines.append(run_overview())
    return "\n".join(lines)


def topic_json() -> str:
    """The complete parser surface as machine-readable JSON, generated from
    the live parser (the same anti-rot principle as the flag tables: a new
    flag lands here by construction). Painted NEVER: the output must stay
    valid JSON even under FORCE_COLOR. Shape: groups (every top-level
    argument group with its flags), run.verbs (per verb: stage, summary,
    prose, the flags it consumes, the shared flags, and whether it takes
    --apply), run.apply_verbs, topics, usage."""
    parser = _parser()
    run_p = next(
        action.choices["run"]
        for action in parser._actions
        if action.dest == "subcommand"
    )
    run_actions = {action.dest: action for action in run_p._actions}

    def flag_entry(action):
        entry = {
            "flags": list(action.option_strings) or [action.dest],
            "dest": action.dest,
            "help": " ".join((action.help or "").split()),
        }
        if action.metavar:
            met = action.metavar
            entry["metavar"] = " ".join(met) if isinstance(met, tuple) else met
        if action.choices:
            entry["choices"] = [str(c) for c in action.choices]
        if action.nargs not in (None, 0):
            entry["nargs"] = str(action.nargs)
        return entry

    groups = []
    for group in _groups(parser):
        if not group.title:
            continue
        groups.append(
            {
                "title": group.title,
                "description": group.description or "",
                "flags": [
                    flag_entry(a) for a in group._group_actions if a.dest != "help"
                ],
            }
        )
    verbs = {}
    for stage, stage_verbs in RUN_STAGES:
        for verb, blurb in stage_verbs:
            verbs[verb] = {
                "stage": stage,
                "summary": blurb,
                "prose": " ".join(VERB_PROSE[verb].split()),
                "flags": [
                    flag_entry(run_actions[dest])
                    for dest in VERB_FLAGS[verb]
                    if dest in run_actions
                ],
                "shared": ["--" + f for f in shared_flags_for(verb)],
                "takes_apply": verb in APPLY_VERBS,
            }
    payload = {
        "tool": "cquarry",
        "version": VERSION,
        "usage": (
            "cquarry [--db DB] [--restrict SEARCH] MODE [mode flags]"
            " | cquarry run VERB [verb flags]"
        ),
        "topics": list(TOPICS),
        "groups": groups,
        "run": {
            "verbs": verbs,
            "apply_verbs": sorted(APPLY_VERBS),
        },
        "help": "cquarry --help TOPIC | cquarry run --help VERB",
    }
    return json.dumps(payload, indent=1, sort_keys=True)


def render_topic(topic: str) -> str:
    parser = _parser()
    if topic == "read":
        return topic_read(parser)
    if topic == "write":
        return topic_write(parser)
    if topic == "set":
        return topic_set(parser)
    if topic == "run":
        return topic_run()
    if topic == "examples":
        return topic_examples()
    if topic == "all":
        return topic_all(parser)
    if topic == "json":
        return topic_json()
    raise ValueError(f"unknown topic {topic!r}")


def _unknown_topic(topic: str) -> str:
    return (
        f"ERROR: unknown help topic {topic!r}.\n"
        f"topics: {', '.join(TOPICS)}\n"
        "per-verb run pages: cquarry run --help VERB"
    )


def _unknown_verb(verb: str) -> str:
    return (
        f"ERROR: unknown run verb {verb!r}.\n"
        f"verbs: {', '.join(RUN_VERBS)}\n"
        "overview: cquarry run --help"
    )


def handle_help(argv: list[str]) -> tuple[int, str] | None:
    """The pre-parse help intercept. Returns (exit code, text) when this
    request is help's to answer, None to fall through to argparse (bare
    --help/-h lands on the parser's custom overview action)."""
    if not argv:
        return None
    if "run" in argv:
        run_i = argv.index("run")
        rest = argv[run_i + 1 :]
        for h in ("--help", "-h"):
            if h in rest:
                hi = rest.index(h)
                if hi + 1 < len(rest) and not rest[hi + 1].startswith("-"):
                    candidate = rest[hi + 1]
                    if candidate in RUN_VERBS:
                        return 0, paint(verb_page(candidate))
                    return 2, _unknown_verb(candidate)
                for token in rest[:hi]:
                    if not token.startswith("-") and token in RUN_VERBS:
                        return 0, paint(verb_page(token))
                return 0, paint(run_overview())
    for h in ("--help", "-h"):
        if h in argv:
            i = argv.index(h)
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                topic = argv[i + 1]
                if topic in TOPICS:
                    text = render_topic(topic)
                    if topic != "json":  # JSON must stay valid under FORCE_COLOR
                        text = paint(text)
                    return 0, text
                return 2, _unknown_topic(topic)
            return None
    return None
