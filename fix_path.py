import glob
for script in glob.glob("/home/bdkl/.gitrepos/CalibreQuarry/scripts/*.py"):
    if "ui.py" in script:
        continue
    with open(script, "r") as f:
        content = f.read()
    if "import ui" in content and "sys.path.insert" not in content:
        content = content.replace(
            "import argparse\nimport ui\n", 
            "import argparse\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport ui\n"
        )
        content = content.replace(
            "import argparse\nimport sys\nimport ui\n", 
            "import argparse\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport ui\n"
        )
        with open(script, "w") as f:
            f.write(content)

