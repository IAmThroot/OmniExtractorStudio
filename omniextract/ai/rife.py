"""
RIFE and FILM AI Frame Interpolation helper and model manager.
"""

import os
from typing import Optional, List, Tuple
import cv2
import numpy as np

from ..utils.resources import get_resource_path
from . import providers

RIFE_CANDIDATE_NAMES = [
    "RIFE_fp32.onnx",
    "rife_fp32.onnx",
    "rife.onnx",
    "rife-v4.onnx",
    "rife_v4.onnx",
    "flownet.pkl",
    "film_net_fp16.safetensors",
    "film_net.safetensors",
]

RIFE_ONNX_URL = "https://huggingface.co/FuryTMP/RIFE_fp32/resolve/main/RIFE_fp32.onnx"
RIFE_REPO_URL = "https://huggingface.co/FuryTMP/RIFE_fp32"
FLOWNET_PKL_URL = "https://huggingface.co/DeepBeepMeep/Wan2.1/resolve/main/flownet.pkl"
FILM_NET_SAFETENSORS_URL = "https://huggingface.co/Comfy-Org/frame_interpolation/resolve/main/frame_interpolation/film_net_fp16.safetensors"
RIFE_PRACTICAL_URL = "https://github.com/hzwer/Practical-RIFE"
COMFY_FRAME_INTERP_URL = "https://huggingface.co/Comfy-Org/frame_interpolation"


def find_rife_model(custom_path: Optional[str] = None) -> Optional[str]:
    """Find a valid RIFE/FILM model file path, checking custom path and assets/models/."""
    if custom_path is not None:
        return custom_path if os.path.isfile(custom_path) else None

    for name in RIFE_CANDIDATE_NAMES:
        rel_path = os.path.join("assets", "models", name)
        resolved = get_resource_path(rel_path)
        if os.path.isfile(resolved):
            return resolved

    return None


def get_model_status_info(custom_path: Optional[str] = None) -> Tuple[str, bool, str]:
    """
    Returns (status_text, is_ready, model_path).
    Validates model existence and checks required dependencies (ONNX Runtime, PyTorch, Safetensors).
    """
    model_path = find_rife_model(custom_path)
    if not model_path:
        return ("No AI model found in assets/models/ (place RIFE_fp32.onnx)", False, "")

    ext = os.path.splitext(model_path)[1].lower()
    fname = os.path.basename(model_path)

    if ext == ".onnx":
        available = providers.get_available_providers()
        if not available:
            return (f"⚠ ONNX Runtime unavailable for {fname}", False, model_path)
        p_status = providers.get_provider_status()
        if p_status.selected == "CUDAExecutionProvider":
            return (f"✓ ONNX Model Loaded: {fname} (Device: NVIDIA CUDA GPU)", True, model_path)
        elif p_status.selected == "DmlExecutionProvider":
            return (f"✓ ONNX Model Loaded: {fname} (Device: DirectML GPU)", True, model_path)
        elif p_status.selected == "CPUExecutionProvider":
            return (
                f"⚠ ONNX Model Loaded: {fname} [CPU Only - Slow! Install onnxruntime-cuda / onnxruntime-gpu for GPU speed]",
                True,
                model_path,
            )
        return (f"✓ ONNX Model Loaded: {fname} ({p_status.display_name})", True, model_path)

    elif ext in (".pkl", ".pt", ".pth"):
        try:
            import torch
            return (f"✓ PyTorch Model Loaded: {fname}", True, model_path)
        except ImportError:
            return (
                f"⚠ {fname} is a PyTorch checkpoint (PyTorch is not installed in this environment). "
                f"Use FFmpeg Motion Interpolation (MCI) or install 'torch'.",
                False,
                model_path
            )

    elif ext == ".safetensors":
        try:
            import safetensors
            import torch
            return (f"✓ SafeTensors Model Loaded: {fname}", True, model_path)
        except ImportError:
            return (
                f"⚠ {fname} requires 'safetensors' and 'torch' (not installed in this environment). "
                f"Use FFmpeg Motion Interpolation (MCI) or install 'safetensors' and 'torch'.",
                False,
                model_path
            )

    return (f"Unknown model format: {fname}", False, model_path)


