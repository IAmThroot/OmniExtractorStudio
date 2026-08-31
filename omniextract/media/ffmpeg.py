import shutil
import subprocess


def get_ffmpeg_path():
    """Find FFmpeg binary on PATH."""
    return shutil.which("ffmpeg")


def get_ffprobe_path():
    """Find FFprobe binary on PATH."""
    return shutil.which("ffprobe")





def get_ffmpeg_version():
    """Retrieve FFmpeg version string if installed."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return "Not found"
    try:
        res = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            first_line = res.stdout.splitlines()[0]
            return first_line
    except Exception:
        pass
    return "Unknown"

def check_ffmpeg_available():
    return bool(get_ffmpeg_path() and get_ffprobe_path())
