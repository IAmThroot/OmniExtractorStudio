"""
Hierarchical audio waveform extraction for OmniExtract Studio.
"""
import math
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Generator, Tuple, List

import numpy as np


@dataclass
class WaveformData:
    sample_rate: int
    duration_ms: int
    peaks: List[Tuple[float, float]]
    bucket_count: int
    level: int


def _compute_waveform_from_samples(
    samples: np.ndarray,
    sample_rate: int,
    bucket_count: int,
    level: int,
    duration_ms: int = 0
) -> WaveformData:
    num_samples = len(samples)
    if duration_ms == 0:
        duration_ms = int((num_samples / sample_rate) * 1000)

    samples_per_bucket = max(1, num_samples // bucket_count)
    actual_bucket_count = min(bucket_count, math.ceil(num_samples / samples_per_bucket))

    usable_len = actual_bucket_count * samples_per_bucket
    usable_samples = samples[:usable_len]

    if len(usable_samples) == usable_len and usable_len > 0:
        reshaped = usable_samples.reshape(actual_bucket_count, samples_per_bucket)
        mins = reshaped.min(axis=1) / 32768.0
        maxs = reshaped.max(axis=1) / 32768.0
        peaks = list(zip(mins.tolist(), maxs.tolist()))
    else:
        peaks = []
        max_val = 32768.0
        for i in range(actual_bucket_count):
            start_idx = i * samples_per_bucket
            end_idx = min((i + 1) * samples_per_bucket, num_samples)
            bucket = samples[start_idx:end_idx]
            if len(bucket) == 0:
                peaks.append((0.0, 0.0))
            else:
                peaks.append((float(bucket.min()) / max_val, float(bucket.max()) / max_val))

    while len(peaks) < bucket_count:
        peaks.append((0.0, 0.0))

    return WaveformData(
        sample_rate=sample_rate,
        duration_ms=duration_ms,
        peaks=peaks[:bucket_count],
        bucket_count=bucket_count,
        level=level
    )


def _decode_audio_samples(video_path: str, sample_rate: int = 8000) -> Optional[Tuple[np.ndarray, int]]:
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return None

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

    samples = np.frombuffer(stdout, dtype=np.int16)
    if len(samples) == 0:
        return None

    return samples, sample_rate


def extract_waveform(video_path: str, level: int = 0, duration_ms: int = 0) -> Optional[WaveformData]:
    """
    Extracts waveform data from a video file.
    level 0: 500 buckets
    level 1: 2000 buckets
    level 2: 8000 buckets
    """
    if level == 0:
        bucket_count = 500
    elif level == 1:
        bucket_count = 2000
    else:
        bucket_count = 8000

    decoded = _decode_audio_samples(video_path, sample_rate=8000)
    if not decoded:
        return None

    samples, sample_rate = decoded
    return _compute_waveform_from_samples(
        samples, sample_rate, bucket_count, level, duration_ms=duration_ms
    )


def extract_waveform_progressive(video_path: str, duration_ms: int = 0) -> Generator[WaveformData, None, None]:
    """
    Generator that decodes audio once and yields WaveformData for level 0, level 1, and level 2.
    """
    decoded = _decode_audio_samples(video_path, sample_rate=8000)
    if not decoded:
        return

    samples, sample_rate = decoded
    bucket_counts = [500, 2000, 8000]
    for level, b_count in enumerate(bucket_counts):
        yield _compute_waveform_from_samples(
            samples, sample_rate, b_count, level, duration_ms=duration_ms
        )
