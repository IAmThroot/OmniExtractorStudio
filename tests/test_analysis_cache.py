import os
import json
import tempfile
import pytest
from unittest.mock import patch
from omniextract.media.analysis_cache import compute_video_hash, AnalysisCache

def test_compute_video_hash_nonexistent():
    assert compute_video_hash("nonexistent_video.mp4") == ""

def test_compute_video_hash_existing():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"0" * 1024)
        path = f.name
    
    try:
        h = compute_video_hash(path)
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex digest length
    finally:
        os.remove(path)

@patch('omniextract.media.analysis_cache.get_cache_dir')
def test_analysis_cache_save_load_scenes(mock_get_cache_dir):
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_get_cache_dir.return_value = temp_dir
        
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"dummy video data")
            path = f.name
            
        try:
            cache = AnalysisCache(path)
            assert cache.cache_dir.startswith(temp_dir)
            
            scenes = [{"start": 0, "end": 100}]
            cache.save_scenes(scenes)
            
            loaded = cache.load_scenes()
            assert loaded == scenes
        finally:
            os.remove(path)
