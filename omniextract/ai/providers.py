"""
ONNX Runtime Execution Provider discovery and session management.
"""

from dataclasses import dataclass
from typing import Optional, List, Any


@dataclass
class ProviderStatus:
    available: List[str]
    selected: str
    display_name: str


def get_available_providers() -> List[str]:
    """Return a list of available execution providers."""
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except ImportError:
        return []


def get_preferred_provider(preference: str = 'auto') -> List[str]:
    """Return the ordered provider list for InferenceSession based on preference."""
    available = get_available_providers()
    if not available:
        return []

    preference = preference.lower()
    
    if preference == 'cpu':
        return ['CPUExecutionProvider'] if 'CPUExecutionProvider' in available else []
        
    elif preference == 'directml':
        return ['DmlExecutionProvider'] if 'DmlExecutionProvider' in available else ['CPUExecutionProvider']
        
    elif preference == 'cuda':
        return ['CUDAExecutionProvider'] if 'CUDAExecutionProvider' in available else ['CPUExecutionProvider']
        
    # 'auto' or default
    providers = []
    if 'CUDAExecutionProvider' in available:
        providers.append('CUDAExecutionProvider')
    if 'DmlExecutionProvider' in available:
        providers.append('DmlExecutionProvider')
    if 'CPUExecutionProvider' in available:
        providers.append('CPUExecutionProvider')
        
    return providers


def create_session(model_path: str, provider: str = 'auto') -> Any:
    """Create an ONNX Runtime InferenceSession using the resolved provider list."""
    try:
        import onnxruntime as ort
        providers = get_preferred_provider(provider)
        if not providers:
            # Fallback to whatever ORT defaults to
            return ort.InferenceSession(model_path)
        return ort.InferenceSession(model_path, providers=providers)
    except ImportError:
        raise ImportError("onnxruntime is not installed.")


def get_provider_status(preference: str = 'auto') -> ProviderStatus:
    """Return the current status for UI display."""
    available = get_available_providers()
    preferred = get_preferred_provider(preference)
    
    selected = preferred[0] if preferred else "None"
    
    display_name = selected
    if display_name == 'CUDAExecutionProvider':
        display_name = 'CUDA (NVIDIA GPU)'
    elif display_name == 'DmlExecutionProvider':
        display_name = 'DirectML (GPU)'
    elif display_name == 'CPUExecutionProvider':
        display_name = 'CPU'
        
    return ProviderStatus(
        available=available,
        selected=selected,
        display_name=display_name
    )
