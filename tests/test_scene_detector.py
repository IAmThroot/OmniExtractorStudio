import pytest
from omniextract.media.scene_detector import SceneCut, detect_scenes_ffmpeg, detect_scenes_histogram, detect_scenes

def test_scenecut_instantiation():
    cut = SceneCut(timestamp_ms=1000, confidence=0.85)
    assert cut.timestamp_ms == 1000
    assert cut.confidence == 0.85

def test_detect_scenes_ffmpeg_nonexistent():
    cuts = detect_scenes_ffmpeg("non_existent_video.mp4")
    assert isinstance(cuts, list)
    assert len(cuts) == 0

def test_detect_scenes_histogram_nonexistent():
    cuts = detect_scenes_histogram("non_existent_video.mp4")
    assert isinstance(cuts, list)
    assert len(cuts) == 0

def test_detect_scenes_auto():
    cuts = detect_scenes("non_existent_video.mp4", method='auto')
    assert isinstance(cuts, list)
    assert len(cuts) == 0
