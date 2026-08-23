import re
from pathlib import Path

content = Path("patchnotes.md").read_text()

new_notes = """# CalibreQuarry Patchnotes

## v3.13.0 (2026-08-23)

---

### Architecture & Upgrades

**Shared cquarry Core:** `CalibreQuarry` has been completely refactored to use the newly extracted `cquarry` standalone library for all database operations, search logic, and taxonomy parsing. This aligns the underlying query engine with `Bindery` and `Hermitage`, meaning all three tools now inherit stability improvements and Calibre lock-handling mechanisms uniformly.

**Local Testing Fixes:** The `pyproject.toml` file now leverages hatch's `allow-direct-references` to properly install the `cquarry` git dependency during local development and testing.

"""

content = re.sub(r"^# CalibreQuarry Patchnotes\n", new_notes, content)
Path("patchnotes.md").write_text(content)
