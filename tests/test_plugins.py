"""The plugin host suite (Phase 21): the gates, the harness, and the verbs.

The git-path tests drive REAL repositories (git init + commit in a tmpdir,
`plugin add file://...`, `plugin update --all`), because the update flow's
hash-compare and re-gate classes are exactly the layer mocks cannot cover
(the live-seam lesson). The registry, approvals, and cache all live under an
isolated CQUARRY_PLUGIN_HOME per test.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cquarry_cli.cli import build_parser, main
from cquarry_cli.plugins import gitops
from cquarry_cli.plugins.discovery import discover
from cquarry_cli.plugins.harness import payload_hash, verify
from cquarry_cli.plugins.manifest import (
    FloorError,
    ManifestError,
    _floor_tuple,
    _version_tuple,
    check_floors,
    load_manifest,
)
from cquarry_cli.plugins.scratch import build_scratch
from cquarry_cli.plugins.store import Approvals, Registry

EXAMPLE = Path(__file__).resolve().parent / "example_echo"


def _manifest_text(
    name: str,
    capabilities=("read",),
    host_floor=">=3.90.0",
    probes=None,
) -> str:
    caps = ", ".join(f'"{c}"' for c in capabilities)
    lines = [
        "schema = 1",
        f'name = "{name}"',
        'version = "0.1.0"',
        "api_version = 1",
        'license = "MIT"',
        f"capabilities = [{caps}]",
        'entry = "example_plugin:register"',
        'description = "test plugin"',
        "",
        "[requires]",
        'cquarry = ">=1.0.0"',
        f'calibrequarry = "{host_floor}"',
        "",
        "[upstream]",
        'source = "https://example.invalid/repo"',
        f'plugin = "{name}"',
        'version = "0.1.0"',
        'license = "MIT"',
        'derivation = "original"',
    ]
    if probes:
        quoted = ", ".join(f'"{p}"' for p in probes)
        lines += ["", "[verify]", f"probes = [{quoted}]"]
    return "\n".join(lines) + "\n"


def _write_plugin(root: Path, name: str, **kwargs) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "cquarry-plugin.toml").write_text(_manifest_text(name, **kwargs))
    shutil.copy(EXAMPLE / "example_plugin.py", plugin_dir / "example_plugin.py")
    return plugin_dir


def _git_source(root: Path, name: str = "src_repo", **kwargs) -> str:
    """A real single-plugin git repository; returns its file:// URL."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "cquarry-plugin.toml").write_text(
        _manifest_text(name.replace("-", "_"), **kwargs)
    )
    shutil.copy(EXAMPLE / "example_plugin.py", repo / "example_plugin.py")
    (repo / "README.md").write_text("test source\n")
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo.as_uri()


class PluginHomeCase(unittest.TestCase):
    """An isolated CQUARRY_PLUGIN_HOME per test."""

    def setUp(self) -> None:
        self._old_home = os.environ.get("CQUARRY_PLUGIN_HOME")
        self.home = Path(tempfile.mkdtemp(prefix="cquarry-plugins-test-"))
        os.environ["CQUARRY_PLUGIN_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("CQUARRY_PLUGIN_HOME", None)
        else:
            os.environ["CQUARRY_PLUGIN_HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def _args(self, *argv):
        parser = build_parser()
        return parser.parse_args(["plugin", *argv])


class TestManifestValidation(unittest.TestCase):
    def test_example_manifest_loads(self) -> None:
        manifest = load_manifest(EXAMPLE / "cquarry-plugin.toml")
        self.assertEqual(manifest.name, "example_echo")
        self.assertEqual(manifest.capabilities, ("read",))
        self.assertFalse(manifest.writes)

    def test_name_must_equal_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "other_name"
            wrong.mkdir()
            shutil.copy(EXAMPLE / "cquarry-plugin.toml", wrong / "cquarry-plugin.toml")
            with self.assertRaises(ManifestError):
                load_manifest(wrong / "cquarry-plugin.toml")

    def test_unknown_schema_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "schema_two")
            path = plugin / "cquarry-plugin.toml"
            path.write_text(
                _manifest_text("schema_two").replace("schema = 1", "schema = 2")
            )
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_unknown_capability_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(
                Path(tmp), "cap_weird", capabilities=("read", "root")
            )
            with self.assertRaises(ManifestError):
                load_manifest(plugin / "cquarry-plugin.toml")

    def test_malformed_floor_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "floor_weird", host_floor="1.2.3")
            with self.assertRaises(ManifestError):
                load_manifest(plugin / "cquarry-plugin.toml")

    def test_missing_requires_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "no_requires")
            text = (plugin / "cquarry-plugin.toml").read_text()
            text = (
                text.split("[requires]")[0]
                + "[upstream]\n"
                + text.split("[upstream]")[1]
            )
            (plugin / "cquarry-plugin.toml").write_text(text)
            with self.assertRaises(ManifestError):
                load_manifest(plugin / "cquarry-plugin.toml")


