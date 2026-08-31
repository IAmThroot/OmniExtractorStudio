import os
import pytest
from omniextract.utils.filenames import render_filename, unique_path

def test_render_filename_default():
    result = render_filename("", 42, 1000, ".png")
    assert result == "frame_00-00-01.000.png"

def test_render_filename_tokens():
    template = "img_{frame}_{hour}h_{minute}m_{second}s"
    result = render_filename(template, 105, 3661000, ".jpg")
    assert result == "img_105_01h_01m_01s.jpg"

def test_render_filename_sanitization():
    template = 'scene:test/bad*chars<{frame}>'
    result = render_filename(template, 5, 0, ".png")
    assert ":" not in result
    assert "/" not in result
    assert "<" not in result
    assert ">" not in result
    assert "*" not in result
    assert result.endswith(".png")

def test_unique_path(tmp_path):
    used = set()
    d = str(tmp_path)
    
    p1 = unique_path(d, "frame.png", used)
    assert os.path.basename(p1) == "frame.png"
    
    p2 = unique_path(d, "frame.png", used)
    assert os.path.basename(p2) == "frame_001.png"
    
    p3 = unique_path(d, "frame.png", used)
    assert os.path.basename(p3) == "frame_002.png"
