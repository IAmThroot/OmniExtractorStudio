from ..media.process import remove_file
from .base import ManagedWorker, ResultFormat
import os
import shutil
import subprocess
from typing import Optional, List

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.timestamps import format_timestamp
from ..media.hwaccel import probe_capabilities, get_hwaccel_encode_profile
from ..ai.rife import find_rife_model, is_rife_available, get_model_status_info, RIFEInterpolator


class InterpolationWorker(ManagedWorker):
    result_format = ResultFormat.FILE
    """
    Worker thread for video framerate changing and motion-compensated frame interpolation.
    Supports FFmpeg minterpolate, standard fps resampling, and optional RIFE AI neural interpolation.
    """
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)  # success, cancelled, output_path, error_message

    def __init__(
        self,
        source_file: str,
        output_path: str,
        target_fps: float,
        duration_ms: int = 0,
        source_fps: float = 0.0,
        engine: str = "ffmpeg",           # "ffmpeg", "resample", "rife"
        mi_mode: str = "mci",             # "mci", "blend"
        mc_mode: str = "aobmc",           # "aobmc", "obmc"
        me_algo: str = "epzs",            # "epzs", "hexbs", "esa"
        scd_enabled: bool = True,
        scd_threshold: float = 10.0,
        video_codec: str = "libx264",     # "libx264", "libx265", "auto"
        quality_crf: int = 20,
        hwaccel_enabled: bool = False,
        audio_mode: str = "copy",         # "copy", "mute"
        rife_model_path: Optional[str] = None,
    ):
        super().__init__()
        self.source_file = source_file
        self.output_path = output_path
        self.target_fps = float(target_fps)
        self.duration_ms = max(1, duration_ms)
        self.source_fps = float(source_fps)
        self.engine = engine
        self.mi_mode = mi_mode
        self.mc_mode = mc_mode
        self.me_algo = me_algo
        self.scd_enabled = scd_enabled
        self.scd_threshold = scd_threshold
        self.video_codec = video_codec
        self.quality_crf = quality_crf
        self.hwaccel_enabled = hwaccel_enabled
        self.audio_mode = audio_mode
        self.rife_model_path = rife_model_path

        self.cancel_requested = False
        self.process = None

    def cancel(self):
        """Request task cancellation."""
        self.cancel_requested = True
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _run(self):
        if not os.path.isfile(self.source_file):
            self.finished.emit(False, False, "", f"Source file does not exist: {self.source_file}")
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, False, "", "FFmpeg not found on PATH.")
            return

        if self.engine == "rife":
            self._rife_cap = None
            try:
                self._run_rife_pipeline(ffmpeg)
            finally:
                if self._rife_cap is not None:
                    self._rife_cap.release()
        else:
            self._run_ffmpeg_pipeline(ffmpeg)

    def _build_filter_string(self) -> str:
        """Construct the FFmpeg video filter for framerate change / interpolation."""
        fps_str = f"{self.target_fps:.4f}".rstrip("0").rstrip(".")

        if self.engine == "resample":
            return f"fps=fps={fps_str}"

        if self.engine == "conform":
            src_fps = self.source_fps if self.source_fps > 0 else self.target_fps
            return f"setpts=({src_fps:.4f}/{self.target_fps:.4f})*PTS"

        # FFmpeg minterpolate
        if self.mi_mode == "blend":
            return f"minterpolate=fps={fps_str}:mi_mode=blend"

        # Motion Compensated Interpolation (MCI)
        parts = [
            f"fps={fps_str}",
            "mi_mode=mci",
            f"mc_mode={self.mc_mode}",
            "me_mode=bidir",
            f"me={self.me_algo}",
            "vsbmc=1",
        ]
        if self.scd_enabled:
            parts.append("scd=fdiff")
            parts.append(f"scd_threshold={self.scd_threshold:.1f}")
        else:
            parts.append("scd=none")

        return f"minterpolate={':'.join(parts)}"

    def _audio_tempo_filter(self):
        ratio = self.target_fps / self.source_fps
        factors = []
        while ratio < 0.5:
            factors.append(0.5)
            ratio /= 0.5
        while ratio > 2:
            factors.append(2.0)
            ratio /= 2
        factors.append(ratio)
        return ','.join(f'atempo={value:.8f}' for value in factors)

    def _resolve_codec_args(self) -> List[str]:
        """Resolve video codec and quality arguments with optional hardware acceleration."""
        codec = "hevc" if self.video_codec in ("libx265", "hevc") else "h264"
        if self.hwaccel_enabled:
            caps = probe_capabilities()
            return get_hwaccel_encode_profile(caps, codec=codec, quality_crf=self.quality_crf, preset="medium")

        # Software encoder fallback
        sw_codec = "libx265" if codec == "hevc" else "libx264"
        return [
            "-c:v", sw_codec,
            "-crf", str(self.quality_crf),
            "-preset", "medium",
            "-pix_fmt", "yuv420p"
        ]

    def _run_ffmpeg_pipeline(self, ffmpeg: str):
        """Execute FFmpeg minterpolate, conform, or fps filter command with progress streaming."""
        vf = self._build_filter_string()
        codec_args = self._resolve_codec_args()

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-threads", "0", "-filter_threads", "0",
            *(["-r", f"{self.target_fps:.4f}"] if self.engine == "conform" else []),
            "-i", self.source_file,
            "-vf", "setpts=PTS-STARTPTS" if self.engine == "conform" else vf,
        ]
        if self.engine == "conform":
            cmd.extend(["-fps_mode", "passthrough"])

        cmd.extend(codec_args)

        if self.audio_mode == "mute":
            cmd.append("-an")
        elif self.engine == "conform":
            cmd.extend(["-af", self._audio_tempo_filter(), "-c:a", "aac"])
        else:
            # Preserving audio stream
            cmd.extend(["-c:a", "copy"])

        cmd.extend(["-progress", "pipe:1", self.output_path])

        from .clip_worker import extract
        if self.engine == "conform":
            self.duration_ms = round(self.duration_ms * self.source_fps / self.target_fps)
        success, error = extract(self, cmd, self.duration_ms, self.output_path, self.progress.emit)
        self.finished.emit(success, self.cancel_requested, self.output_path, error)

    def _run_rife_pipeline(self, ffmpeg: str):
        """Execute AI neural interpolation with RIFE ONNX model."""
        import cv2
        import threading
        import queue

        status_text, is_ready, model_path = get_model_status_info(self.rife_model_path)
        if not is_ready:
            self.finished.emit(False, False, "", status_text)
            return

        try:
            interpolator = RIFEInterpolator(model_path)
        except Exception as exc:
            self.finished.emit(False, False, "", f"Failed to initialize AI model: {exc}")
            return

        cap = cv2.VideoCapture(self.source_file)
        self._rife_cap = cap
        if not cap.isOpened():
            self.finished.emit(False, False, "", f"Failed to read source video: {self.source_file}")
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        codec_args = self._resolve_codec_args()

        # Determine multiplier and recursion depth from target vs source FPS
        src_fps = max(1.0, float(self.source_fps))
        ratio = self.target_fps / src_fps

        if ratio <= 1.05:
            multiplier = 1
            depth = 0
        elif ratio <= 2.1:
            multiplier = 2
            depth = 1
        elif ratio <= 4.2:
            multiplier = 4
            depth = 2
        else:
            multiplier = 8
            depth = 3

        pipe_fps = src_fps * multiplier

        # Pipe raw RGB/BGR frames to FFmpeg encoder
        enc_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", f"{pipe_fps:.4f}",
            "-i", "pipe:0",
        ]

        if self.audio_mode != "mute":
            enc_cmd.extend(["-i", self.source_file, "-map", "0:v", "-map", "1:a?", "-c:a", "copy"])

        if abs(pipe_fps - self.target_fps) > 0.01:
            enc_cmd.extend(["-vf", f"fps=fps={self.target_fps:.4f}"])

        enc_cmd.extend(codec_args)
        enc_cmd.append(self.output_path)

        import tempfile
        stderr_file = tempfile.TemporaryFile()
        stop = threading.Event()
        errors = []
        in_q = queue.Queue(maxsize=2)
        out_q = queue.Queue(maxsize=2)

        def put(q, item):
            while not stop.is_set() and not self.cancel_requested:
                try:
                    q.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    pass
            return False

        def get(q):
            while not stop.is_set() and not self.cancel_requested:
                try:
                    return q.get(timeout=0.1)
                except queue.Empty:
                    pass
            return None

        def fail(exc):
            errors.append(str(exc))
            stop.set()

        def reader():
            try:
                while not stop.is_set() and not self.cancel_requested:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if not put(in_q, frame):
                        break
                put(in_q, None)
            except Exception as exc:
                fail(exc)
            finally:
                cap.release()

        def writer():
            try:
                while not stop.is_set() and not self.cancel_requested:
                    item = get(out_q)
                    if item is None:
                        break
                    remaining = memoryview(item)
                    while remaining and not stop.is_set() and not self.cancel_requested:
                        written = self.process.stdin.write(remaining)
                        if not written:
                            raise BrokenPipeError('Encoder input closed')
                        remaining = remaining[written:]
                self.process.stdin.close()
            except Exception as exc:
                fail(exc)

        def intermediates(f0, f1, depth):
            if depth <= 0 or stop.is_set() or self.cancel_requested:
                return
            mid = interpolator.interpolate_pair(f0, f1)
            yield from intermediates(f0, mid, depth - 1)
            yield mid
            yield from intermediates(mid, f1, depth - 1)

        threads = []
        try:
            self.process = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=stderr_file, bufsize=0)
            threads = [threading.Thread(target=reader, name='rife-reader'),
                       threading.Thread(target=writer, name='rife-writer')]
            for thread in threads:
                thread.start()
            previous = get(in_q)
            if previous is None and not self.cancel_requested:
                raise RuntimeError('Could not read initial video frame.')
            frame_idx = 0
            while previous is not None and not stop.is_set() and not self.cancel_requested:
                if not put(out_q, previous.tobytes()):
                    break
                current = get(in_q)
                if current is None:
                    # Hold the final source frame for its full source-frame interval.
                    for _ in range(multiplier - 1):
                        if not put(out_q, previous.tobytes()):
                            break
                    break
                for frame in intermediates(previous, current, depth):
                    if not put(out_q, frame.tobytes()):
                        break
                previous = current
                frame_idx += 1
                self.progress.emit(min(99, int(frame_idx * 100 / total_frames)),
                                   f'AI RIFE: {frame_idx}/{total_frames}')
            put(out_q, None)
            # Successful completion drains the queue. Failure/cancellation kills
            # the encoder to interrupt a writer blocked on its input pipe.
            while threads[1].is_alive():
                if stop.is_set() or self.cancel_requested:
                    self._kill_process()
                threads[1].join(0.1)
            self.process.wait(timeout=15)
        except Exception as exc:
            fail(exc)
        finally:
            stop.set()
            self._kill_process()
            for thread in threads:
                thread.join()
            cap.release()
            if self.process and self.process.stdin:
                self.process.stdin.close()
            stderr_file.seek(0)
            stderr = stderr_file.read().decode('utf-8', errors='replace')
            stderr_file.close()

        success = (not errors and not self.cancel_requested and self.process is not None
                   and self.process.returncode == 0 and os.path.isfile(self.output_path)
                   and os.path.getsize(self.output_path) > 0)
        if not success and os.path.isfile(self.output_path):
            remove_file(self.output_path)
        self.finished.emit(success, self.cancel_requested, self.output_path,
                           '; '.join(errors) or stderr)

    def _kill_process(self):
        from ..media.process import stop_process
        stop_process(self.process)
