import os
import pytest
from omniextract.media.subtitles import parse_subtitles, write_shifted_srt

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,500
Hello World!

2
00:00:05,000 --> 00:00:08,200
<i>Welcome to OmniExtract Studio.</i>
"""

def test_parse_subtitles(tmp_path):
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(SAMPLE_SRT, encoding="utf-8")

    entries = parse_subtitles(str(srt_file))
    assert len(entries) == 2
    assert entries[0] == (1000, 4500, "Hello World!")
    assert entries[1] == (5000, 8200, "Welcome to OmniExtract Studio.")

def test_write_shifted_srt():
    entries = [
        (2000, 5000, "Segment One"),
        (6000, 9000, "Segment Two")
    ]
    # Extract sub clip from 3000ms to 8000ms
    shifted_path = write_shifted_srt(entries, 3000, 8000)
    assert os.path.isfile(shifted_path)

    with open(shifted_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Shifted relative times:
    # 2000 -> 5000 (relative to 3000 is 0 -> 2000)
    # 6000 -> 9000 (relative to 3000 is 3000 -> 5000)
    assert "00:00:00,000 --> 00:00:02,000" in content
    assert "Segment One" in content
    assert "00:00:03,000 --> 00:00:05,000" in content
    assert "Segment Two" in content

    os.remove(shifted_path)
