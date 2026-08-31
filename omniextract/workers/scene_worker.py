import re
import shutil
import subprocess

import cv2
from PyQt6.QtCore import QThread, pyqtSignal


class SceneDetectionWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, int, float)
    error = pyqtSignal(str)

    def __init__(self, source_file, threshold):
        super().__init__()
        self.source_file = source_file
        self.threshold = threshold
        self.cancel_requested = False
        self.process = None

    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("Could not open video.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = (frame_count / fps) if fps > 0 else 1.0
        cap.release()

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            self._run_ffmpeg(ffmpeg, fps, frame_count, duration_sec)
        else:
            self._run_opencv(fps, frame_count)

    def _run_ffmpeg(self, ffmpeg, fps, frame_count, duration_sec):
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i", self.source_file,
            "-vf", f"select='gt(scene,{self.threshold})',showinfo",
            "-f", "null",
            "-",
            "-progress", "pipe:2"
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        except Exception as e:
            self.error.emit(f"Failed to start FFmpeg: {e}")
            return

        scenes = []
        pts_regex = re.compile(r"pts_time:([0-9.]+)")

        while not self.cancel_requested:
            line = self.process.stderr.readline()
            if not line and self.process.poll() is not None:
                break
            if not line:
                continue

            if "pts_time:" in line:
                match = pts_regex.search(line)
                if match:
                    pts_time = float(match.group(1))
                    frame_num = round(pts_time * fps)
                    if not scenes or scenes[-1] != frame_num:
                        scenes.append(frame_num)
            elif line.startswith("out_time_us="):
                us_val = line.split("=", 1)[1].strip()
                if us_val.isdigit() and duration_sec > 0:
                    cur_sec = int(us_val) / 1_000_000.0
                    pct = int(min(99, (cur_sec / duration_sec) * 100))
                    self.progress.emit(pct)
            elif line.startswith("out_time_ms="):
                ms_val = line.split("=", 1)[1].strip()
                if ms_val.isdigit() and duration_sec > 0:
                    cur_sec = (int(ms_val) / 1_000_000.0) if int(ms_val) > 100000 else (int(ms_val) / 1000.0)
                    pct = int(min(99, (cur_sec / duration_sec) * 100))
                    self.progress.emit(pct)

        if self.cancel_requested:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            return

        self.process.wait()
        self.finished.emit(scenes, frame_count, fps)

    def _run_opencv(self, fps, frame_count):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("Could not open video.")
            return

        previous = None
        scenes = []
        frame_number = 0

        while not self.cancel_requested:
            success, frame = cap.read()
            if not success:
                break

            small = cv2.resize(frame, (160, 90))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if previous is not None:
                difference = cv2.absdiff(previous, gray)
                score = float(difference.mean()) / 255.0

                if score >= self.threshold:
                    scenes.append(frame_number)

            previous = gray
            frame_number += 1

            if frame_number % 50 == 0:
                self.progress.emit(int(frame_number / max(1, frame_count) * 100))

        cap.release()
        if not self.cancel_requested:
            self.finished.emit(scenes, frame_count, fps)


class SceneActionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, action, scenes, source_file, save_dir, extension, format_cb, path_cb, fps, frame_count):
        super().__init__()
        self.action = action
        self.scenes = scenes
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.format_cb = format_cb
        self.path_cb = path_cb
        self.fps = fps
        self.frame_count = frame_count
        self.cancel_requested = False

    def run(self):
        if self.action == "keyframes":
            self._extract_keyframes()
        elif self.action == "clips":
            self._split_clips()

    def _extract_keyframes(self):
        cap = cv2.VideoCapture(self.source_file)
        used_names = set()

        for i, frame_num in enumerate(self.scenes):
            if self.cancel_requested:
                break

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            success, image = cap.read()
            if success:
                ts = round((frame_num / self.fps) * 1000) if self.fps else 0
                filename = self.format_cb(frame_num, ts, self.extension)
                path = self.path_cb(self.save_dir, f"scene_{i+1}_{filename}", used_names)
                cv2.imwrite(path, image)

            pct = int((i + 1) / len(self.scenes) * 100)
            self.progress.emit(pct, f"Extracted scene keyframe {i+1} of {len(self.scenes)}")

        cap.release()
        self.finished.emit(True, "Keyframes extracted successfully.")

    def _split_clips(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, "FFmpeg not found.")
            return

        scenes_ms = [round((f / self.fps) * 1000) for f in self.scenes]
        duration_ms = round((self.frame_count / self.fps) * 1000)
        scenes_ms.append(duration_ms)

        if scenes_ms[0] > 0:
            scenes_ms.insert(0, 0)

        used_names = set()
        for i in range(len(scenes_ms) - 1):
            if self.cancel_requested:
                break

            start_ms = scenes_ms[i]
            end_ms = scenes_ms[i + 1]
            if end_ms <= start_ms:
                continue

            start_sec = start_ms / 1000.0
            dur_sec = (end_ms - start_ms) / 1000.0

            ext = self.extension.lstrip(".")
            out_path = self.path_cb(self.save_dir, f"scene_{i+1}_clip.{ext}", used_names)

            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_sec:.6f}", "-i", self.source_file,
                "-t", f"{dur_sec:.6f}", "-c:v", "copy", "-c:a", "copy",
                "-avoid_negative_ts", "make_zero", out_path
            ]
            subprocess.run(cmd, capture_output=True)
            pct = int((i + 1) / (len(scenes_ms) - 1) * 100)
            self.progress.emit(pct, f"Extracted clip {i+1} of {len(scenes_ms)-1}")

        self.finished.emit(True, "Clips split successfully.")
