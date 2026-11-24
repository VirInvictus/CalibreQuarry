"""The plugin verb family: add, list, install, remove, update, verify, run.

Exit contract (helpcli.TOPIC_DETAILS["plugin"]): 0 clean; 1 a refused gate
(an unapproved write plugin, a floor block, a failed verify or probe, a dead
source); 2 usage. Every mutation is reported line by line; the approvals
ledger and the registry are the durable records.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cquarry_cli.plugins import gitops
from cquarry_cli.plugins.discovery import Source, discover
from cquarry_cli.plugins.gates import PluginContext
from cquarry_cli.plugins.harness import payload_hash, verify
from cquarry_cli.plugins.manifest import (
    FloorError,
    Manifest,
    check_floors,
    load_manifest,
)
from cquarry_cli.plugins.store import Approvals, Registry, cache_root


def _err(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def _usage(message: str) -> int:
    _err(message)
    return 2


def _confirm(question: str, yes: bool) -> tuple[bool, str]:
    """The interactive approval; non-interactive runs must pass --yes."""
    if yes:
        return True, ""
    if not sys.stdin.isatty():
        return False, (
            "non-interactive input: review the report above and re-run with "
            "--yes to record the approval"
        )
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in ("y", "yes"), ""


def _registry_payload(
    manifest: Manifest, digest: str, source_slug: str, plugin_dir: Path
) -> dict:
    from datetime import datetime, timezone

    return {
        "source": source_slug,
        "dir": str(plugin_dir),
        "version": manifest.version,
        "api_version": manifest.api_version,
        "capabilities": list(manifest.capabilities),
        "payload_sha256": digest,
        "installed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _gate_and_install(
    registry: Registry,
    approvals: Approvals,
    source_slug: str,
    manifest: Manifest,
    plugin_dir: Path,
    *,
    yes: bool,
) -> tuple[int, str]:
    """Floor gates + the write approval gate + the registry write."""
    try:
        check_floors(manifest)
    except FloorError as e:
        return 1, f"BLOCKED: {e}"
    digest = payload_hash(plugin_dir)
    if manifest.writes and not approvals.approves(manifest.name, digest):
        report, _manifest, _digest = verify(plugin_dir)
        print(
            report.text(f"verify: {manifest.name} (write-capable; the approval gate)")
        )
        if not report.ok:
            return 1, f"refused: verify failed for {manifest.name}"
        ok, reason = _confirm(
            f"approve {manifest.name} {manifest.version} (payload {digest[:12]})?", yes
        )
        if not ok:
            return 1, f"not approved: {manifest.name} stays uninstalled ({reason})"
        approvals.record(
            manifest.name, manifest.version, list(manifest.capabilities), digest
        )
        approvals.save()
    registry.plugins[manifest.name] = _registry_payload(
        manifest, digest, source_slug, plugin_dir
    )
    registry.save()
    return 0, f"installed {manifest.name} {manifest.version} (payload {digest[:12]})"


def cmd_add(args) -> int:
    target = args.url
    registry = Registry.load()
    approvals = Approvals.load()
    local_root = Path(target).expanduser()
    if local_root.is_dir():
        kind, slug, root = "local", gitops.slug_for(target), local_root.resolve()
        if registry.source(slug) and not args.force:
            print(f"source {slug} already registered; --force re-discovers")
            return 0
        commit = ""
    else:
        kind = "git"
        slug = gitops.slug_for(target)
        known = registry.source(slug)
        if known and known.get("url") == target and not args.force:
            print(f"source {slug} already registered; --force re-discovers")
            return 0
        root = cache_root() / slug
        if root.exists() and args.force:
            commit = gitops.sync(root)
        elif root.exists():
            return _usage(
                f"cache dir {root} already exists for {slug}; --force re-discovers it"
            )
        else:
            commit = gitops.clone(target, root)
    try:
        source: Source = discover(root)
    except Exception as e:
        return _usage(f"source discovery failed for {slug}: {e}")
    shape = "multi-plugin" if source.shape == "multi" else "single-plugin"
    print(f"source {slug} ({shape}): {len(source.plugins)} plugin(s)")
    for manifest in source.plugins:
        print(
            f"  {manifest.name} {manifest.version} "
            f"[{'/'.join(manifest.capabilities)}] {manifest.description}"
        )
    entry = registry.source(slug)
    if entry is None:
        entry = registry.add_source(
            slug, target if kind == "git" else str(root), kind, str(root)
        )
    entry["last_commit"] = commit
    if args.no_install:
        registry.save()
        print(f"registered {slug}; install with: cquarry plugin install <name>")
        return 0
    wanted = set(args.plugin or []) if args.plugin else None
    if wanted is not None:
        unknown = wanted - set(source.names)
        if unknown:
            return _usage(
                f"--plugin names not in source {slug}: {', '.join(sorted(unknown))}"
            )
    exit_code = 0
    for manifest in source.plugins:
        if wanted is not None and manifest.name not in wanted:
            continue
        code, message = _gate_and_install(
            registry, approvals, slug, manifest, manifest.dir, yes=args.yes
        )
        print(message)
        exit_code = max(exit_code, code)
    registry.save()
    return exit_code


def cmd_list(args) -> int:
    registry = Registry.load()
    approvals = Approvals.load()
    if args.format == "json":
        payload = {"sources": registry.sources, "plugins": {}}
        for name, entry in registry.plugins.items():
            payload["plugins"][name] = {**entry, "approval": approvals.active(name)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.format is not None:
        return _usage("plugin list supports --format json only")
    if not registry.sources and not registry.plugins:
        print("no plugin sources registered (cquarry plugin add <git-url-or-path>)")
        return 0
    for source in registry.sources:
        state = f"{source['kind']} source, last synced {source.get('last_commit')[:12] or '-'}"
        print(f"source {source['slug']}: {source['url']} ({state})")
    for name, entry in sorted(registry.plugins.items()):
        live = payload_hash(Path(entry["dir"]))
        bits = [
            f"plugin {name} {entry['version']} [{'/'.join(entry['capabilities'])}]",
            f"from {entry['source']}",
            f"payload {live[:12]}",
        ]
        active = approvals.active(name)
        if entry["capabilities"] and "write" in entry["capabilities"]:
            if approvals.approves(name, live):
                bits.append("approved")
            else:
                bits.append(
                    "RE-GATED (payload changed; cquarry plugin verify " + name + ")"
                )
        elif active:
            bits.append("approved")
        manifest = load_manifest(Path(entry["dir"]) / "cquarry-plugin.toml")
        try:
            check_floors(manifest)
            bits.append("floors ok")
        except FloorError as e:
            bits.append(f"BLOCKED: {e}")
        print("  " + " | ".join(bits))
    return 0


def cmd_install(args) -> int:
    registry = Registry.load()
    approvals = Approvals.load()
    if args.name in registry.plugins:
        return _usage(f"{args.name} is already installed")
    found: tuple[str, Manifest] | None = None
    for source in registry.sources:
        try:
            discovered = discover(Path(source["path"]))
        except Exception:
            continue
        for manifest in discovered.plugins:
            if manifest.name == args.name:
                found = (source["slug"], manifest)
                break
        if found:
            break
    if found is None:
        return _usage(
            f"{args.name} is not provided by any registered source "
            "(cquarry plugin add <git-url-or-path> first)"
        )
    slug, manifest = found
    code, message = _gate_and_install(
        registry, approvals, slug, manifest, manifest.dir, yes=args.yes
    )
    print(message)
    return code


def cmd_remove(args) -> int:
    registry = Registry.load()
    if args.source:
        slug = gitops.slug_for(args.source)
        entry = registry.source(slug)
        if entry is None:
            return _usage(f"unknown source: {slug}")
        dropped = registry.drop_source(slug)
        registry.save()
        print(f"removed source {slug} and {dropped} plugin(s) it provided")
        return 0
    if args.name is None:
        return _usage("remove wants a plugin name, or --source <url-or-slug>")
    if args.name not in registry.plugins:
        return _usage(f"{args.name} is not installed")
    slug = registry.plugins[args.name]["source"]
    del registry.plugins[args.name]
    registry.save()
    print(
        f"removed {args.name} (its approvals entry stays as history; source {slug} keeps)"
    )
    return 0


def cmd_update(args) -> int:
    registry = Registry.load()
    approvals = Approvals.load()
    if args.all:
        slugs = [s["slug"] for s in registry.sources]
    elif args.names:
        slugs = []
        for name in args.names:
            if name not in registry.plugins:
                return _usage(f"{name} is not installed")
            slugs.append(registry.plugins[name]["source"])
    else:
        return _usage("update wants plugin names or --all")
    exit_code = 0
    failures: list[str] = []
    for slug in dict.fromkeys(slugs):
        source = registry.source(slug)
        if source is None:
            failures.append(f"{slug}: source vanished from the registry")
            exit_code = 1
            continue
        root = Path(source["path"])
        if source["kind"] == "local":
            print(f"source {slug}: local source; nothing to fetch")
            continue
        try:
            new_commit = gitops.sync(root)
        except gitops.GitError as e:
            failures.append(f"{slug}: {e}")
            exit_code = 1
            continue
        if new_commit == source.get("last_commit"):
            print(f"source {slug}: up to date ({new_commit[:12]})")
            continue
        source["last_commit"] = new_commit
        try:
            discovered = discover(root)
        except Exception as e:
            failures.append(f"{slug}: discovery failed after fetch: {e}")
            exit_code = 1
            registry.save()
            continue
        installed_names = {
            n for n, p in registry.plugins.items() if p["source"] == slug
        }
        for manifest in discovered.plugins:
            if manifest.name not in installed_names:
                print(
                    f"source {slug}: {manifest.name} {manifest.version} is available "
                    f"(cquarry plugin install {manifest.name})"
                )
                continue
            old = registry.plugins[manifest.name]
            live = payload_hash(manifest.dir)
            if live == old.get("payload_sha256"):
                print(f"plugin {manifest.name}: unchanged")
                continue
            if not manifest.writes:
                old["payload_sha256"] = live
                old["version"] = manifest.version
                old["dir"] = str(manifest.dir)
                print(f"plugin {manifest.name}: updated to {manifest.version}")
                continue
            try:
                check_floors(manifest)
            except FloorError as e:
                failures.append(f"{manifest.name}: {e}")
                exit_code = 1
                continue
            report, _m, _d = verify(manifest.dir)
            print(report.text(f"verify: {manifest.name} (changed; write-capable)"))
            if not report.ok:
                failures.append(
                    f"{manifest.name}: verify failed; the old payload stays installed"
                )
                exit_code = 1
                continue
            ok, reason = _confirm(
                f"approve {manifest.name} {manifest.version} (payload {live[:12]})?",
                args.yes,
            )
            if not ok:
                failures.append(
                    f"{manifest.name}: not approved ({reason}); the old payload stays installed"
                )
                exit_code = 1
                continue
            approvals.record(
                manifest.name, manifest.version, list(manifest.capabilities), live
            )
            approvals.save()
            old["payload_sha256"] = live
            old["version"] = manifest.version
            old["dir"] = str(manifest.dir)
            print(
                f"plugin {manifest.name}: updated to {manifest.version} (re-approved)"
            )
        registry.save()
    if failures:
        for failure in failures:
            _err(failure)
        return 1
    return exit_code


def _resolve_plugin(name: str, registry: Registry) -> Path | None:
    if name in registry.plugins:
        return Path(registry.plugins[name]["dir"])
    for source in registry.sources:
        try:
            discovered = discover(Path(source["path"]))
        except Exception:
            continue
        for manifest in discovered.plugins:
            if manifest.name == name:
                return manifest.dir
    return None


def cmd_verify(args) -> int:
    registry = Registry.load()
    raw = Path(args.name).expanduser()
    if raw.is_dir():
        # A directory path verifies directly (pre-registration use).
        plugin_dir = raw.resolve()
    else:
        plugin_dir = _resolve_plugin(args.name, registry)
    if plugin_dir is None:
        return _usage(
            f"{args.name} is neither installed nor provided by a registered source"
        )
    spec_path = Path(args.spec) if args.spec else None
    if spec_path is not None and not spec_path.is_file():
        return _usage(f"--spec file not found: {spec_path}")
    report, manifest, digest = verify(
        plugin_dir, spec_path=spec_path, scratch_root=cache_root() / "verify-scratch"
    )
    print(report.text(f"verify: {args.name}"))
    if not report.ok or manifest is None:
        return 1
    if manifest.writes:
        active_ok = Approvals.load().approves(manifest.name, digest)
        if active_ok:
            print(f"approval already recorded for payload {digest[:12]}")
            return 0
        ok, reason = _confirm(
            f"approve {manifest.name} {manifest.version} (payload {digest[:12]})?",
            args.yes,
        )
        if not ok:
            print(f"not approved ({reason})")
            return 1
        approvals = Approvals.load()
        approvals.record(
            manifest.name, manifest.version, list(manifest.capabilities), digest
        )
        approvals.save()
        print(f"approved {manifest.name} (payload {digest[:12]})")
    return 0


def run_plugin_verb(manifest: Manifest, context: PluginContext, argv: list[str]) -> int:
    """Load the plugin, pick the verb, run it; the probe and cmd_run seam."""
    _module, _register, verbs = _load(manifest, context)
    if not argv:
        if verbs:
            print(f"verbs for {manifest.name}: {', '.join(sorted(verbs))}")
            return 0
        _err(f"{manifest.name} registers no verbs")
        return 2
    verb, tail = argv[0], argv[1:]
    handler = verbs.get(verb)
    if handler is None:
        _err(
            f"unknown verb {verb!r} for {manifest.name}; verbs: {', '.join(sorted(verbs))}"
        )
        return 2
    code = handler(context, tail)
    return int(code) if code is not None else 0


def _load(manifest: Manifest, context: PluginContext):
    from cquarry_cli.plugins.harness import load_plugin

    return load_plugin(manifest, context)


def cmd_run(args) -> int:
    from cquarry_cli.plugins.manifest import MANIFEST_NAME

    registry = Registry.load()
    plugin_dir = _resolve_plugin(args.plugin_name, registry)
    if plugin_dir is None:
        return _usage(f"unknown plugin: {args.plugin_name}")
    manifest = load_manifest(plugin_dir / MANIFEST_NAME)
    try:
        check_floors(manifest)
    except FloorError as e:
        _err(str(e))
        return 1
    digest = payload_hash(plugin_dir)
    approvals = Approvals.load()
    approved = not manifest.writes or approvals.approves(manifest.name, digest)
    if args.apply and manifest.writes and not approved:
        _err(
            f"{manifest.name}'s payload {digest[:12]} is not the approved one; "
            "cquarry plugin verify <name> re-gates it"
        )
        return 1
    db_path = args.db
    if db_path is None:
        from cquarry.helpers import find_db

        db_path = find_db(None)
    if db_path is None:
        _err("no calibre library found (cquarry --db PATH to name one)")
        return 2
    context = PluginContext(
        plugin_name=manifest.name,
        capabilities=manifest.capabilities,
        db_path=str(db_path),
        dry_run=not args.apply,
    )
    try:
        code = run_plugin_verb(manifest, context, args.plugin_args)
    except PermissionError as e:
        _err(str(e))
        return 1
    for line in context.log_lines:
        print(line, file=sys.stderr)
    return code


def dispatch_plugin(args) -> int:
    return {
        "add": cmd_add,
        "list": cmd_list,
        "install": cmd_install,
        "remove": cmd_remove,
        "update": cmd_update,
        "verify": cmd_verify,
        "run": cmd_run,
    }[args.plugin_command](args)
