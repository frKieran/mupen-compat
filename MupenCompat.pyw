"""Mupen Compat launcher: double-click to open the window (or drop files on this file / the built exe)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mupencompat.__main__ import run  # noqa: E402

run()
