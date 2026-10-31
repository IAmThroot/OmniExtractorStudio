"""Common worker outcome and cooperative subprocess ownership."""
from dataclasses import dataclass, field
from enum import Enum

from PyQt6.QtCore import QThread, Qt, pyqtSignal


class ResultFormat(Enum):
    """Legacy completion signal layouts, inherited by worker subclasses."""
    SIMPLE = 'success, message'
    FRAMES = 'count, elapsed, cancelled'
    SCENES = 'scenes, frame_count, fps'
    FILE = 'success, cancelled, path, error'
    COUNTED = 'success, count, message'
    SEGMENTS = 'success, count, paths, error'


class JobStatus(str, Enum):
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


@dataclass
class JobOutcome:
    status: JobStatus
    count: int = 0
    paths: list = field(default_factory=list)
    error: str = ''


class ManagedWorker(QThread):
    """Keep legacy result signals while providing one consistent final outcome."""
    result_format = ResultFormat.SIMPLE
    outcome_ready = pyqtSignal(object)

    def run(self):
        self.result_args = None
        self.failure = ''
        self.output_paths = []
        def capture(*args):
            if self.result_args is None:
                self.result_args = args
        def capture_error(message):
            self.failure = message
        if hasattr(self, 'error'):
            self.error.connect(capture_error, Qt.ConnectionType.DirectConnection)
        self.finished.connect(capture, Qt.ConnectionType.DirectConnection)
        try:
            self._run()
        except Exception as exc:
            self.failure = str(exc)
        finally:
            from ..media.process import close_stream, stop_process
            process = getattr(self, 'process', None)
            stop_process(process)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    close_stream(stream)
            self.finished.disconnect(capture)
            if hasattr(self, "error"):
                self.error.disconnect(capture_error)
            args = self.result_args
            cancelled = getattr(self, 'cancel_requested', False)
            failed = bool(self.failure) or args is None
            count = len(self.output_paths)
            if args:
                if self.result_format == ResultFormat.FRAMES:
                    count = args[0]
                    cancelled |= args[2]
                elif self.result_format == ResultFormat.SCENES:
                    count = len(args[0])
                else:
                    failed |= not args[0]
                    if self.result_format == ResultFormat.FILE:
                        cancelled |= args[1]
                        self.output_paths = [args[2]] if args[0] and args[2] else []
                        count = len(self.output_paths)
                    elif self.result_format in (ResultFormat.COUNTED, ResultFormat.SEGMENTS):
                        count = args[1]
                        if self.result_format == ResultFormat.SEGMENTS:
                            self.output_paths = list(args[2])
                    if failed and isinstance(args[-1], str):
                        self.failure = self.failure or args[-1]
            if failed and not self.failure and not cancelled:
                self.failure = 'Processing failed without a valid output.'
            self.outcome = JobOutcome(JobStatus.CANCELLED if cancelled else JobStatus.FAILED if failed else JobStatus.SUCCESS,
                                      count, self.output_paths, self.failure)
            self.outcome_ready.emit(self.outcome)

    def cancel(self):
        self.cancel_requested = True

    def native_finished(self):
        return QThread.finished.__get__(self, type(self))
