import os
import shutil
import subprocess
from typing import Optional, List

from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.timestamps import format_timestamp
from ..media.hwaccel import probe_capabilities, get_hwaccel_encode_args
from ..ai.rife import find_rife_model, is_rife_available, get_model_status_info, RIFEInterpolator


class InterpolationWorker(QThread):
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

    def run(self):
        if not os.path.isfile(self.source_file):
            self.finished.emit(False, False, "", f"Source file does not exist: {self.source_file}")
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, False, "", "FFmpeg not found on PATH.")
            return

        if self.engine == "rife":
            self._run_rife_pipeline(ffmpeg)
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

    def _resolve_codec_args(self) -> List[str]:
        """Resolve video codec and quality arguments with optional hardware acceleration."""
        if self.hwaccel_enabled:
            caps = probe_capabilities()
            if self.video_codec in ("libx265", "hevc"):
                hw_args = get_hwaccel_encode_args(caps, codec="hevc")
            else:
                hw_args = get_hwaccel_encode_args(caps, codec="h264")
            if hw_args:
                # E.g. ['-c:v', 'h264_nvenc']
                encoder = hw_args[1] if len(hw_args) > 1 else ""
                if "nvenc" in encoder:
                    return hw_args + ["-cq", str(self.quality_crf), "-preset", "p4", "-pix_fmt", "yuv420p"]
                elif "qsv" in encoder:
                    return hw_args + ["-global_quality", str(self.quality_crf), "-pix_fmt", "nv12"]
                elif "vaapi" in encoder:
                    return hw_args + ["-qp", str(self.quality_crf)]
                return hw_args + ["-pix_fmt", "yuv420p"]

        # Software encoder fallback
        codec = "libx265" if self.video_codec in ("libx265", "hevc") else "libx264"
        return [
            "-c:v", codec,
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
            "-i", self.source_file,
            "-vf", vf,
        ]
        if self.engine == "conform":
            cmd.extend(["-r", f"{self.target_fps:.4f}"])

        cmd.extend(codec_args)

        if self.audio_mode == "mute":
            cmd.append("-an")
        else:
            # Preserving audio stream
            cmd.extend(["-c:a", "copy"])

        cmd.extend(["-progress", "pipe:1", self.output_path])

        self.progress.emit(0, "Starting interpolation...")

        ffmpeg_error = ""
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            while self.process.poll() is None:
                if self.cancel_requested:
                    self._kill_process()
                    break

                line = self.process.stdout.readline()
                if line:
                    line = line.strip()
                    if line.startswith("out_time_ms="):
                        try:
                            out_ms = int(line.split("=", 1)[1]) // 1000
                            pct = int(min(99, max(0, out_ms * 100 / self.duration_ms)))
                            self.progress.emit(
                                pct,
                                f"Interpolating: {format_timestamp(out_ms)} / {format_timestamp(self.duration_ms)} ({self.target_fps:.1f} FPS)"
                            )
                        except (ValueError, ZeroDivisionError):
                            pass

            _, stderr_data = self.process.communicate()
            if stderr_data:
                ffmpeg_error = stderr_data.strip()

        except Exception as exc:
            ffmpeg_error = str(exc)
            self._kill_process()

        if self.cancel_requested:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError:
                    pass
            self.finished.emit(False, True, self.output_path, "Cancelled by user.")
            return

        success = (
            self.process is not None
            and self.process.returncode == 0
            and os.path.isfile(self.output_path)
            and os.path.getsize(self.output_path) > 0
        )

        if success:
            self.progress.emit(100, "Interpolation complete!")
        self.finished.emit(success, False, self.output_path, ffmpeg_error)

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
        if not cap.isOpened():
            self.finished.emit(False, False, "", f"Failed to read source video: {self.source_file}")
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        codec_args = self._resolve_codec_args()

        # Pipe raw RGB/BGR frames to FFmpeg encoder
        enc_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", f"{self.target_fps:.4f}",
            "-i", "pipe:0",
        ]

        if self.audio_mode != "mute":
            enc_cmd.extend(["-i", self.source_file, "-map", "0:v", "-map", "1:a?", "-c:a", "copy"])

        enc_cmd.extend(codec_args)
        enc_cmd.append(self.output_path)

        try:
            self.process = subprocess.Popen(
                enc_cmd,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10**7
            )
        except Exception as exc:
            cap.release()
            self.finished.emit(False, False, "", f"Failed to launch FFmpeg encoder: {exc}")
            return

        # Start multi-threaded reader and writer to overlap I/O with GPU inference
        in_q = queue.Queue(maxsize=10)
        out_q = queue.Queue(maxsize=10)

        def reader():
            while not self.cancel_requested:
                r, f = cap.read()
                if not r or f is None:
                    break
                in_q.put(f)
            in_q.put(None)
            cap.release()

        def writer():
            while not self.cancel_requested:
                item = out_q.get()
                if item is None:
                    break
                try:
                    self.process.stdin.write(item)
                except Exception:
                    break

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=writer, daemon=True).start()

        prev_frame = in_q.get()
        if prev_frame is None:
            self._kill_process()
            self.finished.emit(False, False, "", "Could not read initial video frame.")
            out_q.put(None)
            return

        # Write first frame
        out_q.put(prev_frame.tobytes())

        frame_idx = 1
        ffmpeg_error = ""

        try:
            while not self.cancel_requested:
                curr_frame = in_q.get()
                if curr_frame is None:
                    break

                # Generate intermediate frame
                mid_frame = interpolator.interpolate_pair(prev_frame, curr_frame)

                # Write intermediate frame and current frame
                out_q.put(mid_frame.tobytes())
                out_q.put(curr_frame.tobytes())

                prev_frame = curr_frame
                frame_idx += 1

                provider_name = getattr(interpolator, "active_provider", "AI")
                if "CUDA" in provider_name:
                    dev_label = "CUDA GPU"
                elif "Dml" in provider_name:
                    dev_label = "DirectML GPU"
                elif "CPU" in provider_name:
                    dev_label = "CPU"
                else:
                    dev_label = provider_name

                pct = int(min(99, frame_idx * 100 / total_frames))
                self.progress.emit(
                    pct,
                    f"AI RIFE [{dev_label}]: Frame {frame_idx}/{total_frames} ({pct}%)"
                )

        except Exception as exc:
            ffmpeg_error = str(exc)

        out_q.put(None)

        try:
            if self.process.stdin:
                self.process.stdin.close()
            _, stderr_data = self.process.communicate(timeout=15)
            if stderr_data:
                ffmpeg_error = stderr_data.decode("utf-8", errors="ignore").strip()
        except Exception:
            self._kill_process()

        if self.cancel_requested:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError:
                    pass
            self.finished.emit(False, True, self.output_path, "Cancelled by user.")
            return

        success = (
            self.process is not None
            and self.process.returncode == 0
            and os.path.isfile(self.output_path)
            and os.path.getsize(self.output_path) > 0
        )

        if success:
            self.progress.emit(100, "AI Interpolation complete!")
        self.finished.emit(success, False, self.output_path, ffmpeg_error)

    def _kill_process(self):
        """Safely terminate or kill running FFmpeg subprocess."""
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait()
                except Exception:
                    pass
