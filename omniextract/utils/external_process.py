"""Launch system media tools without the frozen app's bundled library paths."""
import os
import subprocess
import sys


def external_environment(env=None):
    child = dict(os.environ if env is None else env)
    if not getattr(sys, 'frozen', False) or not sys.platform.startswith(('linux', 'freebsd')):
        return child

    # The bootloader prepends _MEIPASS. AppImage's launcher may also have
    # prepended APPDIR paths before the bootloader saved the original value.
    original = child.get('LD_LIBRARY_PATH_ORIG', '')
    roots = [os.path.realpath(path) for path in
             (getattr(sys, '_MEIPASS', ''), child.get('APPDIR', '')) if path]
    paths = []
    for path in original.split(os.pathsep):
        if not path:
            continue
        resolved = os.path.realpath(path)
        if not any(resolved == root or resolved.startswith(root + os.sep) for root in roots):
            paths.append(path)
    if paths:
        child['LD_LIBRARY_PATH'] = os.pathsep.join(paths)
    else:
        child.pop('LD_LIBRARY_PATH', None)
    return child


def run_external(*args, **kwargs):
    kwargs['env'] = external_environment(kwargs.get('env'))
    return subprocess.run(*args, **kwargs)


def popen_external(*args, **kwargs):
    kwargs['env'] = external_environment(kwargs.get('env'))
    return subprocess.Popen(*args, **kwargs)
