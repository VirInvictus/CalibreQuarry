with open("patchnotes.md", "r") as f:
    patch = f.read()

patch = patch.replace("## v0.9.0 (2026-08-21)\n- **UI Upgrade:** CLI scripts now feature rich output (ANSI formatting, `tqdm` progress bars, and a clear summary block). The project is no longer strictly stdlib-only and now depends on `tqdm`.\n\n# CalibreQuarry — Patch Notes", "# CalibreQuarry — Patch Notes")

patch = patch.replace("## v3.10.1 (2026-08-14)", "## v3.11.0 (2026-08-21)\n\n### Features\n\n**UI Upgrade:** CLI scripts now feature rich output (ANSI formatting, `tqdm` progress bars, and a clear summary block). The project is no longer strictly stdlib-only and now depends on `tqdm`.\n\n## v3.10.1 (2026-08-14)")

with open("patchnotes.md", "w") as f:
    f.write(patch)

# And fix the version in pyproject, config.py, and VERSION to 3.11.0
import glob

# pyproject.toml
with open("pyproject.toml", "r") as f:
    toml = f.read()
toml = toml.replace('version = "3.10.1"', 'version = "3.11.0"')
with open("pyproject.toml", "w") as f:
    f.write(toml)

# config.py
with open("src/cquarry/config.py", "r") as f:
    config = f.read()
config = config.replace('VERSION = "3.10.1"', 'VERSION = "3.11.0"')
with open("src/cquarry/config.py", "w") as f:
    f.write(config)

# VERSION
with open("VERSION", "w") as f:
    f.write("3.11.0\n")

