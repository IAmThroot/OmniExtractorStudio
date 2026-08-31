import pytest
from omniextract.utils.timestamps import (
    format_timestamp, qtime_to_ms, ms_to_qtime, duration_text, frame_to_ms
)
from PyQt6.QtCore import QTime

def test_format_timestamp_zero():
    assert format_timestamp(0) == "00:00:00.000"

def test_format_timestamp_hyphen():
    assert format_timestamp(3661500, separator="-") == "01-01-01.500"

def test_format_timestamp_negative():
    assert format_timestamp(-500) == "00:00:00.000"

def test_format_timestamp_large():
    # 25 hours + 30 mins
    ms = (25 * 3600 + 30 * 60) * 1000
    assert format_timestamp(ms) == "25:30:00.000"

def test_qtime_conversions():
    qt = QTime(1, 23, 45, 678)
    ms = qtime_to_ms(qt)
    expected_ms = (1 * 3600 + 23 * 60 + 45) * 1000 + 678
    assert ms == expected_ms

    reconstructed_qt = ms_to_qtime(ms)
    assert reconstructed_qt.hour() == 1
    assert reconstructed_qt.minute() == 23
    assert reconstructed_qt.second() == 45
    assert reconstructed_qt.msec() == 678

def test_frame_to_ms():
    assert frame_to_ms(0, 30.0) == 0
    assert frame_to_ms(30, 30.0) == 1000
    assert frame_to_ms(60, 60.0) == 1000
    assert frame_to_ms(15, 0) == 0
