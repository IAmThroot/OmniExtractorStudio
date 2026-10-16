import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass
class HardwareCapabilities:
    decode: List[str]
    encode: List[str]
    ai: List[str]


def probe_decode_capabilities() -> List[str]:
    """Probe FFmpeg for available hardware decoders."""
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return []

    try:
        result = subprocess.run(
            [ffmpeg_path, '-hwaccels'],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.splitlines()
        hwaccels = []
        found_header = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('Hardware acceleration methods:'):
                found_header = True
                continue
            if found_header:
                hwaccels.append(line)
        return hwaccels
    except Exception:
        return []


def probe_encode_capabilities() -> List[str]:
    """Probe FFmpeg for available hardware encoders using robust regex parsing."""
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return []

    try:
        result = subprocess.run(
            [ffmpeg_path, '-encoders'],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.splitlines()
        encoders = []
        hw_keywords = ('nvenc', 'qsv', 'amf', 'videotoolbox', 'vaapi')
        pattern = re.compile(r'^\s*[VFA.S]{6}\s+([a-zA-Z0-9_-]+)')

        for line in lines:
            m = pattern.search(line)
            if m:
                enc = m.group(1).lower()
                if any(kw in enc for kw in hw_keywords):
                    encoders.append(enc)
            else:
                parts = line.strip().split()
                if len(parts) >= 2:
                    encoder_name = parts[1].lower()
                    if any(kw in encoder_name for kw in hw_keywords):
                        encoders.append(encoder_name)

        return list(dict.fromkeys(encoders))
    except Exception:
        return []


def probe_capabilities() -> HardwareCapabilities:
    """Probe system for hardware acceleration capabilities."""
    decode = probe_decode_capabilities()
    encode = probe_encode_capabilities()
    ai = []

    try:
        from omniextract.ai.providers import get_available_providers
        ai = get_available_providers()
    except ImportError:
        pass

    return HardwareCapabilities(decode=decode, encode=encode, ai=ai)


def get_hwaccel_decode_args(capabilities: HardwareCapabilities) -> List[str]:
    """Get the best FFmpeg decode arguments based on capabilities."""
    priorities = ['cuda', 'nvdec', 'qsv', 'd3d11va', 'vaapi', 'videotoolbox']

    for hw in priorities:
        if hw in capabilities.decode:
            return ['-hwaccel', hw]

    return []


def get_hwaccel_encode_args(capabilities: HardwareCapabilities, codec: str = 'h264') -> List[str]:
    """Get the best FFmpeg encode arguments based on capabilities and codec."""
    is_hevc = codec.lower() in ('hevc', 'h265')
    codec_family = 'hevc' if is_hevc else 'h264'

    priorities = [
        f'{codec_family}_nvenc',
        f'{codec_family}_qsv',
        f'{codec_family}_amf',
        f'{codec_family}_vaapi',
        f'{codec_family}_videotoolbox'
    ]

    for enc in priorities:
        if enc in capabilities.encode:
            return ['-c:v', enc]

    fallback = 'libx265' if is_hevc else 'libx264'
    return ['-c:v', fallback]


class EncodeProfile(list):
    """List of FFmpeg arguments representing a video encoding profile with helper properties."""
    @property
    def codec(self) -> str:
        return self[1] if len(self) > 1 else ""

    @property
    def video_args(self) -> list:
        return list(self)

    def get(self, key, default=None):
        if key == "video_args":
            return self.video_args
        if key == "codec":
            return self.codec
        return default


def get_hwaccel_encode_profile(
    capabilities: Optional[HardwareCapabilities] = None,
    codec: str = 'h264',
    quality_crf: int = 20,
    preset: str = "medium",
    crf: Optional[int] = None
) -> EncodeProfile:
    """
    Get full FFmpeg video encoder profile flags (codec, rate control, and pixel format).
    Supports NVENC (-cq), QSV (-global_quality), VAAPI (-qp), and software x264/x265 (-crf).
    """
    if crf is not None:
        quality_crf = crf
    if capabilities is None:
        capabilities = probe_capabilities()

    base_args = get_hwaccel_encode_args(capabilities, codec=codec)
    enc = base_args[1] if len(base_args) > 1 else ""

    if "nvenc" in enc:
        args = base_args + ["-cq", str(quality_crf), "-preset", "p4", "-pix_fmt", "yuv420p"]
    elif "qsv" in enc:
        args = base_args + ["-global_quality", str(quality_crf), "-pix_fmt", "nv12"]
    elif "vaapi" in enc:
        args = base_args + ["-qp", str(quality_crf)]
    else:
        args = base_args + ["-crf", str(quality_crf), "-preset", preset, "-pix_fmt", "yuv420p"]

    return EncodeProfile(args)

