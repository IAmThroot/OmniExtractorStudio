"""
AI and Inference related modules for OmniExtract Studio.
"""

from .detector import YOLOPresenceDetector
from .rife import (
    RIFEInterpolator,
    find_rife_model,
    is_rife_available,
    get_model_status_info,
    FLOWNET_PKL_URL,
    FILM_NET_SAFETENSORS_URL,
    RIFE_PRACTICAL_URL,
    COMFY_FRAME_INTERP_URL,
)
from .providers import get_available_providers, get_preferred_provider

__all__ = [
    "YOLOPresenceDetector",
    "RIFEInterpolator",
    "find_rife_model",
    "is_rife_available",
    "get_model_status_info",
    "FLOWNET_PKL_URL",
    "FILM_NET_SAFETENSORS_URL",
    "RIFE_PRACTICAL_URL",
    "COMFY_FRAME_INTERP_URL",
    "get_available_providers",
    "get_preferred_provider",
]
