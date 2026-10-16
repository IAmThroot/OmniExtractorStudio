import concurrent.futures
import csv
import math
import os
import threading
import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from ..media.subtitles import draw_subtitle_on_frame
from ..utils.timestamps import format_timestamp


class FrameExtractionWorker(QThread):
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

    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("The selected source file could not be opened.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        current_frame = self.start_frame
        expected = max(1, math.ceil((self.end_frame - self.start_frame) / self.step))
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
            csv_path = os.path.join(self.save_dir, "extraction_manifest.csv")
            try:
                manifest_file = open(csv_path, 'w', newline='', encoding='utf-8')
                manifest_writer = csv.DictWriter(
                    manifest_file,
                    fieldnames=["frame_number", "timestamp_ms", "formatted_time", "filename"]
                )
                manifest_writer.writeheader()
            except Exception as e:
                self.error.emit(f"Could not open manifest: {e}")

        workers = (os.cpu_count() or 4)
        # Bound in-flight (uncompressed) frames independently of core count so
        # 4K/8K extraction does not balloon to hundreds of MB of buffered images.
        max_in_flight = max(2, min(4, workers))
        sem = threading.Semaphore(max_in_flight)
        write_error = []

        def _write_task(path, img):
            try:
                if not cv2.imwrite(path, img, encode_params):
                    write_error.append(f"Could not save: {path}")
            except Exception as exc:
                write_error.append(str(exc))
            finally:
                sem.release()

        last_update_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            while current_frame < self.end_frame and not self.cancel_requested:
                if write_error:
                    self.error.emit(write_error[0])
                    self.cancel_requested = True
                    break

                target_frame = round(current_frame)

                delta = target_frame - current_pos
                if 0 <= delta <= 60:
                    while current_pos < target_frame:
                        if not cap.grab():
                            break
                        current_pos += 1
                    success, image = cap.read()
                    current_pos += 1
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    success, image = cap.read()
                    current_pos = target_frame + 1

                if not success:
                    break

                actual_frame = max(0, target_frame)

                if self.filter_blur:
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    h, w = gray.shape[:2]
                    max_dim = max(h, w)
                    if max_dim > 720:
                        scale = 720.0 / max_dim
                        small_gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                    else:
                        small_gray = gray
                    variance = cv2.Laplacian(small_gray, cv2.CV_32F).var()
                    if variance < self.blur_threshold:
                        skipped += 1
                        current_frame += self.step
                        continue

                timestamp_ms = round((actual_frame / fps) * 1000) if fps else 0

                if self.subtitles:
                    active_texts = []
                    for s, e, text in self.subtitles:
                        if s > timestamp_ms:
                            break
                        if e >= timestamp_ms:
                            active_texts.append(text)
                    if active_texts:
                        image = draw_subtitle_on_frame(image, "\n".join(active_texts))

                filename = self.render_filename_cb(actual_frame, timestamp_ms, self.extension)
                path = self.unique_path_cb(self.save_dir, filename, used_names)

                if manifest_writer:
                    try:
                        manifest_writer.writerow({
                            "frame_number": actual_frame,
                            "timestamp_ms": timestamp_ms,
                            "formatted_time": format_timestamp(timestamp_ms),
                            "filename": os.path.basename(path)
                        })
                    except Exception:
                        pass

                sem.acquire()
                executor.submit(_write_task, path, image)

                extracted += 1
                current_frame += self.step
                
                now = time.time()
                if now - last_update_time > 0.05 or current_frame >= self.end_frame:
                    percent = int(min(100, ((extracted + skipped) / expected) * 100))
                    elapsed = now - start_clock
                    rate = extracted / elapsed if elapsed > 0 else 0

                    lbl_blur = f" (Skipped {skipped})" if skipped > 0 else ""
                    label = f"Frame {actual_frame:,}  |  {format_timestamp(timestamp_ms)}  |  {rate:.1f} fps{lbl_blur}"
                    self.progress.emit(percent, extracted, label)
                    last_update_time = now

            for _ in range(max_in_flight):
                sem.acquire()

        cap.release()

        if manifest_file:
            try:
                manifest_file.flush()
                manifest_file.close()
            except Exception:
                pass

        self.finished.emit(extracted, time.time() - start_clock, self.cancel_requested)
