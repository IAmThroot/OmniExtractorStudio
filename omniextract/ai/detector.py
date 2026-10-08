"""
YOLOv8 presence detector extracted from motion_worker.py.
"""

from typing import Optional, List, Any
import cv2

from . import providers


class YOLOPresenceDetector:
    def __init__(self, model_path: str, provider: str = 'auto', target_classes: Optional[List[int]] = None):
        import numpy as np
        self.np = np
        self.session = providers.create_session(model_path, provider)
        
        # Default target classes: COCO classes for people, vehicles, and animals
        if target_classes is None:
            self.target_classes = [0, 1, 2, 3, 5, 6, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
        else:
            self.target_classes = target_classes

    def detect_presence(self, frame: Any, confidence: float = 0.40) -> bool:
        """
        Run inference on the frame to detect target presence.
        frame: BGR OpenCV frame.
        """
        np = self.np
        # Letterbox resize to 640x640
        h, w = frame.shape[:2]
        r = min(640 / h, 640 / w)
        new_w = int(w * r)
        new_h = int(h * r)
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        
        # Pad
        pad_w = 640 - new_w
        pad_h = 640 - new_h
        pad_left = pad_w // 2
        pad_right = pad_w - pad_left
        pad_top = pad_h // 2
        pad_bottom = pad_h - pad_top
        
        padded = cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        
        # Fast C++ preprocessing: BGR -> RGB, HWC -> CHW, 1/255.0 normalization, batch dim
        blob = cv2.dnn.blobFromImage(padded, 1.0 / 255.0, (640, 640), swapRB=True)
        
        # Inference
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: blob})
        output = outputs[0]
        
        # Shape: (1, 84, 8400)
        # Class scores start from index 4
        class_scores = output[0, 4:, :]
        
        # Filter for target classes
        target_scores = class_scores[self.target_classes, :]
        
        # Max confidence
        max_conf = float(np.max(target_scores))
        
        return max_conf >= confidence

    def close(self):
        """Cleanup resources."""
        self.session = None
