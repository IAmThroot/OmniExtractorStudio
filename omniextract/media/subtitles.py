import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from html import unescape

import cv2
import numpy as np

from ..utils.timestamps import format_timestamp


# Matches HH:MM:SS,mmm --> HH:MM:SS,mmm tolerating 1-3 digit hours and
# 1-3 digit milliseconds (SRT uses ',' and WebVTT uses '.').
_TIME_RE = re.compile(
    r'(\d{1,3}):([0-5]?\d):([0-5]?\d)[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,3}):([0-5]?\d):([0-5]?\d)[,.](\d{1,3})'
)
_TAG_RE = re.compile(r'<[^>]+>')
_VTT_TAG_RE = re.compile(r'\{[^}]*\}')


def _time_to_ms(hours, minutes, seconds, fraction):
    frac = (fraction + "00")[:3]
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(frac)


def parse_subtitles(file_path):
    """
    Parses .srt or .vtt subtitle file into a sorted list of tuples:
    [(start_ms, end_ms, text), ...]

    Uses a linear block scan rather than a single backtracking regex, so it is
    safe on very large subtitle files, and tolerates single-digit hours,
    1-3 digit milliseconds, WebVTT cue settings, and inline tags.
    """
    if not file_path or not os.path.isfile(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return []

    content = content.replace("\r\n", "\n").replace("\r", "\n")

    entries = []
    for block in content.split("\n\n"):
        lines = block.split("\n")
        timing_positions = []
        for idx, line in enumerate(lines):
            m = _TIME_RE.search(line)
            if m:
                timing_positions.append((idx, m))

        for k, (idx, m) in enumerate(timing_positions):
            end_line = timing_positions[k + 1][0] if k + 1 < len(timing_positions) else len(lines)
            start_ms = _time_to_ms(*m.group(1, 2, 3, 4))
            end_ms = _time_to_ms(*m.group(5, 6, 7, 8))

            text_lines = lines[idx + 1:end_line]
            # Drop framing-only lines (trailing blanks, leading cue numbers).
            while text_lines and not text_lines[-1].strip():
                text_lines.pop()
            while text_lines and text_lines[0].strip().isdigit():
                text_lines.pop(0)

            raw_text = "\n".join(text_lines)
            text = _TAG_RE.sub("", _VTT_TAG_RE.sub("", raw_text))
            clean_text = unescape(text).replace("\xa0", " ").strip()
            if clean_text:
                entries.append((start_ms, end_ms, clean_text))

    entries.sort(key=lambda x: x[0])
    return entries


def draw_subtitle_on_frame(image, text):
    """Draw centered multi-line subtitle text at the bottom of the OpenCV image with shadow and background box."""
    if not text or image is None:
        return image

    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 1:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    h, w = image.shape[:2]
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return image

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.5, min(2.0, (w / 1280.0) * 0.9))
    thickness = max(1, int(scale * 2.2))
    outline_thickness = thickness + max(2, int(scale * 3.0))
    line_spacing = int(36 * scale)

    line_sizes = [cv2.getTextSize(l, font, scale, thickness)[0] for l in lines]
    max_line_w = max(s[0] for s in line_sizes)
    total_text_h = len(lines) * line_spacing
    margin_bottom = int(h * 0.05)
    start_y = h - margin_bottom - total_text_h + line_spacing

    box_pad_x = int(20 * scale)
    box_pad_y = int(10 * scale)
    box_x1 = max(0, (w - max_line_w) // 2 - box_pad_x)
    box_x2 = min(w, (w + max_line_w) // 2 + box_pad_x)
    box_y1 = max(0, start_y - line_spacing + int(8 * scale) - box_pad_y)
    box_y2 = min(h, start_y + (len(lines) - 1) * line_spacing + box_pad_y)

    if box_y2 > box_y1 and box_x2 > box_x1:
        roi = image[box_y1:box_y2, box_x1:box_x2]
        image[box_y1:box_y2, box_x1:box_x2] = (roi.astype(np.float32) * 0.35).astype(np.uint8)

    for i, line in enumerate(lines):
        line_w = line_sizes[i][0]
        x = (w - line_w) // 2
        y = start_y + i * line_spacing
        cv2.putText(image, line, (x, y), font, scale, (0, 0, 0), outline_thickness, cv2.LINE_AA)
        cv2.putText(image, line, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

    return image


def extract_subtitles_to_temp(source_video, sub_index):
    """Extract an embedded subtitle track to a temp .srt file and return its path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_video or not os.path.isfile(source_video):
        return ""
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
        temp_srt = f.name
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", source_video, "-map", f"0:s:{sub_index}",
        "-c:s", "srt", temp_srt
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
        if os.path.isfile(temp_srt) and os.path.getsize(temp_srt) > 0:
            return temp_srt
    except Exception:
        pass
    if os.path.isfile(temp_srt):
        try:
            os.remove(temp_srt)
        except OSError:
            pass
    return ""


def write_shifted_srt(subtitles_list, start_ms, end_ms):
    """
    Takes a list of (start_ms, end_ms, text) and writes a temporary .srt file
    shifted by -start_ms for the range [start_ms, end_ms].
    """
    if not subtitles_list:
        return ""

    filtered = []
    idx = 1
    for s, e, text in subtitles_list:
        if e >= start_ms and s <= end_ms:
            clip_dur = end_ms - start_ms
            rel_s = max(0, s - start_ms)
            rel_e = min(clip_dur, max(rel_s + 100, e - start_ms))

            s_str = format_timestamp(rel_s, ":").replace(".", ",")
            e_str = format_timestamp(rel_e, ":").replace(".", ",")

            filtered.append(f"{idx}\n{s_str} --> {e_str}\n{text}\n")
            idx += 1

    if not filtered:
        return ""

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8") as f:
            f.write("\n".join(filtered) + "\n")
            return f.name
    except Exception:
        return ""



