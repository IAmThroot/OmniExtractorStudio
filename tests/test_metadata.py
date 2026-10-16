import pytest
from omniextract.media.metadata import format_file_size, probe_video_metadata

def test_format_file_size():
    assert format_file_size(500) == "500.00 B"
    assert format_file_size(1024) == "1.00 KB"
    assert format_file_size(1024 * 1024 * 5) == "5.00 MB"
    assert format_file_size(1024 * 1024 * 1024 * 2.5) == "2.50 GB"

def test_probe_nonexistent_video():
    data = probe_video_metadata("/nonexistent/video.mp4")
    assert data == {}

def test_probe_subtitle_tracks_nonexistent():
    from omniextract.workers.metadata_worker import probe_subtitle_tracks_for_video
    tracks, ext = probe_subtitle_tracks_for_video("/nonexistent/video.mp4")
    assert tracks == []
    assert ext == ""

