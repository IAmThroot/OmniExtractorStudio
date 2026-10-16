"""
Per-video analysis caching system.
"""

import os
import json
import hashlib
import shutil
from typing import Optional, Union, List

def compute_video_hash(video_path: str) -> str:
    """
    Compute a fast, robust content-based hash combining file size, mtime,
    and distributed block samples (0%, 25%, 50%, 75%, 100%).
    """
    if not os.path.exists(video_path):
        return ""
    
    try:
        stat = os.stat(video_path)
        file_size = stat.st_size
        mtime = stat.st_mtime
        
        hasher = hashlib.sha256()
        hasher.update(f"{file_size}:{mtime}:".encode('utf-8'))
        
        block_size = 65536  # 64 KB
        
        with open(video_path, 'rb') as f:
            if file_size <= block_size * 5:
                hasher.update(f.read())
            else:
                checkpoints = [
                    0,
                    int(file_size * 0.25),
                    int(file_size * 0.50),
                    int(file_size * 0.75),
                    max(0, file_size - block_size),
                ]
                for offset in checkpoints:
                    f.seek(offset)
                    hasher.update(f.read(block_size))
                
        return hasher.hexdigest()
    except (OSError, IOError):
        return ""

def get_cache_dir() -> str:
    """Get the base cache directory path."""
    from PyQt6.QtCore import QStandardPaths
    cache_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    app_cache_dir = os.path.join(cache_path, "OmniExtract")
    os.makedirs(app_cache_dir, exist_ok=True)
    return app_cache_dir

class AnalysisCache:
    """Cache manager for video analysis data."""
    
    def __init__(self, video_path: str):
        self.video_path = video_path
        self._hash = compute_video_hash(video_path)
        
    @property
    def cache_dir(self) -> str:
        """Get the full path to the cache directory for this video."""
        if not self._hash:
            return ""
        return os.path.join(get_cache_dir(), self._hash)

    def is_valid(self) -> bool:
        """Check if cache directory and metadata exist, and match current video hash."""
        if not self._hash or not self.cache_dir or not os.path.isdir(self.cache_dir):
            return False
            
        metadata = self.load_metadata()
        if not metadata:
            return False
            
        return metadata.get('video_hash') == self._hash

    def _save_json(self, filename: str, data: Union[dict, list]):
        if not self.cache_dir:
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        target = os.path.join(self.cache_dir, filename)
        temp_target = target + ".tmp"
        with open(temp_target, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(temp_target, target)

    def _load_json(self, filename: str):
        if not self.cache_dir:
            return None
        filepath = os.path.join(self.cache_dir, filename)
        if not os.path.isfile(filepath):
            return None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None

    def save_metadata(self, metadata: dict):
        """Save metadata containing the hash to validate cache without mutating caller dictionary."""
        meta_copy = dict(metadata)
        meta_copy['video_hash'] = self._hash
        self._save_json('metadata.json', meta_copy)

    def load_metadata(self) -> Optional[dict]:
        """Load metadata."""
        return self._load_json('metadata.json')
        
    def save_waveform(self, data: bytes):
        """Save raw waveform data atomically."""
        if not self.cache_dir:
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        target = os.path.join(self.cache_dir, 'waveform.dat')
        temp_target = target + ".tmp"
        with open(temp_target, 'wb') as f:
            f.write(data)
        os.replace(temp_target, target)

    def load_waveform(self) -> Optional[bytes]:
        """Load raw waveform data."""
        if not self.cache_dir:
            return None
        filepath = os.path.join(self.cache_dir, 'waveform.dat')
        if not os.path.isfile(filepath):
            return None
        with open(filepath, 'rb') as f:
            return f.read()
            
    def save_scenes(self, scenes: List[dict]):
        self._save_json('scenes.json', scenes)
        
    def load_scenes(self) -> Optional[List[dict]]:
        return self._load_json('scenes.json')
        
    def save_subtitles(self, subtitles: List[dict]):
        self._save_json('subtitles.json', subtitles)
        
    def load_subtitles(self) -> Optional[List[dict]]:
        return self._load_json('subtitles.json')

    def invalidate(self):
        """Remove entire cache directory for this video."""
        if self.cache_dir and os.path.isdir(self.cache_dir):
            shutil.rmtree(self.cache_dir)
