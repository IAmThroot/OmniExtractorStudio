import os
import pytest
from omniextract.ai.detector import YOLOPresenceDetector

def test_yolo_presence_detector():
    pytest.importorskip('onnxruntime')
    np = pytest.importorskip('numpy')
    
    model_path = 'dummy_model.onnx'
    if not os.path.exists(model_path):
        pytest.skip(f"Model file {model_path} missing")
        
    detector = YOLOPresenceDetector(model_path)
    
    # Test detect_presence returns a bool given dummy numpy array (640x640x3 zeros)
    dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
    
    try:
        res = detector.detect_presence(dummy_frame)
        assert isinstance(res, bool)
    except Exception as e:
        pytest.fail(f"detect_presence raised exception: {e}")
    finally:
        detector.close()
