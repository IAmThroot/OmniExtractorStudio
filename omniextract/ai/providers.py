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


import sys

def _ensure_cuda_cudnn_loaded():
    """Ensure cuDNN 9+ modular libraries are loaded into global symbol table on Linux."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            import ctypes.util
            for _name in ("cudnn_ops", "cudnn_cnn", "cudnn_adv", "cudnn", "cudnn_graph"):
                for _candidate in (f"lib{_name}.so", f"lib{_name}.so.9", ctypes.util.find_library(_name)):
                    if _candidate:
                        try:
                            ctypes.CDLL(_candidate, mode=ctypes.RTLD_GLOBAL)
                            break
                        except Exception:
                            pass
        except Exception:
            pass

_ensure_cuda_cudnn_loaded()


def get_available_providers() -> List[str]:
    """Return a list of available execution providers."""
    _ensure_cuda_cudnn_loaded()
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
        
        try:
            ort.set_default_logger_severity(3)
        except Exception:
            pass

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.log_severity_level = 3
        
        providers_list = get_preferred_provider(provider)
        
        if not providers_list:
            return ort.InferenceSession(model_path, sess_options=sess_options)
            
        provider_options = []
        for p in providers_list:
            if p == 'CUDAExecutionProvider':
                provider_options.append({
                    'cudnn_conv_algo_search': 'DEFAULT',
                    'arena_extend_strategy': 'kNextPowerOfTwo'
                })
            else:
                provider_options.append({})
                
        return ort.InferenceSession(model_path, sess_options=sess_options, providers=providers_list, provider_options=provider_options)
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