class TestVersionFloors(unittest.TestCase):
    def test_tuple_parsing(self) -> None:
        self.assertEqual(_version_tuple("1.29.2"), (1, 29, 2))
        self.assertEqual(_floor_tuple(">=3.94.0"), (3, 94, 0))

    def test_unreachable_host_floor_refuses_with_the_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "future_plugin", host_floor=">=99.0.0")
            manifest = load_manifest(plugin / "cquarry-plugin.toml")
            with self.assertRaises(FloorError) as ctx:
                check_floors(manifest)
            self.assertIn(">=99.0.0", str(ctx.exception))
            self.assertIn("upgrade calibrequarry", str(ctx.exception))

    def test_satisfied_floors_pass(self) -> None:
        manifest = load_manifest(EXAMPLE / "cquarry-plugin.toml")
        check_floors(manifest)


class TestDiscovery(unittest.TestCase):
    def test_single_plugin_source(self) -> None:
        source = discover(EXAMPLE)
        self.assertEqual(source.shape, "single")
        self.assertEqual(source.names, ["example_echo"])

    def test_multi_plugin_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_plugin(root, "plugin_a")
            _write_plugin(root, "plugin_b")
            source = discover(root)
            self.assertEqual(source.shape, "multi")
            self.assertEqual(source.names, ["plugin_a", "plugin_b"])

    def test_empty_source_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ManifestError):
                discover(Path(tmp))

    def test_duplicate_names_refused(self) -> None:
        # Two package dirs claiming one name: the manifests disagree with
        # their own dirs first (name must equal dir), so discovery refuses
        # with ManifestError either way -- the dup check guards the same
        # broken state at the source level.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_plugin(root, "twin_a")
            _write_plugin(root, "twin_b")
            pattern = 'name = "twin_'
            replacement = 'name = "twin'
            for dirname in ("twin_a", "twin_b"):
                path = root / dirname / "cquarry-plugin.toml"
                path.write_text(path.read_text().replace(pattern, replacement))
            with self.assertRaises(ManifestError):
                discover(root)


class TestPayloadHash(unittest.TestCase):
    def test_stable_and_change_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "hashed")
            first = payload_hash(plugin)
            self.assertEqual(first, payload_hash(plugin))
            (plugin / "example_plugin.py").write_text("# changed\n")
            self.assertNotEqual(first, payload_hash(plugin))


class TestStore(PluginHomeCase):
    def test_registry_round_trip_and_drop(self) -> None:
        registry = Registry.load()
        registry.add_source("src", "https://example.invalid/repo", "git", "/tmp/nope")
        registry.plugins["p"] = {"source": "src"}
        registry.save()
        loaded = Registry.load()
        self.assertEqual(loaded.source("src")["url"], "https://example.invalid/repo")
        self.assertIn("p", loaded.plugins)
        self.assertEqual(loaded.drop_source("src"), 1)
        self.assertEqual(loaded.plugins, {})

    def test_approvals_re_gate_on_hash_change(self) -> None:
        approvals = Approvals.load()
        approvals.record("p", "0.1.0", ["read", "write"], "hash-one")
        approvals.save()
        loaded = Approvals.load()
        self.assertTrue(loaded.approves("p", "hash-one"))
        self.assertFalse(loaded.approves("p", "hash-two"))
        loaded.record("p", "0.2.0", ["read", "write"], "hash-two")
        self.assertTrue(loaded.approves("p", "hash-two"))
        self.assertEqual(len(loaded.entries["p"]), 2)


