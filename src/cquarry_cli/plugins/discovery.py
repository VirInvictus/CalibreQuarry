"""Source discovery: find plugin manifests and classify the source.

A directory containing ``cquarry-plugin.toml`` at its root is a SINGLE-plugin
source; several manifests (one per package dir, this shape one level deep)
make it a MULTI-plugin source. Classification is BY DISCOVERY, never by
configuration -- no source metadata needs to say which it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cquarry_cli.plugins.manifest import (
    MANIFEST_NAME,
    Manifest,
    ManifestError,
    load_manifest,
)


@dataclass
class Source:
    root: Path
    shape: str  # "single" | "multi"
    plugins: list[Manifest]

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.plugins]


def _deep_manifests(root: Path) -> list[Manifest]:
    found: list[Manifest] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            manifest = child / MANIFEST_NAME
            if manifest.is_file():
                found.append(load_manifest(manifest))
    return found


def discover(root: Path) -> Source:
    """Scan one source root; ManifestError propagates with its path named."""
    root = Path(root).resolve()
    root_manifest = root / MANIFEST_NAME
    if root_manifest.is_file():
        return Source(root=root, shape="single", plugins=[load_manifest(root_manifest)])
    plugins = _deep_manifests(root)
    if not plugins:
        raise ManifestError(
            f"{root}: no {MANIFEST_NAME} at the root and none one level deep; "
            "a plugin source carries at least one"
        )
    names = [m.name for m in plugins]
    if len(names) != len(set(names)):
        raise ManifestError(
            f"{root}: duplicate plugin names across package dirs: {names}"
        )
    return Source(root=root, shape="multi", plugins=plugins)
