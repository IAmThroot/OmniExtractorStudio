import subprocess
import shutil
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
    """Probe FFmpeg for available hardware encoders."""
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
        # Target keywords for hardware encoders
        hw_keywords = ['nvenc', 'qsv', 'amf', 'videotoolbox']
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            parts = line.split()
            if len(parts) >= 2:
                encoder_name = parts[1]
                if any(kw in encoder_name for kw in hw_keywords):
                    encoders.append(encoder_name)
                    
        return encoders
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
    
    # Prefix for standard codecs based on families
    codec_family = 'hevc' if is_hevc else 'h264'
    
    priorities = [
        f'{codec_family}_nvenc',
        f'{codec_family}_qsv',
        f'{codec_family}_amf',
        f'{codec_family}_videotoolbox'
    ]
    
    for enc in priorities:
        if enc in capabilities.encode:
            return ['-c:v', enc]
            
    # Software fallback
    fallback = 'libx265' if is_hevc else 'libx264'
    return ['-c:v', fallback]
