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

def test_compute_video_hash_multiblock_differentiation():
    # Two files with identical head and tail but different middle blocks
    size = 1024 * 1024  # 1 MB
    data1 = bytearray(b"A" * size)
    data2 = bytearray(b"A" * size)
    # Modify data2 in the 50% region (middle)
    data2[size // 2: (size // 2) + 1024] = b"B" * 1024

    with tempfile.NamedTemporaryFile(delete=False) as f1, tempfile.NamedTemporaryFile(delete=False) as f2:
        f1.write(data1)
        f2.write(data2)
        p1, p2 = f1.name, f2.name

    try:
        h1 = compute_video_hash(p1)
        h2 = compute_video_hash(p2)
        assert h1 != h2
    finally:
        os.remove(p1)
        os.remove(p2)


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