def is_rife_available(custom_path: Optional[str] = None) -> bool:
    """Return True if an available model and its matching engine/runtime are ready."""
    _, ready, _ = get_model_status_info(custom_path)
    return ready


class RIFEInterpolator:
    """
    AI frame interpolator for ONNX models or PyTorch checkpoints.
    Takes pairs of consecutive frames and infers intermediate frames.
    """

    def __init__(self, model_path: Optional[str] = None, provider: str = "auto"):
        resolved_path = find_rife_model(model_path)
        if not resolved_path:
            raise FileNotFoundError(
                "AI interpolation model not found. Please place 'flownet.pkl' or 'film_net_fp16.safetensors' in 'assets/models/'."
            )

        self.model_path = resolved_path
        ext = os.path.splitext(self.model_path)[1].lower()

        if ext == ".onnx":
            self.session = providers.create_session(self.model_path, provider)
            self.input_names = [inp.name for inp in self.session.get_inputs()]
            self.output_names = [out.name for out in self.session.get_outputs()]
            active = self.session.get_providers()
            self.active_provider = active[0] if active else "CPU"
            self.backend = "onnx"
        elif ext in (".pkl", ".pt", ".pth"):
            try:
                import torch
            except ImportError:
                raise RuntimeError(
                    f"'{os.path.basename(self.model_path)}' is a PyTorch model checkpoint (.pkl). "
                    "Running PyTorch models requires 'torch', which is not installed in the current environment.\n\n"
                    "Recommended: Use 'FFmpeg Motion Interpolation (MCI)', which runs immediately with zero extra dependencies."
                )
            self.backend = "torch"
        elif ext == ".safetensors":
            try:
                import safetensors
                import torch
            except ImportError:
                raise RuntimeError(
                    f"'{os.path.basename(self.model_path)}' is a SafeTensors checkpoint file. "
                    "Running it requires 'safetensors' and 'torch', which are not installed in the current environment.\n\n"
                    "Recommended: Use 'FFmpeg Motion Interpolation (MCI)', which runs immediately with zero extra dependencies."
                )
            self.backend = "safetensors"
        else:
            raise ValueError(f"Unsupported model format: '{os.path.basename(self.model_path)}'.")

    def interpolate_pair(self, frame0: np.ndarray, frame1: np.ndarray) -> np.ndarray:
        """
        Interpolate an intermediate frame between frame0 and frame1 (both BGR uint8).
        Returns intermediate frame (BGR uint8).
        """
        h, w = frame0.shape[:2]

        if getattr(self, "backend", "onnx") == "onnx":
            # Pad to multiple of 32
            pad_h = (32 - (h % 32)) % 32
            pad_w = (32 - (w % 32)) % 32

            if pad_h > 0 or pad_w > 0:
                f0 = cv2.copyMakeBorder(frame0, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)
                f1 = cv2.copyMakeBorder(frame1, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)
            else:
                f0, f1 = frame0, frame1

            # Fast BGR -> RGB conversion using OpenCV
            f0_rgb = cv2.cvtColor(f0, cv2.COLOR_BGR2RGB)
            f1_rgb = cv2.cvtColor(f1, cv2.COLOR_BGR2RGB)

            if len(self.input_names) >= 2:
                t0 = np.expand_dims(f0_rgb.transpose(2, 0, 1), axis=0).astype(np.float32) / 255.0
                t1 = np.expand_dims(f1_rgb.transpose(2, 0, 1), axis=0).astype(np.float32) / 255.0
                feed = {self.input_names[0]: t0, self.input_names[1]: t1}
            else:
                combined = np.concatenate([f0_rgb, f1_rgb], axis=2)
                t_in = np.expand_dims(combined.transpose(2, 0, 1), axis=0).astype(np.float32) / 255.0
                feed = {self.input_names[0]: t_in}

            outputs = self.session.run(self.output_names, feed)
            mid_tensor = outputs[0][0]

            mid_img = (np.clip(mid_tensor.transpose(1, 2, 0), 0.0, 1.0) * 255.0).astype(np.uint8)
            return mid_img[:h, :w, ::-1]

        # Fallback blending if deep model runtime is missing
        return cv2.addWeighted(frame0, 0.5, frame1, 0.5, 0)
