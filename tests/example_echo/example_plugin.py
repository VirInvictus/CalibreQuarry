"""The plugin authoring template: copy this package and change the names.

``register(context)`` returns the plugin's verbs; every handler takes
``(context, argv)`` and returns an int exit code. Reads go through
``context.db`` (the opened read-only CalibreDB; the ``read`` capability
gates it), writes ONLY through ``context.write`` (the WriteGate: the
sanctioned seams behind the standing rails; dry-run default), and network
only through ``context.net`` when the manifest declares the capability.
"""

from __future__ import annotations


def register(context):
    return {"hello": hello}


def hello(context, argv):
    """Count the library's books; proves the read gate end to end."""
    count = context.db.count_books()
    context.log(f"hello: library holds {count} book(s)")
    print(f"hello from example_echo: {count} book(s) in the library")
    return 0
