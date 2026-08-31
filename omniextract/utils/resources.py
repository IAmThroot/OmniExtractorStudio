import os
import sys


def get_resource_path(relative_path):
    """Resolve file path for development or PyInstaller standalone packaging."""
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    direct_path = os.path.join(base_path, relative_path)
    if os.path.exists(direct_path):
        return direct_path

    # Check under omniextract package folder
    pkg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", relative_path))
    if os.path.exists(pkg_path):
        return pkg_path

    # Check PyInstaller onedir _internal folder
    exe_dir = os.path.dirname(sys.executable)
    internal_path = os.path.join(exe_dir, "_internal", relative_path)
    if os.path.exists(internal_path):
        return internal_path

    # Fallback to executable directory
    fallback = os.path.join(exe_dir, relative_path)
    if os.path.exists(fallback):
        return fallback

    return direct_path
