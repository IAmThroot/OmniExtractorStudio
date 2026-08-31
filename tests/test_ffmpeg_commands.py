import pytest
from unittest.mock import patch, MagicMock
from omniextract.media.ffmpeg import (
    get_ffmpeg_path,
    get_ffprobe_path,
    check_ffmpeg_available,
    get_ffmpeg_version
)

def test_get_ffmpeg_path():
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/bin/ffmpeg"
        assert get_ffmpeg_path() == "/usr/bin/ffmpeg"
        mock_which.assert_called_once_with("ffmpeg")

def test_get_ffprobe_path():
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/bin/ffprobe"
        assert get_ffprobe_path() == "/usr/bin/ffprobe"
        mock_which.assert_called_once_with("ffprobe")

def test_check_ffmpeg_available_both_exist():
    with patch("omniextract.media.ffmpeg.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("omniextract.media.ffmpeg.get_ffprobe_path", return_value="/usr/bin/ffprobe"):
        assert check_ffmpeg_available() is True

def test_check_ffmpeg_available_missing_one():
    with patch("omniextract.media.ffmpeg.get_ffmpeg_path", return_value=None), \
         patch("omniextract.media.ffmpeg.get_ffprobe_path", return_value="/usr/bin/ffprobe"):
        assert check_ffmpeg_available() is False

def test_get_ffmpeg_version_not_found():
    with patch("omniextract.media.ffmpeg.get_ffmpeg_path", return_value=None):
        assert get_ffmpeg_version() == "Not found"

def test_get_ffmpeg_version_success():
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = "ffmpeg version 4.2.2 Copyright (c) 2000-2019 the FFmpeg developers\nbuilt with gcc 9.2.1"
    
    with patch("omniextract.media.ffmpeg.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", return_value=mock_run):
        version = get_ffmpeg_version()
        assert version == "ffmpeg version 4.2.2 Copyright (c) 2000-2019 the FFmpeg developers"

def test_get_ffmpeg_version_failure():
    mock_run = MagicMock()
    mock_run.returncode = 1
    
    with patch("omniextract.media.ffmpeg.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", return_value=mock_run):
        assert get_ffmpeg_version() == "Unknown"
