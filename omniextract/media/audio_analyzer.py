"""
Hierarchical audio waveform extraction for OmniExtract Studio.
"""
import shutil
import subprocess
import struct
import math
from dataclasses import dataclass
from typing import Optional, Generator, Tuple, List


@dataclass
class WaveformData:
    sample_rate: int
    duration_ms: int
    peaks: List[Tuple[float, float]]
    bucket_count: int
    level: int


def extract_waveform(video_path: str, level: int = 0, duration_ms: int = 0) -> Optional[WaveformData]:
    """
    Extracts waveform data from a video file.
    level 0: 500 buckets
    level 1: 2000 buckets
    level 2: 8000 buckets
    """
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return None

    if level == 0:
        bucket_count = 500
    elif level == 1:
        bucket_count = 2000
    else:
        bucket_count = 8000

    sample_rate = 8000
    cmd = [
        ffmpeg_path,
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-loglevel", "error",
        "pipe:1"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = process.communicate(timeout=120)
    except Exception:
        if 'process' in locals():
            process.kill()
        return None

    if process.returncode != 0 or not stdout:
        return None

    # Each sample is 2 bytes (16-bit)
    num_samples = len(stdout) // 2
    if num_samples == 0:
        return None
    
    samples = struct.unpack(f"<{num_samples}h", stdout)

    if duration_ms == 0:
        duration_ms = int((num_samples / sample_rate) * 1000)

    samples_per_bucket = max(1, num_samples // bucket_count)
    actual_bucket_count = min(bucket_count, math.ceil(num_samples / samples_per_bucket))
    
    peaks = []
    max_val = 32768.0  # 16-bit signed integer max

    for i in range(actual_bucket_count):
        start_idx = i * samples_per_bucket
        end_idx = min((i + 1) * samples_per_bucket, num_samples)
        bucket_samples = samples[start_idx:end_idx]
        
        if not bucket_samples:
            peaks.append((0.0, 0.0))
            continue
            
        min_peak = min(bucket_samples) / max_val
        max_peak = max(bucket_samples) / max_val
        peaks.append((min_peak, max_peak))

    # Pad if necessary
    while len(peaks) < bucket_count:
        peaks.append((0.0, 0.0))

    return WaveformData(
        sample_rate=sample_rate,
        duration_ms=duration_ms,
        peaks=peaks[:bucket_count],
        bucket_count=bucket_count,
        level=level
    )


def extract_waveform_progressive(video_path: str, duration_ms: int = 0) -> Generator[WaveformData, None, None]:
    """
    Generator that yields WaveformData for level 0, then level 1, then level 2.
    """
    for level in range(3):
        data = extract_waveform(video_path, level=level, duration_ms=duration_ms)
        if data:
            yield data
