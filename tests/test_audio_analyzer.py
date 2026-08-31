import pytest
from unittest.mock import patch
from omniextract.media.audio_analyzer import WaveformData, extract_waveform

def test_waveform_data_instantiation():
    data = WaveformData(
        sample_rate=8000,
        duration_ms=5000,
        peaks=[(-0.1, 0.1), (-0.2, 0.2)],
        bucket_count=2,
        level=0
    )
    assert data.sample_rate == 8000
    assert data.duration_ms == 5000
    assert data.bucket_count == 2
    assert data.level == 0
    assert len(data.peaks) == 2

def test_bucket_count_constants():
    with patch('subprocess.Popen') as mock_popen, patch('shutil.which', return_value='ffmpeg'):
        mock_process = mock_popen.return_value
        mock_process.returncode = 0
        
        import struct
        dummy_data = struct.pack("<100h", *([0]*100))
        mock_process.communicate.return_value = (dummy_data, b'')
        
        res0 = extract_waveform("dummy.mp4", level=0)
        assert res0 is not None
        assert res0.bucket_count == 500
        
        res1 = extract_waveform("dummy.mp4", level=1)
        assert res1 is not None
        assert res1.bucket_count == 2000
        
        res2 = extract_waveform("dummy.mp4", level=2)
        assert res2 is not None
        assert res2.bucket_count == 8000

def test_extract_waveform_nonexistent_file():
    result = extract_waveform("non_existent_file.mp4")
    assert result is None
