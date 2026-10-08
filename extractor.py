#!/usr/bin/env python3
"""
OmniExtract Studio root shim entry point.
"""
import sys

from omniextract import __version__
from omniextract.main import main

# Ensure Windows assigns the taskbar icon to our custom app ID rather than grouping under python.exe
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"throot.omniextractstudio.app.{__version__}")
    except Exception:
        pass

if __name__ == "__main__":
    main()
