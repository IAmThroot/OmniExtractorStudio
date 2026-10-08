"""
OmniExtract Studio
All-in-one desktop video processing and dataset creation studio.
"""

__version__ = "1.2.0"
__author__ = "Throot"

# On Linux, NVIDIA cuDNN 9+ separates functions across modular shared objects
# (libcudnn_ops, libcudnn_cnn, libcudnn_adv). When ONNX Runtime's CUDA provider loads,
# dlopen fails unless these modular dependencies are loaded with RTLD_GLOBAL first.
import sys
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
