from ..media.process import remove_file
import os
import subprocess
from PyQt6.QtCore import pyqtSignal
from .base import ManagedWorker, ResultFormat
from ..media.process import close_stream, progress_lines, stop_process
from ..utils.timestamps import format_timestamp


def extract(owner, cmd, duration, path, progress):
    owner.process = None
    success = False
    try:
        owner.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        errors = []
        for line in progress_lines(owner, owner.process):
            if line.startswith('out_time_ms='):
                try:
                    ms = int(line.split('=', 1)[1]) // 1000
                    progress(min(99, int(ms * 100 / max(1, duration))), format_timestamp(ms))
                except ValueError:
                    pass
            elif '=' not in line:
                errors.append(line.strip())
                errors = errors[-30:]
        owner.process.wait()
        success = not owner.cancel_requested and owner.process.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 0
        return success, '\n'.join(errors)
    except Exception as exc:
        return False, str(exc)
    finally:
        stop_process(owner.process)
        if owner.process and owner.process.stdout:
            close_stream(owner.process.stdout)
        if owner.process is not None and not success:
            if os.path.isfile(path):
                remove_file(path)


class ClipExtractionWorker(ManagedWorker):
    result_format = ResultFormat.FILE
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)

    def __init__(self, cmd, duration_ms, clip_path):
        super().__init__()
        self.cmd, self.duration_ms, self.clip_path = cmd, duration_ms, clip_path
        self.cancel_requested = False

    def _run(self):
        success, error = extract(self, self.cmd, self.duration_ms, self.clip_path, self.progress.emit)
        self.finished.emit(success, self.cancel_requested, self.clip_path, error)


class MultiSegmentWorker(ManagedWorker):
    result_format = ResultFormat.SEGMENTS
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, int, list, str)

    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks
        self.cancel_requested = False

    def _run(self):
        paths = []
        try:
            for i, task in enumerate(self.tasks):
                if self.cancel_requested:
                    break
                success, error = extract(self, task['cmd'], task['duration_ms'], task['output_path'],
                    lambda pct, label: self.progress.emit(int((i + pct / 100) * 100 / len(self.tasks)), label))
                if not success:
                    self.finished.emit(False, len(paths), paths, error or 'Cancelled' if self.cancel_requested else error or 'Segment extraction failed')
                    return
                paths.append(task['output_path'])
                self.output_paths.append(task['output_path'])
            self.finished.emit(not self.cancel_requested, len(paths), paths, 'Cancelled' if self.cancel_requested else 'Segments extracted.')
        finally:
            for task in self.tasks:
                if task.get('temp_srt'):
                    remove_file(task['temp_srt'])
