import shutil
import subprocess

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from ..utils.resources import get_resource_path
from ..utils.timestamps import format_timestamp


class MotionExtractionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, int, str)

    def __init__(self, source_file, save_dir, extension, mode, sensitivity, min_area, cooldown_ms, path_cb, format_cb, engine="MOG2", yolo_conf=0.40):
        super().__init__()
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.mode = mode
        self.sensitivity = sensitivity
        self.min_area = min_area
        self.cooldown_ms = cooldown_ms
        self.path_cb = path_cb
        self.format_cb = format_cb
        self.engine = engine
        self.yolo_conf = yolo_conf
        self.cancel_requested = False

    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.finished.emit(False, 0, "Could not open source file.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        is_yolo = "YOLO" in self.engine
        ort_session = None
        input_name = None
        target_classes = [0, 1, 2, 3, 5, 6, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
        
        fgbg = None
        if is_yolo:
            import onnxruntime
            import numpy as np
            model_path = get_resource_path("assets/models/yolov8n.onnx")
            ort_session = onnxruntime.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            input_name = ort_session.get_inputs()[0].name
        else:
            var_threshold = max(5, 200 - int(self.sensitivity * 1.95))
            fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=var_threshold, detectShadows=False)

        motion_intervals = []
        current_interval_start = None
        last_motion_time = None

        extracted_count = 0
        used_names = set()
        current_frame = 0

        # Read first frame to determine area scaling factor
        success, frame = cap.read()
        if not success:
            self.finished.emit(False, 0, "Could not read video stream.")
            return

        H, W = frame.shape[:2]
        area_scale = (320 * 180) / (W * H)
        scaled_min_area = self.min_area * area_scale

        while not self.cancel_requested:
            current_ms = round((current_frame / fps) * 1000)
            
            motion_detected = False
            
            if is_yolo:
                # --- YOLOv8 ONNX Logic ---
                # 1. Letterbox resize to 640x640 (standard YOLOv8 input)
                shape = frame.shape[:2]
                r = min(640 / shape[0], 640 / shape[1])
                new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
                dw, dh = (640 - new_unpad[0]) / 2, (640 - new_unpad[1]) / 2
                
                if shape[::-1] != new_unpad:
                    im = cv2.resize(frame, new_unpad, interpolation=cv2.INTER_LINEAR)
                else:
                    im = frame.copy()
                    
                top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
                left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
                im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
                
                # 2. Preprocess: BGR -> RGB, HWC -> CHW, 0-255 -> 0.0-1.0
                im = im[:, :, ::-1].transpose(2, 0, 1)
                im = np.ascontiguousarray(im).astype(np.float32) / 255.0
                im = im[None]  # Add batch dimension
                
                # 3. ONNX Inference
                results = ort_session.run(None, {input_name: im})[0]
                
                # 4. Ultra-Fast Presence Detection (Bypassing Bounding Box Decoding & NMS)
                # The YOLOv8 raw ONNX output shape is (1, 84, 8400) where:
                # - rows 0-3 are bounding box coordinates
                # - rows 4-83 are the 80 COCO class confidence scores
                # For motion/presence gating, we do not require full object localization or NMS.
                # We slice the scores for our target classes (people, vehicles, animals) and check
                # if the peak presence confidence among all anchors exceeds the trigger threshold.
                scores = results[0, 4:84, :]
                target_scores = scores[target_classes, :]
                
                if np.max(target_scores) > self.yolo_conf:
                    motion_detected = True
            else:
                # MOG2 Logic
                small = cv2.resize(frame, (320, 180))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                fgmask = fgbg.apply(gray)
                fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)[1]
                fgmask = cv2.erode(fgmask, None, iterations=1)
                fgmask = cv2.dilate(fgmask, None, iterations=2)

                contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                if current_frame > 5:
                    for contour in contours:
                        if cv2.contourArea(contour) > scaled_min_area:
                            motion_detected = True
                            break

            if motion_detected:
                if current_interval_start is None:
                    current_interval_start = current_ms

                    if self.mode == "Keyframes":
                        filename = self.format_cb(current_frame, current_ms, self.extension)
                        path = self.path_cb(self.save_dir, "motion_" + filename, used_names)
                        cv2.imwrite(path, frame)
                        extracted_count += 1

                last_motion_time = current_ms
            else:
                if current_interval_start is not None and last_motion_time is not None:
                    if (current_ms - last_motion_time) > self.cooldown_ms:
                        if self.mode == "Clips":
                            motion_intervals.append((current_interval_start, current_ms))
                            extracted_count += 1
                        current_interval_start = None
                        last_motion_time = None

            current_frame += 1
            if current_frame % 30 == 0:
                pct = int(min(100, (current_frame / max(1, frame_count)) * 100))
                self.progress.emit(pct, f"Scanning: {format_timestamp(current_ms)} / {format_timestamp(int((frame_count/fps)*1000))}")

            success, frame = cap.read()
            if not success:
                break

        if current_interval_start is not None and self.mode == "Clips":
            motion_intervals.append((current_interval_start, round((frame_count / fps) * 1000)))
            extracted_count += 1

        cap.release()

        if self.cancel_requested:
            self.finished.emit(False, extracted_count, "Cancelled")
            return

        if self.mode == "Clips" and motion_intervals:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self.finished.emit(False, 0, "FFmpeg not found for clip extraction.")
                return

            self.progress.emit(100, "Extracting clips...")

            for i, (start_ms, end_ms) in enumerate(motion_intervals):
                if self.cancel_requested:
                    break

                start_sec = start_ms / 1000.0
                dur_sec = (end_ms - start_ms) / 1000.0

                ext = self.extension.lstrip(".")
                clip_name = f"motion_{i+1}_{format_timestamp(start_ms, '-')}.{ext}"
                out_path = self.path_cb(self.save_dir, clip_name, used_names)

                cmd = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{start_sec:.6f}", "-i", self.source_file,
                    "-t", f"{dur_sec:.6f}", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "copy",
                    "-avoid_negative_ts", "make_zero", out_path
                ]

                subprocess.run(cmd, capture_output=True)

        self.finished.emit(True, extracted_count, "Success")
