import json
import os
import shutil
import subprocess
import sys

from ..utils.timestamps import duration_text


def format_file_size(size):
    """Convert raw byte count into human-readable size string."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024


def probe_video_metadata(video_path):
    """Probe video properties using ffprobe (avoids OpenCV hanging on MKVs) and return metadata dictionary."""
    if not video_path or not os.path.isfile(video_path):
        return {}

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    
    try:
        cmd = [
            ffprobe, "-v", "error", "-show_format", "-show_streams",
            "-select_streams", "v:0", "-of", "json", video_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            print(f"ffprobe failed for {video_path}: {res.stderr}", file=sys.stderr)
            return {}
        data = json.loads(res.stdout)
            
        streams = data.get("streams", [])
        if not streams:
            return {}
        stream = streams[0]
        fmt = data.get("format", {})
            
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        codec = stream.get("codec_name", "Unknown")
            
        # parse fps
        fps = 0.0
        r_frame_rate = stream.get("r_frame_rate", "0/1")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            if float(den) != 0:
                fps = float(num) / float(den)
                    
        # parse duration
        duration_sec = float(fmt.get("duration", 0.0))
        duration_ms = int(duration_sec * 1000)
            
        # parse frame count
        nb_frames = stream.get("nb_frames")
        if nb_frames and nb_frames not in ("N/A", ""):
            try:
                frame_count = int(nb_frames)
            except (ValueError, TypeError):
                frame_count = int(duration_sec * fps) if fps > 0 else 0
        else:
            frame_count = int(duration_sec * fps) if fps > 0 else 0
                
    except Exception as e:
        print(f"probe_video_metadata error: {e}", file=sys.stderr)
        return {}

    return {
        "File": os.path.basename(video_path),
        "Resolution": f"{width} × {height}",
        "FPS": f"{fps:.3f}" if fps else "Unknown",
        "Total Frames": f"{frame_count:,}",
        "Duration": duration_text(duration_ms),
        "DurationMs": duration_ms,
        "RawFrames": frame_count,
        "RawFPS": fps,
        "Width": width,
        "Height": height,
        "Codec": codec or "Unknown",
        "File Size": format_file_size(os.path.getsize(video_path))
    }


def probe_video_chapters(source_file):
    """Probe video chapters using ffprobe and return list of dicts: [{'name': str, 'start_ms': int, 'end_ms': int}, ...]"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not source_file or not os.path.isfile(source_file):
        return []
    try:
        cmd = [
            ffprobe, "-v", "error", "-show_chapters", "-of", "json", source_file
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            raw_chapters = data.get("chapters", [])
            chapters = []
            for i, chap in enumerate(raw_chapters):
                start_sec = float(chap.get("start_time", 0.0))
                end_sec = float(chap.get("end_time", 0.0))
                start_ms = round(start_sec * 1000)
                end_ms = round(end_sec * 1000)

                tags = chap.get("tags", {})
                title = tags.get("title", f"Chapter {i + 1}")
                if end_ms > start_ms:
                    chapters.append({
                        "name": title,
                        "start_ms": start_ms,
                        "end_ms": end_ms
                    })
            return chapters
    except Exception:
        pass
    return []
