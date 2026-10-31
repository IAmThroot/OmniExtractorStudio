from ..media.process import remove_file
from ..media.process import run_cancellable
from .base import ManagedWorker, ResultFormat
import os
import shutil
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.timestamps import format_timestamp


class AnimatedExportWorker(ManagedWorker):
    result_format = ResultFormat.FILE
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)

    def __init__(self, source_file, output_path, start_ms, end_ms, output_format,
                 width, fps, dither, quality, loop_count, lossless, subtitle_filter=""):
        super().__init__()
        self.source_file = source_file
        self.output_path = output_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.output_format = output_format
        self.width = width
        self.fps = fps
        self.dither = dither
        self.quality = quality
        self.loop_count = loop_count
        self.lossless = lossless
        self.subtitle_filter = subtitle_filter
        self.cancel_requested = False
        self.process = None

    def _run(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, False, "", "FFmpeg not found on PATH.")
            return

        duration_ms = self.end_ms - self.start_ms
        if duration_ms <= 0:
            self.finished.emit(False, False, "", "Invalid time range.")
            return

        start_sec = self.start_ms / 1000.0
        dur_sec = duration_ms / 1000.0

        scale_filter = f"scale={self.width}:-1:flags=lanczos" if self.width > 0 else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        sub_prefix = f"{self.subtitle_filter}," if self.subtitle_filter else ""

        if self.output_format == "GIF":
            try:
                self._export_gif(ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix)
            finally:
                self._cleanup(self.output_path + ".palette.png")
        else:
            self._export_webp(ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix)

    def _export_gif(self, ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix):
        palette_path = self.output_path + ".palette.png"

        self.progress.emit(5, "Generating palette...")
        palette_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-vf", f"{sub_prefix}fps={self.fps},{scale_filter},palettegen=stats_mode=diff",
            palette_path
        ]

        try:
            result = run_cancellable(self, palette_cmd)
            if result.returncode != 0 or not os.path.isfile(palette_path):
                self.finished.emit(False, False, "", f"Palette generation failed: {result.stderr}")
                return
        except subprocess.TimeoutExpired:
            self.finished.emit(False, False, "", "Palette generation timed out.")
            return

        if self.cancel_requested:
            self._cleanup(palette_path)
            self.finished.emit(False, True, "", "Cancelled")
            return

        self.progress.emit(20, "Rendering GIF...")
        gif_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-i", palette_path,
            "-lavfi", f"{sub_prefix}fps={self.fps},{scale_filter} [x]; [x][1:v] paletteuse=dither={self.dither}",
            "-loop", str(self.loop_count),
            "-progress", "pipe:1",
            self.output_path
        ]

        self._run_ffmpeg_with_progress(gif_cmd, duration_ms, 20)
        self._cleanup(palette_path)

    def _export_webp(self, ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix):
        self.progress.emit(5, "Rendering WebP...")

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-vf", f"{sub_prefix}fps={self.fps},{scale_filter}",
            "-c:v", "libwebp_anim",
            "-quality", str(self.quality),
            "-loop", str(self.loop_count),
            "-an",
            "-progress", "pipe:1",
            self.output_path
        ]

        if self.lossless:
            cmd.insert(-1, "-lossless")
            cmd.insert(-1, "1")

        self._run_ffmpeg_with_progress(cmd, duration_ms, 5)

    def cancel(self):
        self.cancel_requested = True
        if self.process is not None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _run_ffmpeg_with_progress(self, cmd, duration_ms, base_pct):
        from .clip_worker import extract
        success, error = extract(self, cmd, duration_ms, self.output_path, self.progress.emit)
        self.finished.emit(success, self.cancel_requested, self.output_path,
                           'Animation saved.' if success else error or 'Export failed')

    def _cleanup(self, *paths):
        for p in paths:
            try:
                if os.path.isfile(p):
                    remove_file(p)
            except OSError:
                pass
