"""Reject conflicting Qt runtimes before shipping the PyQt6 application."""
import re
import os
import subprocess
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


def validate_xcb_dependencies(directory):
    directory = Path(directory).resolve()
    plugins = list(directory.rglob('libqxcb.so'))
    if not plugins:
        raise RuntimeError('The Linux bundle is missing the Qt XCB platform plugin (libqxcb.so).')
    for plugin in plugins:
        env = os.environ.copy()
        # Match the bootloader/Qt library locations, including Qt wheel layouts.
        paths = [str(directory / '_internal'), str(plugin.parents[2] / 'lib')]
        if env.get('LD_LIBRARY_PATH'):
            paths.append(env['LD_LIBRARY_PATH'])
        env['LD_LIBRARY_PATH'] = os.pathsep.join(paths)
        result = subprocess.run(['ldd', str(plugin)], env=env, capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        if result.returncode or 'not found' in output:
            raise RuntimeError(f'XCB plugin dependencies are missing or incompatible: {plugin}\n{output}')


if __name__ == '__main__':
    import cv2
    validate_opencv(cv2.getBuildInformation())
    if len(sys.argv) > 1:
        validate_bundle(sys.argv[1])
        if sys.platform.startswith('linux'):
            validate_xcb_dependencies(sys.argv[1])
    print('Packaging checks passed: headless OpenCV; no conflicting Qt 5 runtime.')
