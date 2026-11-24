"""The CQ plugin system host (Phase 21).

Plugins are git-sourced directories carrying a ``cquarry-plugin.toml``
manifest; the registry (``~/.config/cquarry/plugins.json``) and the approvals
ledger (``plugin-approvals.json``) maintain them, and the verb family below
is the whole feature surface:

    cquarry plugin add <git-url-or-path>   discover + install (write-capable
                                           plugins pass the verify-and-approve
                                           gate)
    cquarry plugin list [--format json]    the registry rendered
    cquarry plugin install <name>          from a registered source
    cquarry plugin remove <name>           (approvals stay as history)
    cquarry plugin remove --source <url>
    cquarry plugin update [names...|--all] fetch, hash-compare, re-gate
                                           write-capable changes
    cquarry plugin verify <name> [--spec FILE]
                                           the test run, then the approval
    cquarry plugin run <name> [verb ...]   invoke a registered plugin verb
                                           (dry-run default; --apply behind
                                           the approval)

The capability gates, the trust-boundary honesty, and the version floors
are in this package's submodules; the lane law lives in the roadmap's
Phase 21 and the cquarry-plugins repo's spec.md.
"""

from __future__ import annotations

from cquarry_cli.plugins.verbs import dispatch_plugin, run_plugin_verb

__all__ = ["dispatch_plugin", "run_plugin_verb"]
