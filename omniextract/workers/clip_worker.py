import os
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.timestamps import format_timestamp


class ClipExtractionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)

    def __init__(self, cmd, duration_ms, clip_path):
        super().__init__()
        self.cmd = cmd
        self.duration_ms = duration_ms
        self.clip_path = clip_path
        self.cancel_requested = False

    def run(self):
        process = None
        ffmpeg_error = ""

        try:
            process = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            while process.poll() is None:
                line = process.stdout.readline()
                if line:
                    line = line.strip()
                    if line.startswith("out_time_ms="):
                        try:
                            out_ms = int(line.split("=", 1)[1]) // 1000
                            pct = int(min(99, out_ms * 100 / self.duration_ms))
                            self.progress.emit(pct, f"Extracting: {format_timestamp(out_ms)} / {format_timestamp(self.duration_ms)}")
                        except (ValueError, ZeroDivisionError):
                            pass

                if self.cancel_requested:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break

            process.communicate()
        except Exception as exc:
            ffmpeg_error = str(exc)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

        if self.cancel_requested:
            self.finished.emit(False, True, self.clip_path, "Cancelled")
            return

        success = (process.returncode == 0 and os.path.isfile(self.clip_path) and os.path.getsize(self.clip_path) > 0)
        self.finished.emit(success, False, self.clip_path, ffmpeg_error)


class MultiSegmentWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, int, list, str)

    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks
        self.cancel_requested = False
        self.process = None

    def run(self):
        total_tasks = len(self.tasks)
        if total_tasks == 0:
            self.finished.emit(True, 0, [], "No segments to extract.")
            return

        extracted_paths = []
        total_duration_ms = sum(t["duration_ms"] for t in self.tasks) or 1
        accumulated_ms = 0

        for i, task in enumerate(self.tasks):
            if self.cancel_requested:
                break

            cmd = task["cmd"]
            duration_ms = task["duration_ms"]
            out_path = task["output_path"]
            name = task.get("name", f"Segment {i + 1}")

            self.progress.emit(
                int((i / total_tasks) * 100),
                f"[{i + 1}/{total_tasks}] {name}..."
            )

            try:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )
                while self.process.poll() is None:
                    line = self.process.stdout.readline()
                    if line:
                        line = line.strip()
                        if line.startswith("out_time_ms="):
                            try:
                                seg_out_ms = int(line.split("=", 1)[1]) // 1000
                                current_total_ms = accumulated_ms + seg_out_ms
                                pct = int(min(99, (current_total_ms / total_duration_ms) * 100))
                                self.progress.emit(pct, f"[{i + 1}/{total_tasks}] {name} ({format_timestamp(seg_out_ms)} / {format_timestamp(duration_ms)})")
                            except (ValueError, ZeroDivisionError):
                                pass

                    if self.cancel_requested:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait()
                        break

                self.process.communicate()
            except Exception as exc:
                if self.process is not None and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait()
                self._cleanup_temp_srts()
                self.finished.emit(False, len(extracted_paths), extracted_paths, str(exc))
                return

            if task.get("temp_srt") and os.path.exists(task["temp_srt"]):
                try: os.remove(task["temp_srt"])
                except OSError: pass

            if self.cancel_requested:
                if os.path.exists(out_path):
                    try: os.remove(out_path)
                    except OSError: pass
                break

            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                extracted_paths.append(out_path)
                accumulated_ms += duration_ms
            else:
                self._cleanup_temp_srts()
                self.finished.emit(False, len(extracted_paths), extracted_paths, f"Failed to extract segment: {name}")
                return

        self._cleanup_temp_srts()
        if self.cancel_requested:
            self.finished.emit(False, len(extracted_paths), extracted_paths, "Cancelled")
        else:
            self.progress.emit(100, f"Completed {len(extracted_paths)} segments!")
            self.finished.emit(True, len(extracted_paths), extracted_paths, f"Successfully extracted {len(extracted_paths)} segments.")

    def _cleanup_temp_srts(self):
        for task in self.tasks:
            if task.get("temp_srt") and os.path.exists(task["temp_srt"]):
                try: os.remove(task["temp_srt"])
                except OSError: pass
