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


VTT_SAMPLE = """WEBVTT

NOTE a comment that must be ignored

cue-1
00:00:03.00 --> 00:00:05.500 align:start position:10%
Hello <b>World</b>
second line

1:02:03,004 --> 1:02:05,000
{styled} Tail &amp; text
"""


def test_parse_subtitles_vtt_robust(tmp_path):
    vtt_file = tmp_path / "test.vtt"
    vtt_file.write_text(VTT_SAMPLE, encoding="utf-8")

    entries = parse_subtitles(str(vtt_file))
    # Two cues: tolerant of 2-digit ms, single-digit hour, cue settings and entities.
    assert entries[0] == (3000, 5500, "Hello World\nsecond line")
    assert entries[1] == (3723004, 3725000, "Tail & text")


def test_parse_subtitles_missing_file():
    assert parse_subtitles("/nonexistent/captions.srt") == []
