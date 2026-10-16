import pytest
from omniextract.media.hwaccel import (
    HardwareCapabilities,
    probe_decode_capabilities,
    probe_encode_capabilities,
    probe_capabilities,
    get_hwaccel_decode_args,
    get_hwaccel_encode_args
)

def test_probe_decode_capabilities():
    result = probe_decode_capabilities()
    assert isinstance(result, list)

def test_probe_encode_capabilities():
    result = probe_encode_capabilities()
    assert isinstance(result, list)

def test_probe_capabilities():
    result = probe_capabilities()
    assert isinstance(result, HardwareCapabilities)
    assert isinstance(result.decode, list)
    assert isinstance(result.encode, list)
    assert isinstance(result.ai, list)

def test_get_hwaccel_decode_args():
    # Test with empty capabilities (fallback)
    caps = HardwareCapabilities([], [], [])
    args = get_hwaccel_decode_args(caps)
    assert isinstance(args, list)
    assert args == []

    # Test with a known capability
    caps = HardwareCapabilities(['cuda'], [], [])
    args = get_hwaccel_decode_args(caps)
    assert args == ['-hwaccel', 'cuda']

def test_get_hwaccel_encode_args():
    # Test with empty capabilities (h264 fallback)
    caps = HardwareCapabilities([], [], [])
    args = get_hwaccel_encode_args(caps)
    assert isinstance(args, list)
    assert args == ['-c:v', 'libx264']
    
    # Test with empty capabilities (hevc fallback)
    args_hevc = get_hwaccel_encode_args(caps, 'hevc')
    assert args_hevc == ['-c:v', 'libx265']

    # Test with hardware encoder
    caps = HardwareCapabilities([], ['h264_nvenc'], [])
    args_nvenc = get_hwaccel_encode_args(caps)
    assert args_nvenc == ['-c:v', 'h264_nvenc']

def test_get_hwaccel_encode_profile():
    from omniextract.media.hwaccel import get_hwaccel_encode_profile
    profile = get_hwaccel_encode_profile(preset="fast", crf=18)
    assert isinstance(profile, list)
    assert "-c:v" in profile
    assert profile.codec in profile
    assert profile.get("video_args") == list(profile)


def test_capability_probe_is_cached():
    from omniextract.media import hwaccel
    hwaccel.invalidate_capability_cache()
    first = hwaccel.probe_capabilities()
    second = hwaccel.probe_capabilities()
    # Cached: identical object means ffmpeg is probed at most once per session.
    assert first is second
    hwaccel.invalidate_capability_cache()
    assert hwaccel.probe_capabilities() is not first
