"""Reject conflicting Qt runtimes before shipping the PyQt6 application."""
import re
import sys
from pathlib import Path


def validate_opencv(build_information):
    match = re.search(r'^\s*GUI:\s*(\S+)', build_information, re.MULTILINE)
    if not match or match.group(1).upper() != 'NONE':
        raise RuntimeError('Build requires opencv-python-headless (GUI: NONE). '
                           'Remove other OpenCV wheel variants and reinstall requirements.txt in a clean environment.')


def validate_bundle(directory):
    conflicts = [str(path) for path in Path(directory).rglob('*')
                 if path.is_file() and re.match(r'(?:lib)?Qt5.*\.(?:so(?:\..*)?|dll|dylib)$', path.name, re.IGNORECASE)]
    if conflicts:
        raise RuntimeError('Qt 5 libraries found in PyQt6 bundle: ' + ', '.join(conflicts))


if __name__ == '__main__':
    import cv2
    validate_opencv(cv2.getBuildInformation())
    if len(sys.argv) > 1:
        validate_bundle(sys.argv[1])
    print('Packaging checks passed: headless OpenCV; no conflicting Qt 5 runtime.')
