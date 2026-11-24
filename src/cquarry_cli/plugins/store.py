"""The plugin registry and the approvals ledger.

Two small inspectable JSON stores beside the engine's config
(``~/.config/cquarry/``): the registry (sources + installed plugins) and the
approvals ledger (the recorded approvals that gate write-capable plugins).
Not SQLite: dozens of rows, human-diffable. The base directory moves with
``CQUARRY_PLUGIN_HOME`` so the suite runs hermetic.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_SCHEMA = 1
APPROVALS_SCHEMA = 1


def plugin_home() -> Path:
    """The base directory for the registry, approvals, and clone cache."""
    override = os.environ.get("CQUARRY_PLUGIN_HOME")
    if override:
        return Path(override)
    from cquarry.config import CONFIG_FILE

    return Path(CONFIG_FILE).parent


def registry_path() -> Path:
    return plugin_home() / "plugins.json"


def approvals_path() -> Path:
    return plugin_home() / "plugin-approvals.json"


def cache_root() -> Path:
    return plugin_home() / "cache"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, data: dict) -> None:
    """Atomic replace: a torn registry or approvals file must never exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


@dataclass
class Registry:
    sources: list[dict]
    plugins: dict[str, dict]

    @classmethod
    def load(cls) -> "Registry":
        data = _read_json(
            registry_path(), {"schema": REGISTRY_SCHEMA, "sources": [], "plugins": {}}
        )
        return cls(sources=data.get("sources", []), plugins=data.get("plugins", {}))

    def save(self) -> None:
        _write_json(
            registry_path(),
            {
                "schema": REGISTRY_SCHEMA,
                "sources": self.sources,
                "plugins": self.plugins,
            },
        )

    def source(self, slug: str) -> dict | None:
        for entry in self.sources:
            if entry["slug"] == slug:
                return entry
        return None

    def add_source(self, slug: str, url: str, kind: str, path: str) -> dict:
        entry = {
            "slug": slug,
            "url": url,
            "kind": kind,
            "path": path,
            "last_commit": "",
            "added": _now(),
        }
        self.sources.append(entry)
        return entry

    def drop_source(self, slug: str) -> int:
        """Forget a source and every plugin it provides; returns the count."""
        before = len(self.plugins)
        self.sources = [s for s in self.sources if s["slug"] != slug]
        self.plugins = {n: p for n, p in self.plugins.items() if p["source"] != slug}
        return before - len(self.plugins)


@dataclass
class Approvals:
    entries: dict[str, list[dict]]

    @classmethod
    def load(cls) -> "Approvals":
        data = _read_json(
            approvals_path(), {"schema": APPROVALS_SCHEMA, "approvals": {}}
        )
        return cls(entries=data.get("approvals", {}))

    def save(self) -> None:
        _write_json(
            approvals_path(), {"schema": APPROVALS_SCHEMA, "approvals": self.entries}
        )

    def record(
        self, name: str, version: str, capabilities: list[str], payload_hash: str
    ) -> dict:
        """Append one approval; the entry stays as history even after removal."""
        entry = {
            "version": version,
            "capabilities": capabilities,
            "payload_sha256": payload_hash,
            "approved_at": _now(),
        }
        self.entries.setdefault(name, []).append(entry)
        return entry

    def active(self, name: str) -> dict | None:
        history = self.entries.get(name)
        return history[-1] if history else None

    def approves(self, name: str, payload_hash: str) -> bool:
        """True only when the ACTIVE approval carries this exact payload hash.

        Any payload, version, or capability change re-gates: the hash is the
        update-safety mechanism, and no flag bypasses it.
        """
        active = self.active(name)
        return bool(active and active.get("payload_sha256") == payload_hash)
