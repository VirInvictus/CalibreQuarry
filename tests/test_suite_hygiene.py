"""Suite-hygiene pins from the 2026-09-20 test-gap audit (its item 8).

Two test files carried ``if __name__ == "__main__": unittest.main()``
guards mid-file: direct ``python tests/test_x.py`` runs silently skipped
every class defined below the guard, while discovery still saw them, so
the gap hid from run_tests.sh and CI both. The 3.41.0-era repair moved
the guards to file end; this pin keeps every guard below the last class
so the bug class cannot return by appending a suite after a guard.
"""

import re
import unittest
from pathlib import Path

_TESTS = Path(__file__).parent


class TestUnittestMainGuardPosition(unittest.TestCase):
    def test_every_guard_sits_below_the_last_class(self):
        offenders = []
        for path in sorted(_TESTS.glob("test_*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            last_class = max(
                (i for i, line in enumerate(lines) if re.match(r"^class ", line)),
                default=None,
            )
            guard = next(
                (
                    i
                    for i, line in enumerate(lines)
                    if re.match(r'^if __name__ == "__main__":', line)
                ),
                None,
            )
            if last_class is not None and guard is not None and guard < last_class:
                offenders.append(
                    f"{path.name}:{guard + 1} (last class at {last_class + 1})"
                )
        self.assertEqual(offenders, [])

    def test_every_suite_reachable_by_direct_invocation(self):
        # The sibling pin: a file that ends in something other than the
        # guard (or has classes after any trailing code) is exactly how
        # the mid-file guards happened. Every test file ends at the guard
        # block, so a class appended "after" it cannot be invisible.
        for path in sorted(_TESTS.glob("test_*.py")):
            with self.subTest(path.name):
                lines = [
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertTrue(
                    lines[-1].strip() == "unittest.main()",
                    f"{path.name} does not end with unittest.main()",
                )


if __name__ == "__main__":
    unittest.main()
