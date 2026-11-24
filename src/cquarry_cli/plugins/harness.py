"""The verify-and-approve harness (Brandon's ruling, verbatim in the roadmap):

the engine runs a test run -- custom-written so the operator tests what
they want -- and asks approval before a writing plugin is added.
``plugin verify <name>`` runs the manifest's declared probes (and/or an
operator-supplied spec) in dry-run mode against a scratch library, renders
the report, and records the approval keyed by payload sha256; any payload,
version, or capability change re-gates. Read-only plugins install without
the prompt; write-capable ones cannot be added, updated, or re-enabled
without a recorded approval.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from cquarry_cli.plugins.gates import PluginContext
from cquarry_cli.plugins.manifest import (
    FloorError,
    Manifest,
    check_floors,
    load_manifest,
)
from cquarry_cli.plugins.scratch import build_scratch


def payload_hash(plugin_dir: Path) -> str:
    """SHA-256 over the plugin directory's tracked content, order-stable.

    Every file's relative path and bytes feed the digest, sorted, so any
    payload change re-gates and an unchanged payload keeps its approval.
    """
    digest = hashlib.sha256()
    for path in sorted(Path(plugin_dir).rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in (".pyc", ".pyo")
        ):
            rel = path.relative_to(plugin_dir).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def load_plugin(manifest: Manifest, context: PluginContext):
    """Import the entry module from the plugin dir and call register(ctx).

    The plugin dir goes on sys.path for the duration only; register() may
    return a dict of verb handlers ({name: callable(context, argv) -> int})
    or None. Import errors and register errors propagate -- verify surfaces
    them, install treats them as a refused load.
    """
    module_name, _, function_name = manifest.entry.partition(":")
    module_file = manifest.dir.joinpath(*module_name.split("."))
    if module_file.suffix != ".py":
        module_file = module_file.with_suffix(".py")
    if not module_file.is_file():
        raise ImportError(f"entry module file not found: {module_file}")
    # A unique per-plugin module name: two plugins may ship the same module
    # basename, and a stale sys.modules entry must never bind one plugin's
    # load to another's file (the cross-test ENOENT class).
    spec = importlib.util.spec_from_file_location(
        f"cquarry_plugin_{manifest.name}_{module_name}", module_file
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build a loader for {module_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    register = getattr(module, function_name, None)
    if register is None:
        raise ImportError(
            f"entry function {function_name!r} not found in module {module_name!r}"
        )
    verbs = register(context)
    return module, register, (verbs or {})


class VerifyReport:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ok = True

    def good(self, line: str) -> None:
        self.lines.append(f"  ok: {line}")

    def bad(self, line: str) -> None:
        self.ok = False
        self.lines.append(f"  FAIL: {line}")

    def text(self, header: str) -> str:
        return "\n".join([header, *self.lines])


def _manifest_probes(manifest: Manifest) -> list[str]:
    """The manifest's optional [verify] probes: argv tails the host dry-runs."""
    from cquarry_cli.plugins.manifest import MANIFEST_NAME

    try:
        with (manifest.dir / MANIFEST_NAME).open("rb") as fh:
            import tomllib

            data = tomllib.load(fh)
    except OSError, tomllib.TOMLDecodeError:
        return []
    probes = data.get("verify", {}).get("probes", [])
    return [str(p) for p in probes] if isinstance(probes, list) else []


def _spec_probes(spec_path: Path | None) -> list[str]:
    """The operator's custom test spec: JSON {'probes': [argv, ...]}."""
    if spec_path is None:
        return []
    with spec_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    probes = data.get("probes", [])
    if not isinstance(probes, list):
        raise ValueError(f"{spec_path}: 'probes' must be a list of argv strings")
    return [str(p) for p in probes]


def run_probe(
    probe: str, manifest: Manifest, scratch_db: Path
) -> tuple[int, list[str]]:
    """One probe = one full dry-run of ``plugin run <name> <argv...>``.

    Fresh scratch library, fresh context, fresh module namespace each probe:
    a probe cannot leave state behind for the next one.
    """
    from cquarry_cli.plugins import verbs

    argv = probe.split()
    context = PluginContext(
        plugin_name=manifest.name,
        capabilities=manifest.capabilities,
        db_path=str(scratch_db),
        dry_run=True,
    )
    code = verbs.run_plugin_verb(manifest, context, argv)
    return code, context.log_lines


def verify(
    plugin_dir: Path,
    *,
    spec_path: Path | None = None,
    scratch_root: Path | None = None,
) -> tuple[VerifyReport, Manifest | None, str]:
    """The full verify pass; returns (report, manifest|None if loadable, payload hash).

    Stages: manifest structure, floor gates, payload hash, load + register,
    then every probe (manifest-declared plus operator-supplied) against a
    fresh scratch library. A refused stage stops the pass fail-closed.
    """
    from cquarry_cli.plugins.manifest import MANIFEST_NAME

    report = VerifyReport()
    manifest_path = Path(plugin_dir) / MANIFEST_NAME
    digest = payload_hash(plugin_dir)
    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        report.bad(f"manifest refuses to load: {e}")
        return report, None, digest
    report.good(
        f"manifest valid: {manifest.name} {manifest.version}, "
        f"capabilities {'/'.join(manifest.capabilities)}"
    )
    try:
        check_floors(manifest)
    except FloorError as e:
        report.bad(str(e))
        return report, manifest, digest
    report.good(
        f"floors satisfied: engine {manifest.requires_cquarry}, "
        f"host {manifest.requires_calibrequarry}"
    )
    try:
        context = PluginContext(
            plugin_name=manifest.name,
            capabilities=manifest.capabilities,
            db_path=None,
            dry_run=True,
        )
        _module, _register, verbs = load_plugin(manifest, context)
    except Exception as e:
        report.bad(f"load/register refused: {e}")
        return report, manifest, digest
    report.good(
        "load + register ok"
        + (f" ({len(verbs)} verb(s): {', '.join(sorted(verbs))})" if verbs else "")
    )
    probes = _manifest_probes(manifest) + _spec_probes(spec_path)
    if not probes:
        report.good("no probes declared; the load pass is the whole test")
        return report, manifest, digest
    if scratch_root is None:
        import tempfile

        scratch_root = Path(tempfile.mkdtemp(prefix="cquarry-verify-"))
    scratch_db = build_scratch(scratch_root)
    for probe in probes:
        try:
            code, log_lines = run_probe(probe, manifest, scratch_db)
        except Exception as e:
            report.bad(f"probe {probe!r} raised: {e}")
            continue
        if code == 0:
            report.good(
                f"probe {probe!r} exited 0"
                + (f" ({len(log_lines)} log lines)" if log_lines else "")
            )
        else:
            report.bad(f"probe {probe!r} exited {code}")
    return report, manifest, digest
