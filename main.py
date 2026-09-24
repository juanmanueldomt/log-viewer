"""Run JMLogViewer from a source checkout without installing it: ``python main.py [FILE]``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jmlogviewer.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
