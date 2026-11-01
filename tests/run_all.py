"""
Run every test: python tests/run_all.py

These drive the real app, so windows open and close while they run. They use
temporary settings and a temporary database, and fake market and AI servers, so
they don't touch your data or need an API key. test_ai adds and removes one
throwaway entry in the system credential store.

Set OSRS_LIVE=1 to also make the few free checks against the real OpenRouter
model list. Set OSRS_TEST_SHOTS=1 to save screenshots (needs Pillow).
"""

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    failed = []
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        name = os.path.basename(path)
        start = time.time()
        result = subprocess.run(
            [sys.executable, path], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        passed = result.returncode == 0
        print(f"{'ok  ' if passed else 'FAIL'} {name} ({time.time() - start:.0f}s)",
              flush=True)
        if not passed:
            failed.append(name)
            lines = [l for l in result.stdout.splitlines() if l.startswith("FAIL")]
            print("\n".join(lines or result.stdout.splitlines()[-15:]))
            print("\n".join(result.stderr.splitlines()[-15:]))
    print(f"\n{'ALL TESTS PASSED' if not failed else 'FAILED: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
