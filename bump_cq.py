import re
from datetime import datetime
import os

date_str = datetime.now().strftime("%Y-%m-%d")

# pyproject.toml
with open("pyproject.toml", "r") as f: content = f.read()
content = re.sub(r'version = "3\.13\.0"', 'version = "3.14.0"', content)
with open("pyproject.toml", "w") as f: f.write(content)

# VERSION file
if os.path.exists("VERSION"):
    with open("VERSION", "w") as f: f.write("3.14.0")

# patchnotes.md
patchnotes = f"""# 3.14.0 ({date_str})
- **Refactor**: Adapted to `vir-tui` v2.0.0 public API and decoupled menu fallbacks.
- **Fix**: The non-curses fallback text menu now functions properly for CalibreQuarry by passing custom `letter_keys` and `aliases` during initialization.

"""
with open("patchnotes.md", "r") as f: current_pn = f.read()
with open("patchnotes.md", "w") as f: f.write(patchnotes + current_pn)

# spec.md
with open("spec.md", "r") as f: spec = f.read()
spec = spec.replace("The CLI utilizes a static UI", "The CLI relies on `vir-tui` for dynamic fallback menus")
with open("spec.md", "w") as f: f.write(spec)

# README.md
with open("README.md", "r") as f: readme = f.read()
readme = readme.replace("3.13.0", "3.14.0")
with open("README.md", "w") as f: f.write(readme)

# CLAUDE.md
with open("CLAUDE.md", "r") as f: claude = f.read()
claude = claude.replace("v3.13.0", "v3.14.0")
with open("CLAUDE.md", "w") as f: f.write(claude)