class TestGitops(unittest.TestCase):
    def test_slug_for(self) -> None:
        self.assertEqual(
            gitops.slug_for("https://github.com/x/cquarry-plugins"), "cquarry-plugins"
        )
        self.assertEqual(gitops.slug_for("https://github.com/x/repo.git"), "repo")
        self.assertEqual(gitops.slug_for("https://github.com/x/repo/"), "repo")


class TestVerify(PluginHomeCase):
    def test_example_verifies_with_its_probe(self) -> None:
        report, manifest, digest = verify(EXAMPLE)
        self.assertTrue(report.ok, report.lines)
        self.assertEqual(manifest.name, "example_echo")
        self.assertTrue(any("probe 'hello' exited 0" in line for line in report.lines))

    def test_floor_block_fails_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "future", host_floor=">=99.0.0")
            report, manifest, _digest = verify(plugin)
            self.assertFalse(report.ok)
            self.assertTrue(any(">=99.0.0" in line for line in report.lines))
            self.assertIsNotNone(manifest)

    def test_spec_probes_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"probes": ["hello"]}))
            report, _m, _d = verify(EXAMPLE, spec_path=spec)
            self.assertTrue(
                any("probe 'hello' exited 0" in line for line in report.lines)
            )


class TestVerbs(PluginHomeCase):
    def test_add_local_single_installs_readonly_without_a_prompt(self) -> None:
        code = main(["plugin", "add", str(EXAMPLE)])
        self.assertEqual(code, 0)
        registry = Registry.load()
        self.assertIn("example_echo", registry.plugins)
        self.assertEqual(registry.plugins["example_echo"]["source"], "example_echo")

    def test_add_re_register_is_a_noop_without_force(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        code = main(["plugin", "add", str(EXAMPLE)])
        self.assertEqual(code, 0)
        self.assertEqual(len(Registry.load().sources), 1)

    def test_list_renders_text_and_json(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["plugin", "list"])
        self.assertIn("plugin example_echo", buffer.getvalue())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["plugin", "list", "--format", "json"])
        payload = json.loads(buffer.getvalue())
        self.assertIn("example_echo", payload["plugins"])

    def test_list_refuses_non_json_format(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        self.assertEqual(main(["plugin", "list", "--format", "csv"]), 2)

    def test_run_hello_counts_the_scratch_library(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        scratch = build_scratch(self.home / "run-scratch")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(
                [
                    "plugin",
                    "run",
                    "--library",
                    str(scratch),
                    "example_echo",
                    "hello",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("hello from example_echo: 2 book(s)", buffer.getvalue())

    def test_run_bare_lists_the_verbs(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["plugin", "run", "--library", "unused", "example_echo"])
        self.assertEqual(code, 0)
        self.assertIn("verbs for example_echo: hello", buffer.getvalue())

    def test_run_unknown_plugin_is_usage(self) -> None:
        self.assertEqual(main(["plugin", "run", "ghost"]), 2)

    def test_write_plugin_gates_through_verify_and_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = _git_source(Path(tmp), "writer_repo", capabilities=("read", "write"))
            code = main(["plugin", "add", url, "--yes"])
            self.assertEqual(code, 0)
            approvals = Approvals.load()
            self.assertIn("writer_repo", approvals.entries)
            live = payload_hash(Path(Registry.load().plugins["writer_repo"]["dir"]))
            self.assertTrue(approvals.approves("writer_repo", live))

    def test_apply_refuses_after_payload_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = _git_source(Path(tmp), "writer_repo", capabilities=("read", "write"))
            main(["plugin", "add", url, "--yes"])
            plugin_dir = Path(Registry.load().plugins["writer_repo"]["dir"])
            module_file = plugin_dir / "example_plugin.py"
            module_file.write_text(module_file.read_text() + "\n# drifted\n")
            scratch = build_scratch(self.home / "apply-scratch")
            code = main(
                [
                    "plugin",
                    "run",
                    "--apply",
                    "--library",
                    str(scratch),
                    "writer_repo",
                    "hello",
                ]
            )
            self.assertEqual(code, 1)
            # The dry run is unaffected by the re-gate: no --apply, no refusal.
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(
                    ["plugin", "run", "--library", str(scratch), "writer_repo", "hello"]
                )
            self.assertEqual(code, 0)

    def test_update_re_gates_a_changed_write_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = _git_source(Path(tmp), "writer_repo", capabilities=("read", "write"))
            repo = Path(tmp) / "writer_repo"
            main(["plugin", "add", url, "--yes"])
            before = Registry.load().plugins["writer_repo"]["payload_sha256"]
            module_file = repo / "example_plugin.py"
            module_file.write_text(module_file.read_text() + "\n# v2\n")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-qm",
                    "two",
                ],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            code = main(["plugin", "update", "--all", "--yes"])
            self.assertEqual(code, 0)
            after = Registry.load().plugins["writer_repo"]["payload_sha256"]
            self.assertNotEqual(before, after)
            self.assertEqual(len(Approvals.load().entries["writer_repo"]), 2)

    def test_update_updates_a_changed_readonly_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = _git_source(Path(tmp), "src_repo")
            repo = Path(tmp) / "src_repo"
            main(["plugin", "add", url])
            before = Registry.load().plugins["src_repo"]["payload_sha256"]
            module_file = repo / "example_plugin.py"
            module_file.write_text(module_file.read_text() + "\n# v2\n")
            subprocess.run(
                ["git", "add", "-A"], cwd=repo, check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-qm",
                    "two",
                ],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(main(["plugin", "update", "--all"]), 0)
            self.assertNotEqual(
                before, Registry.load().plugins["src_repo"]["payload_sha256"]
            )

    def test_update_isolates_a_dead_source(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        registry = Registry.load()
        registry.add_source(
            "dead", "file:///nonexistent/repo.git", "git", "/nonexistent/repo"
        )
        registry.save()
        code = main(["plugin", "update", "--all"])
        self.assertEqual(code, 1)
        self.assertIn("example_echo", Registry.load().plugins)

    def test_update_reports_new_plugins_without_installing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "multi_repo"
            root.mkdir(parents=True)
            _write_plugin(root, "plugin_a")
            subprocess.run(
                ["git", "init", "-q", "-b", "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=root, check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-qm",
                    "one",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
            main(["plugin", "add", root.as_uri(), "--plugin", "plugin_a"])
            self.assertNotIn("plugin_b", Registry.load().plugins)
            _write_plugin(root, "plugin_b")
            subprocess.run(
                ["git", "add", "-A"], cwd=root, check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-qm",
                    "two",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
            code = main(["plugin", "update", "--all"])
            self.assertEqual(code, 0)
            registry = Registry.load()
            self.assertNotIn("plugin_b", registry.plugins)
            self.assertIn("plugin_a", registry.plugins)

    def test_remove_plugin_and_source(self) -> None:
        main(["plugin", "add", str(EXAMPLE)])
        self.assertEqual(main(["plugin", "remove", "example_echo"]), 0)
        self.assertNotIn("example_echo", Registry.load().plugins)
        self.assertEqual(main(["plugin", "remove", "--source", "example_echo"]), 0)
        self.assertEqual(Registry.load().sources, [])

    def test_verify_records_approval_for_write_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = _git_source(Path(tmp), "writer_repo", capabilities=("read", "write"))
            main(["plugin", "add", url, "--no-install"])
            code = main(["plugin", "verify", "writer_repo", "--yes"])
            self.assertEqual(code, 0)
            self.assertIn("writer_repo", Approvals.load().entries)
            self.assertNotIn("writer_repo", Registry.load().plugins)
            code = main(["plugin", "install", "writer_repo"])
            self.assertEqual(code, 0)
            self.assertIn("writer_repo", Registry.load().plugins)

    def test_verify_refuses_a_failing_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "future", host_floor=">=99.0.0")
            code = main(["plugin", "verify", str(plugin)])
            self.assertEqual(code, 1)


class TestSurfaceWiring(unittest.TestCase):
    def test_help_json_carries_the_plugin_topic(self) -> None:
        from cquarry_cli.helpcli import topic_json

        payload = json.loads(topic_json())
        self.assertIn("plugin", payload["topics"])
        details = payload["topic_details"]["plugin"]
        self.assertIn("exits", details)
        self.assertTrue(details["gates"])

    def test_plugin_topic_page_renders(self) -> None:
        from cquarry_cli.helpcli import topic_plugin

        text = topic_plugin()
        self.assertIn("cquarry plugin add", text)
        self.assertIn("exit codes", text)

    def test_bare_plugin_command_is_a_usage_error(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["plugin"])

    def test_run_tail_passes_unknown_flags_verbatim(self) -> None:
        # REMAINDER: the verb tail is the plugin's flag space (--ids is not
        # the host's), and the host flags precede the plugin name.
        parser = build_parser()
        args = parser.parse_args(
            [
                "plugin",
                "run",
                "--apply",
                "--library",
                "/tmp/x",
                "p",
                "scan",
                "--ids",
                "2",
            ]
        )
        self.assertTrue(args.apply)
        self.assertEqual(args.library, "/tmp/x")
        self.assertEqual(args.plugin_name, "p")
        self.assertEqual(args.plugin_args, ["scan", "--ids", "2"])

    def test_run_tail_bare(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["plugin", "run", "p"])
        self.assertEqual(args.plugin_args, [])

    def test_unknown_plugin_install_is_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("CQUARRY_PLUGIN_HOME")
            os.environ["CQUARRY_PLUGIN_HOME"] = tmp
            try:
                self.assertEqual(main(["plugin", "install", "ghost"]), 2)
            finally:
                if old is None:
                    os.environ.pop("CQUARRY_PLUGIN_HOME", None)
                else:
                    os.environ["CQUARRY_PLUGIN_HOME"] = old


class TestWriteGateApply(unittest.TestCase):
    """The WriteGate's plan output must be consumable by run_write_batch:
    the (label, action) pair shape (the extract_isbn port's live find)."""

    def test_plan_identifier_yields_label_action_pairs(self) -> None:
        from cquarry_cli.plugins.gates import PluginContext, _WRITE_GATE
        from cquarry_cli.plugins.scratch import build_scratch

        tmp = tempfile.mkdtemp(prefix="cq-gate-")
        try:
            scratch = build_scratch(Path(tmp) / "lib")
            context = PluginContext(
                plugin_name="p",
                capabilities=("read", "write"),
                db_path=str(scratch),
                dry_run=False,
            )
            pair = context.write.plan_identifier(2, "isbn", "9780140445688")
            self.assertIsInstance(pair, tuple)
            label, action = pair
            self.assertIn("isbn", label)
            code = _WRITE_GATE.apply(str(scratch), [pair])
            self.assertEqual(code, 0)
            import sqlite3

            conn = sqlite3.connect(scratch)
            stored = conn.execute(
                "SELECT val FROM identifiers WHERE book = 2 AND type = 'isbn'"
            ).fetchone()
            conn.close()
            self.assertEqual(stored[0], "9780140445688")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_action_gate_pairs(self) -> None:
        """The action-class gates: every plan_* returns the (label, action)
        pair and every action runs through run_write_batch."""
        from cquarry_cli.plugins.gates import PluginContext, _WRITE_GATE
        from cquarry_cli.plugins.scratch import build_scratch

        tmp = tempfile.mkdtemp(prefix="cq-gate2-")
        try:
            scratch = build_scratch(Path(tmp) / "lib")
            context = PluginContext(
                plugin_name="p",
                capabilities=("read", "write"),
                db_path=str(scratch),
                dry_run=False,
            )
            gate = context.write
            add = gate.plan_add_format(2, "EPUB", "merged2", 4096)
            remove_fmt = gate.plan_remove_format(1, "EPUB")
            rename = gate.plan_rename_entity("authors", "Scratch, Author", "New Name")
            remove_book = gate.plan_remove_book(2)
            self.assertTrue(
                all(
                    isinstance(p, tuple) for p in (add, remove_fmt, rename, remove_book)
                )
            )
            code = _WRITE_GATE.apply(
                str(scratch), [add, remove_fmt, rename, remove_book]
            )
            self.assertEqual(code, 0)
            import sqlite3

            conn = sqlite3.connect(scratch)
            rows = conn.execute(
                "SELECT book FROM data WHERE format = 'EPUB'"
            ).fetchall()
            books = conn.execute("SELECT id FROM books ORDER BY id").fetchall()
            author = conn.execute(
                "SELECT name FROM authors WHERE name = 'New Name'"
            ).fetchall()
            old_author = conn.execute(
                "SELECT name FROM authors WHERE name = 'Scratch, Author'"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [])  # book 2's added row cascaded away
            self.assertEqual([b[0] for b in books], [1])  # book 2 removed
            self.assertEqual(len(author), 1)
            self.assertEqual(old_author, [])  # the old spelling is gone
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestConfigSeam(unittest.TestCase):
    """Contract 2: the per-plugin config seam (load/save, isolated dir)."""

    def test_round_trip_and_isolation(self) -> None:
        import json as _json
        from cquarry_cli.plugins.gates import PluginContext
        from cquarry_cli.plugins.store import plugin_config_dir

        home = Path(tempfile.mkdtemp(prefix="cq-cfg-"))
        old = os.environ.get("CQUARRY_PLUGIN_HOME")
        os.environ["CQUARRY_PLUGIN_HOME"] = str(home)
        try:
            a = PluginContext(plugin_name="alpha", capabilities=("read",))
            a.save_config({"libraries": {"u1": {"bookExemptions": [[1, 2]]}}})
            b = PluginContext(plugin_name="beta", capabilities=("read",))
            self.assertEqual(b.load_config(), {})
            self.assertEqual(
                a.load_config(), {"libraries": {"u1": {"bookExemptions": [[1, 2]]}}}
            )
            self.assertNotEqual(plugin_config_dir("alpha"), plugin_config_dir("beta"))
            self.assertTrue(a.config_path().exists())
            self.assertEqual(_json.loads(a.config_path().read_text()), a.load_config())
        finally:
            if old is None:
                os.environ.pop("CQUARRY_PLUGIN_HOME", None)
            else:
                os.environ["CQUARRY_PLUGIN_HOME"] = old
            shutil.rmtree(home, ignore_errors=True)

    def test_plan_seam_allows_dry_planning(self) -> None:
        """Contract 3: planning (label building) is allowed in dry runs;
        execution still demands the write capability."""
        from cquarry_cli.plugins.gates import PluginContext

        home = Path(tempfile.mkdtemp(prefix="cq-plan-"))
        old = os.environ.get("CQUARRY_PLUGIN_HOME")
        os.environ["CQUARRY_PLUGIN_HOME"] = str(home)
        try:
            dry = PluginContext(
                plugin_name="p",
                capabilities=("read", "write"),
                db_path="/tmp/nowhere.db",
                dry_run=True,
            )
            pair = dry.plan.plan_identifier(1, "isbn", "9780140445688")
            self.assertIsInstance(pair, tuple)
            with self.assertRaises(PermissionError):
                _ = dry.write  # execution stays gated under dry runs
            apply_ctx = PluginContext(
                plugin_name="p",
                capabilities=("read", "write"),
                db_path="/tmp/nowhere.db",
                dry_run=False,
            )
            self.assertIs(apply_ctx.write, apply_ctx.plan)
        finally:
            if old is None:
                os.environ.pop("CQUARRY_PLUGIN_HOME", None)
            else:
                os.environ["CQUARRY_PLUGIN_HOME"] = old
            shutil.rmtree(home, ignore_errors=True)

    def test_contract_2_and_3_accepted_contract_4_refused(self) -> None:
        from cquarry_cli.plugins.manifest import FloorError, check_floors, load_manifest

        manifest = load_manifest(EXAMPLE / "cquarry-plugin.toml")
        self.assertEqual(manifest.api_version, 1)
        check_floors(manifest)  # contract 1 plugins stay welcome
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _write_plugin(Path(tmp), "future_api")
            path = plugin / "cquarry-plugin.toml"
            path.write_text(
                _manifest_text("future_api").replace(
                    "api_version = 1", "api_version = 2"
                )
            )
            check_floors(load_manifest(path))  # contracts 2 and 3 stay welcome
            path.write_text(
                _manifest_text("future_api").replace(
                    "api_version = 1", "api_version = 4"
                )
            )
            with self.assertRaises(FloorError):
                check_floors(load_manifest(path))


if __name__ == "__main__":
    unittest.main()
