"""Pytest bootstrap: make the ``src`` layout importable without installation.

Deployment installs the package (``pip install .``); tests and the smoke
commands in the docs run straight from a checkout, so add ``src`` to the path
here instead of requiring an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))