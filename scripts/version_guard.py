"""Version announcement and floor checks for the companion scripts.

The scripts and the `cquarry` binary are installed through separate
channels: the binary lives in the uv tool's own venv, the scripts import
whatever pip's user site holds. A half-upgraded machine (a step-0 upgrade
that names cquarry/bindery-cli/vir-tui but not calibrequarry) runs a stale
cquarry_cli module against a current binary with nothing on the record,
which is how issue #20 chased a ghost: the binary was 3.64.0, the module
the script imported was 3.63.4, and reconcile's identifier normalization
refused default-namespace EPUBs the newer module handles silently.

announce() prints one stderr line naming the version of every workspace
package a run sits on, so every script self-identifies its toolchain.
imported_version() and floor_refusal() are the stronger check for the
scripts whose behavior depends on module internals that move: refuse at
startup, naming the version found, the floor required, and the fix."""

import importlib.metadata
import sys
from importlib.metadata import PackageNotFoundError

# (module name, distribution name) for the workspace packages. The names
# differ (the frontend ships as `calibrequarry` on PyPI, module
# `cquarry_cli`), and these three are exactly the skew surface: a stale
# pip copy of any of them changes what the scripts do.
WORKSPACE_PACKAGES: tuple[tuple[str, str], ...] = (
    ("cquarry", "cquarry"),
    ("vir_tui", "vir-tui"),
    ("cquarry_cli", "calibrequarry"),
)


def imported_version(module_name: str) -> str | None:
    """The version of the module object this process actually imported,
    read from its own __version__ (the single source of truth cquarry_cli
    carries). Unlike the distribution metadata this answers for a checkout
    fallback too: the fallback's src/ module reports itself, not whatever
    pip has installed. None when the module is not imported or carries no
    readable version."""
    mod = sys.modules.get(module_name)
    version = getattr(mod, "__version__", None)
    return str(version) if version else None


def installed_version(module_name: str) -> str | None:
    """The installed distribution's version via importlib.metadata, without
    importing the package. None when the distribution is absent."""
    dist = dict(WORKSPACE_PACKAGES).get(module_name, module_name)
    try:
        return importlib.metadata.version(dist)
    except PackageNotFoundError:
        return None


def version_tuple(version: str | None) -> tuple[int, ...] | None:
    """'3.63.4' -> (3, 63, 4); None when unparseable, which the floor check
    treats as below-floor (a version nobody can parse proves nothing)."""
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def announce(stream=None) -> None:
    """One line on stderr naming the toolchain this run sits on: the
    actually-imported module version where the script imported the package
    (a checkout fallback reports itself), the installed dist version
    otherwise, `absent` for a package that is not installed at all."""
    stream = stream if stream is not None else sys.stderr
    parts = []
    for module_name, _dist in WORKSPACE_PACKAGES:
        version = imported_version(module_name) or installed_version(module_name)
        parts.append(f"{module_name} {version or 'absent'}")
    print(f"toolchain: {', '.join(parts)}", file=stream)


def floor_refusal(
    found: str | None,
    floor: tuple[int, ...],
    *,
    what: str,
    why: str,
    fix: str,
) -> str | None:
    """The refusal message for a version below the floor a script's behavior
    assumes: names the version found, the floor required, what breaks under
    it, and the fix. None when `found` satisfies the floor. An unreadable
    version refuses too (fail-closed, the integrate verbs' pgrep shape)."""
    found_tuple = version_tuple(found)
    if found_tuple is not None and found_tuple >= floor:
        return None
    required = ".".join(str(part) for part in floor)
    return (
        f"ERROR: {what} is version {found or 'unreadable'}, but this script "
        f"needs {required}+ ({why}). {fix}"
    )
