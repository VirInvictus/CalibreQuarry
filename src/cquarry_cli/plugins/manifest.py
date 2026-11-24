"""The plugin manifest contract (Phase 21): parse, validate, and floor-check.

A plugin declares itself in a ``cquarry-plugin.toml`` at its package root
(see the cquarry-plugins spec; the same contract governs both sides). Every
version gate here fails CLOSED naming the version found, the floor required,
and the fix -- the floor_refusal idiom the whole repo uses. The cquarry
floor is judged against the engine the process ACTUALLY imported (the
version_guard.py honest-source rule, never dist metadata that can lag a
checkout); the calibrequarry floor against this package's own version.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "cquarry-plugin.toml"
SCHEMA = 1
CAPABILITIES = frozenset({"read", "write", "network"})
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
FLOOR_RE = re.compile(r"^>=(\d+)(\.(\d+))?(\.(\d+))?$")
ENTRY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


class ManifestError(ValueError):
    """A manifest that refuses to load; the message names the defect."""


class FloorError(ValueError):
    """A version gate the running stack cannot satisfy; names found/required/fix."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    api_version: int
    license: str
    capabilities: tuple[str, ...]
    entry: str
    description: str
    requires_cquarry: str
    requires_calibrequarry: str
    upstream: dict
    path: Path

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def writes(self) -> bool:
        return "write" in self.capabilities

    @property
    def networks(self) -> bool:
        return "network" in self.capabilities


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _floor_tuple(floor: str) -> tuple[int, ...]:
    """'>=1.29.2' -> (1, 29, 2); the floor shape is validated before this runs."""
    return _version_tuple(floor.removeprefix(">="))


def host_version() -> str:
    """This package's version, the calibrequarry side of the floor check."""
    from cquarry_cli import __version__

    return __version__


def engine_version() -> str | None:
    """The cquarry engine version the process actually imported.

    The honest source is the imported module object (``__version__``, or the
    engine config module's VERSION constant); dist metadata is the LAST
    resort because it can lag a checkout. ``None`` means the engine is not
    importable at all, and every cquarry floor then refuses fail-closed.
    """
    try:
        import cquarry
    except ImportError:
        return None
    version = getattr(cquarry, "__version__", None)
    if version:
        return str(version)
    config = getattr(cquarry, "config", None)
    version = getattr(config, "VERSION", None)
    if version:
        return str(version)
    return None


def check_floors(manifest: Manifest) -> None:
    """Raise FloorError unless the running stack satisfies the manifest.

    All three gates (api_version plus the two dependency floors) check here,
    each refusal naming found, floor, and fix. api_version is a contract
    integer: a plugin built for a NEWER host api refuses until the host
    catches up, and a host refusing an older one says so the same way.
    """
    if manifest.api_version > _HOST_API_VERSION:
        raise FloorError(
            f"{manifest.name}: manifest api_version {manifest.api_version} exceeds "
            f"this host's plugin API ({_HOST_API_VERSION}); fix: upgrade calibrequarry"
        )
    engine = engine_version()
    if engine is None:
        raise FloorError(
            f"{manifest.name}: requires {manifest.requires_cquarry} but the cquarry "
            "engine is not importable; fix: pip install --user cquarry"
        )
    if _version_tuple(engine) < _floor_tuple(manifest.requires_cquarry):
        raise FloorError(
            f"{manifest.name}: requires {manifest.requires_cquarry} but the imported "
            f"engine is {engine}; fix: pip install --user --upgrade cquarry"
        )
    host = host_version()
    if _version_tuple(host) < _floor_tuple(manifest.requires_calibrequarry):
        raise FloorError(
            f"{manifest.name}: requires {manifest.requires_calibrequarry} but this "
            f"host is {host}; fix: pip install --user --upgrade calibrequarry"
        )


#: The plugin-API contract this host speaks. It increments when the gate or
#: entry surface changes shape; plugins declaring a higher api_version refuse
#: above. First contract.
_HOST_API_VERSION = 1


def load_manifest(path: Path) -> Manifest:
    """Parse and validate one manifest; raises ManifestError on any defect."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ManifestError(f"{path}: unreadable manifest: {e}") from e
    if data.get("schema") != SCHEMA:
        raise ManifestError(
            f"{path}: manifest schema {data.get('schema')!r} is not {SCHEMA}; "
            "this host refuses unknown schemas fail-closed"
        )
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError(f"{path}: missing or empty 'name'")
    if name != path.parent.name:
        raise ManifestError(
            f"{path}: name {name!r} must equal its directory name {path.parent.name!r}"
        )
    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ManifestError(f"{path}: 'version' must be X.Y.Z, got {version!r}")
    api_version = data.get("api_version")
    if (
        not isinstance(api_version, int)
        or isinstance(api_version, bool)
        or api_version < 1
    ):
        raise ManifestError(f"{path}: 'api_version' must be a positive integer")
    license_text = data.get("license")
    if not isinstance(license_text, str) or not license_text.strip():
        raise ManifestError(f"{path}: missing or empty 'license'")
    capabilities = data.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not set(capabilities).issubset(CAPABILITIES)
    ):
        raise ManifestError(
            f"{path}: 'capabilities' must be a non-empty subset of "
            f"{sorted(CAPABILITIES)}, got {capabilities!r}"
        )
    entry = data.get("entry")
    if not isinstance(entry, str) or not ENTRY_RE.match(entry):
        raise ManifestError(
            f"{path}: 'entry' must be 'module:function' into the plugin dir, "
            f"got {entry!r}"
        )
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ManifestError(f"{path}: missing or empty 'description'")
    requires = data.get("requires")
    if not isinstance(requires, dict):
        raise ManifestError(f"{path}: missing [requires] (the version floors)")
    floors: dict[str, str] = {}
    for key in ("cquarry", "calibrequarry"):
        floor = requires.get(key)
        if not isinstance(floor, str) or not FLOOR_RE.match(floor):
            raise ManifestError(
                f"{path}: requires.{key} must be a '>=X.Y.Z' floor, got {floor!r}"
            )
        floors[key] = floor
    upstream = data.get("upstream")
    if not isinstance(upstream, dict):
        raise ManifestError(f"{path}: missing [upstream] (the provenance block)")
    return Manifest(
        name=name,
        version=version,
        api_version=api_version,
        license=license_text,
        capabilities=tuple(capabilities),
        entry=entry,
        description=description,
        requires_cquarry=floors["cquarry"],
        requires_calibrequarry=floors["calibrequarry"],
        upstream=upstream,
        path=path,
    )
