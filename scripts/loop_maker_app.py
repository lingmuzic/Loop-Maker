"""Entry point for the standalone Windows build (see scripts/build_exe.py).

    Loop Maker.exe                       -> opens the GUI
    Loop Maker.exe --selftest out.txt    -> runs loopmaker.selftest, writes out.txt
"""

import sys
from pathlib import Path


def main() -> int:
    if "--selftest" in sys.argv:
        from loopmaker.selftest import run

        i = sys.argv.index("--selftest")
        target = Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 else Path("selftest.txt")
        return run(target)

    from loopmaker.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
