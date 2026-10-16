import json
import os
import shutil
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from ..media.metadata import probe_video_metadata


def probe_subtitle_tracks_for_video(video_path, external_subtitle_file=""):
    """Probe embedded and external subtitle tracks for a video without GUI dependencies."""
    subtitle_tracks = []
    if video_path and os.path.isfile(video_path):
        base_dir = os.path.dirname(video_path)
        base_name = os.path.splitext(os.path.basename(video_path))[0]

        auto_detected_subs = []
        for ext in (".srt", ".vtt", ".en.srt", ".eng.srt", ".en.vtt"):
            candidate = os.path.join(base_dir, base_name + ext)
            if os.path.isfile(candidate) and candidate != external_subtitle_file:
                auto_detected_subs.append(candidate)

        if not external_subtitle_file and auto_detected_subs:
            external_subtitle_file = auto_detected_subs[0]

    if external_subtitle_file and os.path.isfile(external_subtitle_file):
        subtitle_tracks.append({
            "type": "external",
            "path": external_subtitle_file,
            "label": f"External: {os.path.basename(external_subtitle_file)}"
        })

    ffprobe = shutil.which("ffprobe")
    if ffprobe and video_path and os.path.isfile(video_path):
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=index,codec_name:stream_tags=language,title",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                for i, stream in enumerate(streams):
                    tags = stream.get("tags", {})
                    lang = tags.get("language", "und")
                    title = tags.get("title", "")
                    codec = stream.get("codec_name", "unknown")
                    idx = stream.get("index", i)

                    label = f"Track {i}: {lang}"
                    if title:
                        label += f" — {title}"
                    label += f" ({codec})"

                    subtitle_tracks.append({
                        "type": "embedded",
                        "index": idx,
                        "sub_index": i,
                        "codec": codec,
                        "language": lang,
                        "title": title,
                        "label": label
                    })
        except Exception:
            pass

    return subtitle_tracks, external_subtitle_file


class MetadataProbeWorker(QThread):
    """Offloads ffprobe video metadata and subtitle probing from the UI thread."""
    metadata_ready = pyqtSignal(str, dict, list, str)  # (video_path, metadata, subtitle_tracks, external_sub)
    error = pyqtSignal(str, str)  # (video_path, error_message)

    def __init__(self, video_path, external_subtitle_file=""):
        super().__init__()
        self.video_path = video_path
        self.external_subtitle_file = external_subtitle_file

    def run(self):
        if not self.video_path or not os.path.isfile(self.video_path):
            self.error.emit(self.video_path, "Source file does not exist.")
            return

        try:
            metadata = probe_video_metadata(self.video_path)
            if not metadata:
                self.error.emit(self.video_path, "Could not open video metadata.")
                return

            tracks, external_sub = probe_subtitle_tracks_for_video(
                self.video_path, self.external_subtitle_file
            )
            self.metadata_ready.emit(self.video_path, metadata, tracks, external_sub)
        except Exception as exc:
            self.error.emit(self.video_path, str(exc))
