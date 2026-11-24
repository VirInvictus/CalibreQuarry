"""The capability gates: what a loaded plugin may touch.

``read`` passes the host read API: the opened read-only CalibreDB, the
library root, and a book-id to format-paths resolver. ``write`` passes ONLY
the WriteGate: the sanctioned cquarry.write seams behind the standing rails
(dry-run default, out-of-tree backups, the closed-Calibre guard,
FORBIDDEN_COLUMNS refused by label), via the same ``writeops`` actions the
CLI's own verbs ride -- the seams are the only write path, enforced
host-side, so an honest or dishonest plugin cannot skip them. ``network``
is declared-and-logged: every fetch a plugin makes through the gate lands
in the run log.

This is a procedural trust boundary, not a security sandbox: Python plugins
load in-process. The gates make accidents hard and records complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cquarry_cli import writeops


@dataclass
class PluginContext:
    """What register() and every plugin verb receive."""

    plugin_name: str
    capabilities: tuple[str, ...]
    db_path: str | None = None
    dry_run: bool = True
    log_lines: list[str] = field(default_factory=list)
    _db: object = None

    @property
    def can_read(self) -> bool:
        return "read" in self.capabilities

    @property
    def can_write(self) -> bool:
        return not self.dry_run and "write" in self.capabilities

    def log(self, message: str) -> None:
        self.log_lines.append(message)

    # -- the read gate -------------------------------------------------

    @property
    def db(self):
        """The opened read-only CalibreDB; raises without the read capability."""
        if not self.can_read:
            raise PermissionError(
                f"{self.plugin_name}: the 'read' capability is not granted"
            )
        if self._db is None:
            from cquarry.db import CalibreDB

            if self.db_path is None:
                raise RuntimeError("no library bound to this context")
            self._db = CalibreDB(self.db_path)
        return self._db

    def format_paths(self, book_id: int) -> dict[str, Path]:
        """Book id -> {fmt: path}; the layout paths from the engine's index.

        Paths follow Calibre's storage layout from the original DB location
        (the engine's own ``get_formats`` semantics; unverified against disk).
        """
        return {
            fmt: Path(detail["path"])
            for fmt, detail in self.db.get_formats(book_id).items()
            if detail.get("path")
        }

    # -- the config seam -------------------------------------------------

    def config_path(self) -> Path:
        """This plugin's persistent config file (per-plugin dir, created now)."""
        from cquarry_cli.plugins.store import plugin_config_dir

        directory = plugin_config_dir(self.plugin_name)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "config.json"

    def load_config(self) -> dict:
        from cquarry_cli.plugins.store import load_json

        return load_json(self.config_path(), {})

    def save_config(self, data: dict) -> None:
        from cquarry_cli.plugins.store import save_json

        save_json(self.config_path(), data)

    # -- the write gate -------------------------------------------------

    @property
    def write(self) -> "WriteGate":
        if not self.can_write:
            if "write" not in self.capabilities:
                raise PermissionError(
                    f"{self.plugin_name}: the 'write' capability is not granted"
                )
            raise PermissionError(
                f"{self.plugin_name}: writes are dry-run; the verb must run with --apply"
            )
        return _WRITE_GATE


class _NetworkLog:
    """The declared-network log: every fetch a plugin makes lands here."""

    def __init__(self, context: PluginContext) -> None:
        self._context = context

    def fetch(self, url: str, *, method: str = "GET") -> str:
        if "network" not in self._context.capabilities:
            raise PermissionError(
                f"{self._context.plugin_name}: the 'network' capability is not declared"
            )
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[network] {stamp} {method} {url}"
        self._context.log(line)
        import urllib.request

        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (declared plugin fetch)
            return resp.read().decode("utf-8", errors="replace")


class WriteGate:
    """The only write path a plugin has: writeops actions, full rails.

    Each method maps to the CLI's own action factory, so a plugin write is
    byte-for-byte the same seam a human verb writes through: dry-run
    planning, mandatory backups, the closed-Calibre guard, one batch per
    pass, and the NON-NEGOTIABLES refusal are all inherited.
    """

    def plan_identifier(
        self, book_id: int, id_type: str, value: str
    ) -> tuple[str, object]:
        """The (label, action) pair run_write_batch consumes; the WriteGate
        owns the label because the gate prints the batch results."""
        action = writeops.action_set_identifier(book_id, id_type, value)
        return f"set identifier {id_type}={value} on book {book_id}", action

    def plan_add_format(
        self, book_id: int, fmt: str, name: str, size: int
    ) -> tuple[str, object]:
        """Register a format row (the FILE is the plugin's responsibility:
        copy it into the keeper's directory before the batch runs)."""
        action = writeops.action_add_format(book_id, fmt, name, size)
        return f"add format {fmt} ({name}) on book {book_id}", action

    def plan_remove_format(self, book_id: int, fmt: str) -> tuple[str, object]:
        action = writeops.action_remove_format(book_id, fmt)
        return f"remove format {fmt} from book {book_id}", action

    def plan_rename_entity(self, kind: str, old: str, new: str) -> tuple[str, object]:
        action = writeops.action_rename_entity(kind, old, new)
        return f"rename {kind} {old!r} -> {new!r}", action

    def plan_remove_book(self, book_id: int) -> tuple[str, object]:
        """Remove a book row. The gate forces the confirm flag: the layers
        above it (the plugin approval, the dry-run default, the apply
        refusal on a re-gated payload) ARE the confirmation, so no plugin
        code ever passes one by hand."""
        action = writeops.action_remove_book(book_id, confirm=True)
        return f"remove book {book_id}", action

    def apply(self, db_path: str, actions: list, *, quiet: bool = False) -> int:
        return writeops.run_write_batch(db_path, actions, quiet=quiet)


_WRITE_GATE = WriteGate()
