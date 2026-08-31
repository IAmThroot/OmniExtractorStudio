#!/usr/bin/env python3
"""
OmniExtract Studio root shim entry point.
"""
import sys

# Ensure Windows assigns the taskbar icon to our custom app ID rather than grouping under python.exe
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("throot.omniextractstudio.app.1.1.0")
    except Exception:
        pass

from omniextract.main import main

if __name__ == "__main__":
    main()
