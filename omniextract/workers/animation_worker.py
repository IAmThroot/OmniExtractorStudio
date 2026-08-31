import os
import shutil
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.timestamps import format_timestamp


class AnimatedExportWorker(QThread):
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

    def run(self):
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
            self._export_gif(ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix)
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
            result = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=120)
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

    def _run_ffmpeg_with_progress(self, cmd, duration_ms, base_pct):
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1
            )
        except Exception as e:
            self.finished.emit(False, False, "", str(e))
            return

        while self.process.poll() is None:
            line = self.process.stdout.readline()
            if line:
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        out_us = int(line.split("=", 1)[1])
                        out_ms = out_us // 1000
                        pct = base_pct + int(min(100 - base_pct, (out_ms / max(1, duration_ms)) * (100 - base_pct)))
                        self.progress.emit(pct, f"Encoding: {format_timestamp(out_ms)} / {format_timestamp(duration_ms)}")
                    except (ValueError, ZeroDivisionError):
                        pass

            if self.cancel_requested:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.finished.emit(False, True, "", "Cancelled")
                return

        self.process.wait()
        stderr_output = self.process.stderr.read()

        if self.process.returncode == 0 and os.path.isfile(self.output_path) and os.path.getsize(self.output_path) > 0:
            size_kb = os.path.getsize(self.output_path) / 1024
            self.finished.emit(True, False, self.output_path, f"Output size: {size_kb:.1f} KB")
        else:
            self.finished.emit(False, False, "", stderr_output or "Export failed.")

    def _cleanup(self, *paths):
        for p in paths:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
