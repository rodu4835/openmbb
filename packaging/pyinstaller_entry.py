"""PyInstaller entry point.

A thin wrapper so the frozen binary (onefile or onedir alike) launches the same
CLI as the `openmbb` console script — GUI by default, or --sim / --selftest /
--smoketest.
"""

from openmbb.cli import main

if __name__ == "__main__":
    main()
