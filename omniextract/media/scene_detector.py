"""
Scene cut detection engine for OmniExtract Studio.
"""
import shutil
import subprocess
import re
from dataclasses import dataclass
from typing import List
import cv2
import numpy as np


@dataclass
class SceneCut:
    timestamp_ms: int
    confidence: float


def detect_scenes_ffmpeg(video_path: str, threshold: float = 0.4) -> List[SceneCut]:
    """
    Detects scene changes using FFmpeg's scene detection filter.
    """
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return []

    cmd = [
        ffmpeg_path,
        "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',metadata=print:file=-",
        "-f", "null",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=300)
    except Exception:
        if 'process' in locals():
            process.kill()
        return []

    output = stdout + stderr
    scene_cuts = []
    
    # A simple regex to find the time and score
    time_regex = re.compile(r"pts_time:([\d\.]+)")
    score_regex = re.compile(r"lavfi\.scene_score=([\d\.]+)")
    
    current_time_ms = 0
    for line in output.splitlines():
        time_match = time_regex.search(line)
        if time_match:
            current_time_ms = int(float(time_match.group(1)) * 1000)
            
        score_match = score_regex.search(line)
        if score_match:
            score = float(score_match.group(1))
            scene_cuts.append(SceneCut(timestamp_ms=current_time_ms, confidence=score))
            
    scene_cuts.sort(key=lambda x: x.timestamp_ms)
    return scene_cuts


def detect_scenes_histogram(video_path: str, threshold: float = 0.5, sample_interval_ms: int = 500) -> List[SceneCut]:
    """
    Detects scene changes using OpenCV histogram comparisons.
    """
    scene_cuts = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
            
        frame_interval = max(1, int(fps * (sample_interval_ms / 1000.0)))
        
        prev_hist = None
        frame_count = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
                
            if frame_count % frame_interval == 0:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
                
                if prev_hist is not None:
                    distance = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
                    if distance > threshold:
                        timestamp_ms = int((frame_count / fps) * 1000)
                        confidence = min(1.0, distance / (threshold * 5))
                        scene_cuts.append(SceneCut(timestamp_ms=timestamp_ms, confidence=confidence))
                
                prev_hist = hist
                
            frame_count += 1
            
        cap.release()
    except Exception:
        pass
        
    return scene_cuts


def detect_scenes(video_path: str, threshold: float = 0.4, method: str = 'auto') -> List[SceneCut]:
    """
    Detects scene changes using the specified method.
    """
    if method == 'auto':
        cuts = detect_scenes_ffmpeg(video_path, threshold)
        if not cuts:
            cuts = detect_scenes_histogram(video_path, threshold)
        return cuts
    elif method == 'ffmpeg':
        return detect_scenes_ffmpeg(video_path, threshold)
    elif method == 'histogram':
        return detect_scenes_histogram(video_path, threshold)
        
    return []
