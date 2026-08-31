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
