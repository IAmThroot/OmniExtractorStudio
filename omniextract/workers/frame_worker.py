from ..media.process import remove_file
from .base import ManagedWorker, ResultFormat
import concurrent.futures
import csv
import os
import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from ..media.subtitles import draw_subtitle_on_frame
from ..utils.timestamps import format_timestamp


class FrameExtractionWorker(ManagedWorker):
    result_format = ResultFormat.FRAMES
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, float, bool)
    error = pyqtSignal(str)

    def __init__(self, source_file, save_dir, extension, start_frame, end_frame, step, render_filename_cb, unique_path_cb, quality, export_manifest, image_format, filter_blur=False, blur_threshold=100.0, subtitles=None):
        super().__init__()
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.step = step
        self.render_filename_cb = render_filename_cb
        self.unique_path_cb = unique_path_cb
        self.quality = quality
        self.export_manifest = export_manifest
        self.image_format = image_format
        self.filter_blur = filter_blur
        self.blur_threshold = blur_threshold
        self.subtitles = subtitles or []
        self.cancel_requested = False

    def _run(self):
        cap = cv2.VideoCapture(self.source_file)
        self._manifest_file = None
        try:
            self._process(cap)
        finally:
            cap.release()
            if self._manifest_file:
                self._manifest_file.close()

    def _process(self, cap):
        if not cap.isOpened():
            self.error.emit("The selected source file could not be opened.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        current_frame = self.start_frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_frame)
        current_pos = self.start_frame

        used_names = set()
        extracted = 0
        skipped = 0
        start_clock = time.time()

        encode_params = []
        if self.image_format == "JPEG":
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, int(self.quality)]
        elif self.image_format == "PNG":
            # Map 0-100 UI quality to 0-9 libpng compression level (inverted: 100 quality -> level 0, 0 quality -> level 9)
            png_level = max(0, min(9, int((100 - self.quality) * 9 / 100)))
            encode_params = [cv2.IMWRITE_PNG_COMPRESSION, png_level]

        manifest_file = None
        manifest_writer = None
        if self.export_manifest:
            csv_path = self.unique_path_cb(self.save_dir, "extraction_manifest.csv", used_names)
            try:
                manifest_file = open(csv_path, 'w', newline='', encoding='utf-8')
                self._manifest_file = manifest_file
                manifest_writer = csv.DictWriter(
                    manifest_file,
                    fieldnames=["frame_number", "timestamp_ms", "formatted_time", "filename"]
                )
                manifest_writer.writeheader()
            except Exception as e:
                raise OSError(f'Could not open manifest: {e}') from e

        from collections import deque
        pending = deque()
        errors = []
        last_update_time = time.monotonic()

        def write(path, image):
            try:
                if not cv2.imwrite(path, image, encode_params):
                    raise OSError(f'Could not save: {path}')
                return path
            except Exception:
                if os.path.exists(path):
                    remove_file(path)
                raise

        def collect():
            nonlocal extracted
            future, number, timestamp = pending.popleft()
            try:
                path = future.result()
                self.output_paths.append(path)
                extracted += 1
                if manifest_writer:
                    manifest_writer.writerow(dict(frame_number=number, timestamp_ms=timestamp,
                        formatted_time=format_timestamp(timestamp), filename=os.path.basename(path)))
            except Exception as exc:
                errors.append(str(exc))

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as executor:
                while current_frame < self.end_frame and not self.cancel_requested and not errors:
                    target_frame = round(current_frame)
                    delta = target_frame - current_pos
                    if 0 <= delta <= 60:
                        while current_pos < target_frame:
                            if not cap.grab():
                                break
                            current_pos += 1
                    else:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    success, image = cap.read()
                    current_pos = target_frame + 1
                    if not success:
                        errors.append(f'Could not decode frame {target_frame}')
                        break
                    timestamp_ms = round(target_frame / fps * 1000) if fps else 0
                    current_frame += self.step
                    if self.filter_blur:
                        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                        h, w = gray.shape[:2]
                        scale = min(1, 720 / max(h, w))
                        if scale < 1:
                            gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))))
                        if cv2.Laplacian(gray, cv2.CV_32F).var() < self.blur_threshold:
                            skipped += 1
                            continue
                    texts = [text for start, end, text in self.subtitles if start <= timestamp_ms <= end]
                    if texts:
                        image = draw_subtitle_on_frame(image, '\n'.join(texts))
                    filename = self.render_filename_cb(target_frame, timestamp_ms, self.extension)
                    path = self.unique_path_cb(self.save_dir, filename, used_names)
                    pending.append((executor.submit(write, path, image), target_frame, timestamp_ms))
                    if len(pending) >= 4:
                        collect()
                    now = time.monotonic()
                    if now - last_update_time >= 0.05:
                        self.progress.emit(min(99, int((current_frame - self.start_frame) / max(1, self.end_frame - self.start_frame) * 100)),
                                           extracted, f'Frame {target_frame:,} | {format_timestamp(timestamp_ms)}')
                        last_update_time = now
                while pending:
                    collect()
        finally:
            while pending:
                collect()
            cap.release()
            if manifest_file:
                manifest_file.close()
        if errors:
            self.failure = errors[0]
            self.error.emit(errors[0])

        self.finished.emit(extracted, time.time() - start_clock, self.cancel_requested)
