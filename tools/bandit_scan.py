"""Runs bandit on the code, including the files that use `lazy import` (PEP 810).

bandit needs pyyaml, which has no wheel for Python 3.15 yet, so it runs on Python 3.13 via uv. Python 3.13 cannot
parse `lazy import`, and bandit then silently skips the file (main.py, web.py and image.py were never scanned).
This script copies the code to a temporary folder, removes only the word `lazy` in front of those imports (line
numbers stay the same) and runs bandit there. Remove this script once bandit runs on 3.15.

Usage: python tools/bandit_scan.py  (from the project folder; needs uv)
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["app", "Garage.pyw", "tools"]
BANDIT = ["uvx", "--python", "3.13", "bandit==1.9.4"]
LAZY = re.compile(r"^(\s*)lazy (import|from) ", re.MULTILINE)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="garage-bandit-") as tmp:
        for target in TARGETS:
            source = ROOT / target
            files = [source] if source.is_file() else [p for p in source.rglob("*") if p.suffix in (".py", ".pyw")]
            for file in files:
                copy = Path(tmp) / file.relative_to(ROOT)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text(LAZY.sub(r"\1\2 ", file.read_text(encoding="utf-8")), encoding="utf-8")
        if not shutil.which("uvx"):
            print("uv is missing: install uv (https://docs.astral.sh/uv/) or run bandit yourself.")
            return 1
        result = subprocess.run([*BANDIT, "-q", "-r", *TARGETS, "-ll"], cwd=tmp, capture_output=True, text=True)
        print(result.stdout + result.stderr, end="")
        if "Files skipped" in result.stdout:  # a file bandit cannot read is a gap, not a pass
            print("bandit skipped files; see above.")
            return 1
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
