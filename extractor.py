import sys
import os
import cv2
import time
import json
import math
import shutil
import subprocess
import gc
import concurrent.futures
import csv
import re
import threading
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QFileDialog, QCheckBox,
    QProgressBar, QVBoxLayout, QTimeEdit, QComboBox, QHBoxLayout,
    QMessageBox, QTabWidget, QSpinBox, QDialog, QSlider, QLineEdit,
    QGroupBox, QFormLayout, QDoubleSpinBox, QListWidget,
    QAbstractItemView, QScrollArea, QGridLayout,
    QInputDialog, QToolButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGraphicsView, QGraphicsScene
)
from PyQt6.QtCore import Qt, QTime, QTimer, QSettings, QSize, QThread, pyqtSignal, QUrl, QRectF
from PyQt6.QtGui import QImage, QPixmap, QIcon, QDragEnterEvent, QDropEvent, QDesktopServices
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

VIDEO_FILTER = (
    "Video Files (*.mp4 *.mkv *.avi *.flv *.gif *.m4v *.wmv *.mov *.webm);;"
    "All Files (*)"
)


def get_resource_path(relative_path):
    """Resolve file path for development or PyInstaller standalone packaging."""
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    direct_path = os.path.join(base_path, relative_path)
    if os.path.exists(direct_path):
        return direct_path

    exe_dir = os.path.dirname(sys.executable)
    fallback = os.path.join(exe_dir, relative_path)
    if os.path.exists(fallback):
        return fallback

    return direct_path


def format_timestamp(milliseconds, separator=":"):
    milliseconds = max(0, int(milliseconds))
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    seconds = milliseconds // 1_000
    millis = milliseconds % 1_000
    return f"{hours:02d}{separator}{minutes:02d}{separator}{seconds:02d}.{millis:03d}"


def qtime_to_ms(qtime):
    return (
        qtime.hour() * 3_600_000
        + qtime.minute() * 60_000
        + qtime.second() * 1_000
        + qtime.msec()
    )


def ms_to_qtime(milliseconds):
    milliseconds = max(0, int(milliseconds))
    milliseconds = min(
        milliseconds,
        23 * 3_600_000 + 59 * 60_000 + 59 * 1_000 + 999
    )
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    seconds = milliseconds // 1_000
    msec = milliseconds % 1_000
    return QTime(hours, minutes, seconds, msec)


def duration_text(milliseconds):
    return format_timestamp(milliseconds)


def frame_to_ms(frame, fps):
    return int(round((frame / fps) * 1000)) if fps else 0


def parse_subtitles(file_path):
    """
    Parses .srt or .vtt subtitle file into a sorted list of tuples:
    [(start_ms, end_ms, text), ...]
    """
    if not file_path or not os.path.isfile(file_path):
        return []
    
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return []
    
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(
        r'(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})(?:[^\n]*)?\n(.*?)(?=\n\n|\n\d+\n|\Z)',
        re.DOTALL
    )
    
    entries = []
    for match in pattern.finditer(content):
        sh1, sm1, ss1, sms1, sh2, sm2, ss2, sms2, text = match.groups()
        h1 = int(sh1) if sh1 else 0
        m1 = int(sm1)
        s1 = int(ss1)
        ms1 = int(sms1)
        start_ms = (h1 * 3600 + m1 * 60 + s1) * 1000 + ms1
        
        h2 = int(sh2) if sh2 else 0
        m2 = int(sm2)
        s2 = int(ss2)
        ms2 = int(sms2)
        end_ms = (h2 * 3600 + m2 * 60 + s2) * 1000 + ms2
        
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        if clean_text:
            entries.append((start_ms, end_ms, clean_text))
            
    entries.sort(key=lambda x: x[0])
    return entries


def draw_subtitle_on_frame(image, text):
    """Draw centered multi-line subtitle text at the bottom of the OpenCV image with shadow and background box."""
    if not text or image is None:
        return image
        
    h, w, _ = image.shape
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
    
    # Semi-transparent dark background box behind text
    box_pad_x = int(20 * scale)
    box_pad_y = int(10 * scale)
    box_x1 = max(0, (w - max_line_w) // 2 - box_pad_x)
    box_x2 = min(w, (w + max_line_w) // 2 + box_pad_x)
    box_y1 = max(0, start_y - line_spacing + int(8 * scale) - box_pad_y)
    box_y2 = min(h, start_y + (len(lines) - 1) * line_spacing + box_pad_y)
    
    if box_y2 > box_y1 and box_x2 > box_x1:
        roi = image[box_y1:box_y2, box_x1:box_x2]
        image[box_y1:box_y2, box_x1:box_x2] = (roi * 0.35).astype(np.uint8)
    
    for i, line in enumerate(lines):
        line_w = line_sizes[i][0]
        x = (w - line_w) // 2
        y = start_y + i * line_spacing
        # Black outline
        cv2.putText(image, line, (x, y), font, scale, (0, 0, 0), outline_thickness, cv2.LINE_AA)
        # Crisp white text
        cv2.putText(image, line, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
        
    return image


def extract_subtitles_to_temp(source_video, sub_index):
    """Extract an embedded subtitle track to a temp .srt file and return its path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_video or not os.path.isfile(source_video):
        return ""
    import tempfile
    temp_srt = os.path.join(tempfile.gettempdir(), f"omni_sub_{os.getpid()}_{sub_index}.srt")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", source_video, "-map", f"0:s:{sub_index}",
        "-c:s", "srt", temp_srt
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if os.path.isfile(temp_srt) and os.path.getsize(temp_srt) > 0:
            return temp_srt
    except Exception:
        pass
    return ""


def write_shifted_srt(subtitles_list, start_ms, end_ms):
    """
    Takes a list of (start_ms, end_ms, text) and writes a temporary .srt file
    shifted by -start_ms for the range [start_ms, end_ms].
    Returns the path to the temporary .srt file, or '' if no subtitles in range.
    """
    if not subtitles_list:
        return ""
        
    filtered = []
    idx = 1
    for s, e, text in subtitles_list:
        if e >= start_ms and s <= end_ms:
            rel_s = max(0, s - start_ms)
            rel_e = max(rel_s + 100, e - start_ms)
            
            s_str = format_timestamp(rel_s, ":").replace(".", ",")
            e_str = format_timestamp(rel_e, ":").replace(".", ",")
            
            filtered.append(f"{idx}\n{s_str} --> {e_str}\n{text}\n")
            idx += 1
            
    if not filtered:
        return ""
        
    import tempfile
    temp_srt = os.path.join(tempfile.gettempdir(), f"omni_shifted_{os.getpid()}_{int(time.time()*1000)}.srt")
    try:
        with open(temp_srt, "w", encoding="utf-8") as f:
            f.write("\n".join(filtered) + "\n")
        return temp_srt
    except Exception:
        return ""


def probe_video_chapters(source_file):
    """Probe video chapters using ffprobe and return list of dicts: [{'name': str, 'start_ms': int, 'end_ms': int}, ...]"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not source_file or not os.path.isfile(source_file):
        return []
    try:
        cmd = [
            ffprobe, "-v", "error", "-show_chapters", "-of", "json", source_file
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            raw_chapters = data.get("chapters", [])
            chapters = []
            for i, chap in enumerate(raw_chapters):
                start_sec = float(chap.get("start_time", 0.0))
                end_sec = float(chap.get("end_time", 0.0))
                start_ms = int(round(start_sec * 1000))
                end_ms = int(round(end_sec * 1000))
                
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


class DropLineEdit(QLineEdit):
    """Line edit that accepts a dropped video path."""

    def __init__(self, callback=None, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                if self.callback:
                    self.callback(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()


class SubtitleVideoWidget(QGraphicsView):
    """Video widget supporting subtitle overlays via QGraphicsView."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.video_item = QGraphicsVideoItem()
        self.scene().addItem(self.video_item)
        
        self.sub_label = QLabel()
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        
        self.current_subtitle_color = "white"
        self.current_subtitle_size = 22
        
        self.sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.sub_label.setWordWrap(True)
        
        self.sub_proxy = self.scene().addWidget(self.sub_label)
        self.sub_proxy.setZValue(1)
        self.sub_proxy.hide()
        
        # We will add a drop shadow so transparent-background text remains readable
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(4)
        from PyQt6.QtGui import QColor
        shadow.setColor(QColor(0, 0, 0, 255))
        shadow.setOffset(2, 2)
        self.sub_proxy.setGraphicsEffect(shadow)
        
        self._update_subtitle_style()
        
        # Hide scrollbars and margins
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: black; border: none;")
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

    def videoOutput(self):
        return self.video_item

    def set_subtitle_color(self, color_name):
        self.current_subtitle_color = color_name.lower()
        self._update_subtitle_style()

    def set_subtitle_size(self, size):
        self.current_subtitle_size = size
        self._update_subtitle_style()

    def _update_subtitle_style(self):
        self.sub_label.setStyleSheet(
            f"color: {self.current_subtitle_color}; background: transparent; "
            f"font-weight: bold; font-size: {self.current_subtitle_size}px; padding-bottom: 20px;"
        )

    def set_subtitle(self, text):
        if text:
            if self.sub_label.text() != text:
                self.sub_label.setText(text)
                self._reposition()
            if not self.sub_proxy.isVisible():
                self.sub_proxy.show()
        else:
            if self.sub_proxy.isVisible():
                self.sub_proxy.hide()

    def _reposition(self):
        w = self.width()
        h = self.height()
        
        self.scene().setSceneRect(0, 0, w, h)
        self.video_item.setSize(QRectF(0, 0, w, h).size())
        
        # Full width, positioned in the lower third
        lbl_h = max(100, int(h / 3))
        y = h - lbl_h
        
        self.sub_label.setFixedSize(w, lbl_h)
        self.sub_proxy.setPos(0, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()


class ThumbnailWorker(QThread):
    """Asynchronously generates timeline thumbnails in the background."""
    thumbnail_ready = pyqtSignal(int, int, QPixmap)  # (index, timestamp_ms, QPixmap)

    def __init__(self, video_path, fps, frame_count, count=12):
        super().__init__()
        self.video_path = video_path
        self.fps = fps
        self.frame_count = frame_count
        self.count = count
        self.cancel_requested = False

    def run(self):
        if self.frame_count <= 0 or not os.path.isfile(self.video_path):
            return
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return

        count = min(self.count, max(6, self.frame_count))
        for i in range(count):
            if self.cancel_requested:
                break
            frame_number = int((self.frame_count - 1) * i / max(1, count - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            success, frame = cap.read()
            if not success or frame is None:
                continue

            timestamp_ms = frame_to_ms(frame_number, self.fps)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(qimg).scaled(
                130, 75,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.thumbnail_ready.emit(i, timestamp_ms, pixmap)

        cap.release()


class VideoPreviewDialog(QDialog):
    """Video preview dialog with playback, subtitles, and scrubbing."""

    in_selected = pyqtSignal(int)
    out_selected = pyqtSignal(int)

    def __init__(self, video_path, subtitle_tracks=None, initial_sub_index=0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.subtitle_tracks = subtitle_tracks or []
        self.initial_sub_index = initial_sub_index
        self.subtitles_data = []  # [(start_ms, end_ms, text), ...]
        self._temp_extracted_srt = None
        self._slider_dragging = False

        # Probe video metadata
        self.cap = cv2.VideoCapture(video_path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration_ms = int((self.frame_count / self.fps) * 1000) if self.frame_count else 0
        self.cap.release()

        self.setWindowTitle(f"Preview - {os.path.basename(video_path)} ({self.fps:.2f} fps)")
        self.resize(1020, 740)

        # QtMultimedia Setup (native hardware-accelerated playback pipeline)
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)

        self.video_widget = SubtitleVideoWidget()
        self.video_widget.setMinimumSize(640, 360)
        self.media_player.setVideoOutput(self.video_widget.videoOutput())

        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setMinimum(0)
        self.position_slider.setMaximum(max(0, self.duration_ms))
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_play)

        self.prev_button = QPushButton("◀ Frame")
        self.prev_button.clicked.connect(self.previous_frame)

        self.next_button = QPushButton("Frame ▶")
        self.next_button.clicked.connect(self.next_single_frame)

        self.set_in_button = QPushButton("Set Start (In)")
        self.set_in_button.clicked.connect(lambda: self.in_selected.emit(self.media_player.position()))

        self.set_out_button = QPushButton("Set End (Out)")
        self.set_out_button.clicked.connect(lambda: self.out_selected.emit(self.media_player.position()))

        self.save_frame_button = QPushButton("Save Current Frame")
        self.save_frame_button.clicked.connect(self.save_current_frame)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)

        # Speed, Subtitle & Audio Controls
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25x", "0.5x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self.change_speed)

        # Subtitle track selector
        self.sub_combo = QComboBox()
        self.sub_combo.addItem("Subtitles: Off")
        for track in self.subtitle_tracks:
            self.sub_combo.addItem(f"Sub: {track['label']}")
        self.sub_combo.addItem("<Browse External Subtitle...>")
        self.sub_combo.currentIndexChanged.connect(self._on_subtitle_track_changed)
        
        # Subtitle color selector
        self.color_combo = QComboBox()
        self.color_combo.addItems(["White", "Black", "Yellow", "Red", "Green", "Blue"])
        self.color_combo.currentTextChanged.connect(self.video_widget.set_subtitle_color)

        # Subtitle font size selector
        self.size_spin = QSpinBox()
        self.size_spin.setRange(10, 100)
        self.size_spin.setValue(22)
        self.size_spin.valueChanged.connect(self.video_widget.set_subtitle_size)

        self.mute_button = QPushButton("Mute")
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self.audio_output.setMuted)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100.0))

        media_extras = QHBoxLayout()
        media_extras.addWidget(QLabel("Speed:"))
        media_extras.addWidget(self.speed_combo)
        media_extras.addWidget(QLabel("Subtitles:"))
        media_extras.addWidget(self.sub_combo)
        media_extras.addWidget(QLabel("Color:"))
        media_extras.addWidget(self.color_combo)
        media_extras.addWidget(QLabel("Size:"))
        media_extras.addWidget(self.size_spin)
        media_extras.addStretch()
        media_extras.addWidget(self.mute_button)
        media_extras.addWidget(QLabel("Vol:"))
        media_extras.addWidget(self.volume_slider)

        controls = QHBoxLayout()
        controls.addWidget(self.prev_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.set_in_button)
        controls.addWidget(self.set_out_button)
        controls.addWidget(self.save_frame_button)
        controls.addWidget(self.close_button)

        self.thumbnail_scroll = QScrollArea()
        self.thumbnail_scroll.setWidgetResizable(True)
        self.thumbnail_container = QWidget()
        self.thumbnail_layout = QHBoxLayout(self.thumbnail_container)
        self.thumbnail_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.thumbnail_scroll.setWidget(self.thumbnail_container)
        self.thumbnail_scroll.setFixedHeight(115)

        layout = QVBoxLayout()
        layout.addWidget(self.video_widget)
        layout.addLayout(media_extras)
        layout.addWidget(self.time_label)
        layout.addWidget(self.position_slider)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Timeline thumbnails (click to jump):"))
        layout.addWidget(self.thumbnail_scroll)
        self.setLayout(layout)

        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.playbackStateChanged.connect(self.update_play_state)

        # Set media source
        self.media_player.setSource(QUrl.fromLocalFile(video_path))

        # Select initial subtitle track if provided
        if self.initial_sub_index > 0 and self.initial_sub_index <= len(self.subtitle_tracks):
            self.sub_combo.setCurrentIndex(self.initial_sub_index)

        # Launch background thumbnail generator
        self.thumb_worker = ThumbnailWorker(video_path, self.fps, self.frame_count, count=12)
        self.thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.thumb_worker.start()

    def _on_thumbnail_ready(self, index, timestamp_ms, pixmap):
        button = QToolButton()
        button.setIcon(QIcon(pixmap))
        button.setText(format_timestamp(timestamp_ms))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(130, 75))
        button.clicked.connect(lambda checked=False, ms=timestamp_ms: self.media_player.setPosition(ms))
        self.thumbnail_layout.addWidget(button)

    def _on_subtitle_track_changed(self, index):
        if index == self.sub_combo.count() - 1:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select Subtitle File",
                os.path.dirname(self.video_path),
                "Subtitle Files (*.srt *.vtt);;All Files (*)"
            )
            if file_path:
                self.subtitles_data = parse_subtitles(file_path)
                lbl = f"External: {os.path.basename(file_path)}"
                self.sub_combo.blockSignals(True)
                self.sub_combo.setItemText(index, f"Sub: {lbl}")
                self.sub_combo.blockSignals(False)
            else:
                self.sub_combo.setCurrentIndex(0)
            return

        if index <= 0 or index > len(self.subtitle_tracks):
            self.subtitles_data = []
            self.video_widget.set_subtitle("")
            return

        track = self.subtitle_tracks[index - 1]
        if track.get("type") == "external":
            self.subtitles_data = parse_subtitles(track["path"])
        else:
            si = track["sub_index"]
            temp_ext = extract_subtitles_to_temp(self.video_path, si)
            if temp_ext:
                self._temp_extracted_srt = temp_ext
                self.subtitles_data = parse_subtitles(temp_ext)

        # Immediately refresh subtitle display at current playhead
        self._refresh_subtitle_at(self.media_player.position())

    def _get_subtitle_at(self, position_ms):
        if not self.subtitles_data:
            return ""
        active_lines = []
        for s, e, text in self.subtitles_data:
            if s <= position_ms <= e:
                active_lines.append(text)
            elif s > position_ms:
                break
        return "\n".join(active_lines)

    def _refresh_subtitle_at(self, position_ms):
        sub_text = self._get_subtitle_at(position_ms)
        self.video_widget.set_subtitle(sub_text)

    def _on_slider_pressed(self):
        self._slider_dragging = True

    def _on_slider_released(self):
        self._slider_dragging = False
        self.media_player.setPosition(self.position_slider.value())

    def _on_slider_moved(self, pos):
        self.media_player.setPosition(pos)
        self.time_label.setText(f"{format_timestamp(pos)} / {format_timestamp(self.duration_ms)}")
        self._refresh_subtitle_at(pos)

    def change_speed(self, speed_str):
        try:
            speed_val = float(speed_str.replace("x", ""))
            self.media_player.setPlaybackRate(speed_val)
        except ValueError:
            pass

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Right:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                self.media_player.setPosition(min(self.duration_ms, self.media_player.position() + 5000))
            else:
                self.next_single_frame()
        elif key == Qt.Key.Key_Left:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                self.media_player.setPosition(max(0, self.media_player.position() - 5000))
            else:
                self.previous_frame()
        elif key == Qt.Key.Key_I:
            self.in_selected.emit(self.media_player.position())
            QMessageBox.information(self, "In Point", "Start (In) point set.")
        elif key == Qt.Key.Key_O:
            self.out_selected.emit(self.media_player.position())
            QMessageBox.information(self, "Out Point", "End (Out) point set.")
        else:
            super().keyPressEvent(event)

    def update_position(self, position_ms):
        if not self._slider_dragging:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position_ms)
            self.position_slider.blockSignals(False)
            self.time_label.setText(f"{format_timestamp(position_ms)} / {format_timestamp(self.duration_ms)}")
        self._refresh_subtitle_at(position_ms)

    def update_play_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def previous_frame(self):
        self.media_player.pause()
        delta = max(1, int(round(1000.0 / self.fps)))
        new_pos = max(0, self.media_player.position() - delta)
        self.media_player.setPosition(new_pos)
        self._refresh_subtitle_at(new_pos)

    def next_single_frame(self):
        self.media_player.pause()
        delta = max(1, int(round(1000.0 / self.fps)))
        new_pos = min(self.duration_ms, self.media_player.position() + delta)
        self.media_player.setPosition(new_pos)
        self._refresh_subtitle_at(new_pos)

    def save_current_frame(self):
        self.media_player.pause()
        pos_ms = self.media_player.position()
        target_frame = int(round((pos_ms / 1000.0) * self.fps))
        target_frame = max(0, min(target_frame, self.frame_count - 1))

        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        success, frame = cap.read()
        cap.release()

        if not success or frame is None:
            QMessageBox.warning(self, "No Frame Available", "Could not decode the current frame.")
            return

        default_dir = os.path.dirname(self.video_path)
        suggested = os.path.join(default_dir, f"frame_{format_timestamp(pos_ms, '-')}.png")

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Current Frame", suggested,
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg);;TIFF Image (*.tif *.tiff)"
        )
        if not path:
            return

        lower = path.lower()
        if selected_filter.startswith("JPEG") and not lower.endswith((".jpg", ".jpeg")):
            path += ".jpg"
        elif selected_filter.startswith("TIFF") and not lower.endswith((".tif", ".tiff")):
            path += ".tiff"
        elif not lower.endswith(".png"):
            path += ".png"

        try:
            if cv2.imwrite(path, frame):
                QMessageBox.information(self, "Frame Saved", f"Frame saved successfully.\n\n{path}")
            else:
                QMessageBox.critical(self, "Save Failed", "OpenCV could not write the image.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save the frame.\n\n{exc}")

    def closeEvent(self, event):
        self.media_player.stop()
        if hasattr(self, "thumb_worker") and self.thumb_worker.isRunning():
            self.thumb_worker.cancel_requested = True
            self.thumb_worker.wait(1000)
        if self._temp_extracted_srt and os.path.exists(self._temp_extracted_srt):
            try: os.remove(self._temp_extracted_srt)
            except OSError: pass
        event.accept()


class MetadataDialog(QDialog):
    def __init__(self, metadata, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Video Information")
        self.resize(430, 300)

        form = QFormLayout()
        for key, value in metadata.items():
            form.addRow(QLabel(f"<b>{key}</b>"), QLabel(str(value)))

        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(close)
        self.setLayout(layout)


class ContactSheetDialog(QDialog):
    def __init__(self, image_paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Contact Sheet")
        self.resize(900, 650)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        grid = QGridLayout(container)

        columns = 4
        for index, path in enumerate(image_paths):
            label = QLabel()
            pixmap = QPixmap(path).scaled(
                210, 140,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            label.setPixmap(pixmap)
            label.setToolTip(os.path.basename(path))
            grid.addWidget(label, index // columns, index % columns)

        scroll.setWidget(container)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addWidget(scroll)
        layout.addWidget(close)
        self.setLayout(layout)

class SceneResultsDialog(QDialog):
    def __init__(self, scenes, frame_count, fps, parent=None):
        super().__init__(parent)
        self.scenes = scenes
        self.frame_count = frame_count
        self.fps = fps
        self.action = None
        
        self.setWindowTitle(f"Scene Detection - {len(scenes)} Scenes Found")
        self.resize(400, 500)
        
        self.list_widget = QListWidget()
        for i, f in enumerate(scenes):
            ts = format_timestamp(frame_to_ms(f, fps))
            self.list_widget.addItem(f"Scene {i + 1}: Frame {f} ({ts})")
            
        self.extract_keyframes_btn = QPushButton("Extract 1 Keyframe per Scene")
        self.extract_keyframes_btn.clicked.connect(lambda: self.trigger_action("keyframes"))
        
        self.split_clips_btn = QPushButton("Split into Clips")
        self.split_clips_btn.clicked.connect(lambda: self.trigger_action("clips"))
        
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Detected Scenes:"))
        layout.addWidget(self.list_widget)
        layout.addWidget(self.extract_keyframes_btn)
        layout.addWidget(self.split_clips_btn)
        layout.addWidget(self.close_btn)
        self.setLayout(layout)
        
    def trigger_action(self, action_type):
        self.action = action_type
        self.accept()

class FrameExtractionWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, float, bool)
    error = pyqtSignal(str)
    
    def __init__(self, source_file, save_dir, extension, start_frame, end_frame, step, render_filename_cb, unique_path_cb, quality, export_manifest, image_format, filter_blur=False, blur_threshold=100.0, subtitles=None):
        super().__init__()
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.step = step
        self.render_filename_cb = render_filename_cb
        self.unique_path_cb = unique_path_cb
        self.quality = quality
        self.export_manifest = export_manifest
        self.image_format = image_format
        self.filter_blur = filter_blur
        self.blur_threshold = blur_threshold
        self.subtitles = subtitles or []
        self.cancel_requested = False
        
    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("The selected source file could not be opened.")
            return
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        current_frame = self.start_frame
        expected = max(1, int(math.ceil((self.end_frame - self.start_frame) / self.step)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_frame)
        current_pos = self.start_frame
        
        used_names = set()
        extracted = 0
        skipped = 0
        start_clock = time.time()
        
        encode_params = []
        if self.image_format == "JPEG":
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
        elif self.image_format == "PNG":
            encode_params = [cv2.IMWRITE_PNG_COMPRESSION, self.quality]
            
        manifest_data = []
        
        # Bounded concurrency: cap in-flight frame writes to 8 (prevents memory explosion)
        max_in_flight = 8
        sem = threading.Semaphore(max_in_flight)
        write_error = []
        
        def _write_task(path, img):
            try:
                if not cv2.imwrite(path, img, encode_params):
                    write_error.append(f"Could not save: {path}")
            except Exception as exc:
                write_error.append(str(exc))
            finally:
                sem.release()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            while current_frame < self.end_frame and not self.cancel_requested:
                if write_error:
                    self.error.emit(write_error[0])
                    self.cancel_requested = True
                    break
                    
                target_frame = int(round(current_frame))
                
                # Ultra-fast skipping: use hardware-accelerated cap.grab() for delta <= 60
                delta = target_frame - current_pos
                if 0 <= delta <= 60:
                    while current_pos < target_frame:
                        if not cap.grab():
                            break
                        current_pos += 1
                    success, image = cap.read()
                    current_pos += 1
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    success, image = cap.read()
                    current_pos = target_frame + 1
                
                if not success:
                    break
                    
                actual_frame = max(0, target_frame)
                
                if self.filter_blur:
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
                    if variance < self.blur_threshold:
                        skipped += 1
                        current_frame += self.step
                        continue
                        
                timestamp_ms = int(round((actual_frame / fps) * 1000)) if fps else 0
                
                # Fast O(1) monotonic subtitle matching
                if self.subtitles:
                    active_texts = []
                    for s, e, text in self.subtitles:
                        if s > timestamp_ms:
                            break
                        if e >= timestamp_ms:
                            active_texts.append(text)
                    if active_texts:
                        image = draw_subtitle_on_frame(image, "\n".join(active_texts))
                
                filename = self.render_filename_cb(actual_frame, timestamp_ms, self.extension)
                path = self.unique_path_cb(self.save_dir, filename, used_names)
                
                if self.export_manifest:
                    manifest_data.append({
                        "frame_number": actual_frame,
                        "timestamp_ms": timestamp_ms,
                        "formatted_time": format_timestamp(timestamp_ms),
                        "filename": os.path.basename(path)
                    })
                
                # Acquire slot in bounded queue (blocks if 8 writes are already in-flight)
                sem.acquire()
                executor.submit(_write_task, path, image)
                
                extracted += 1
                percent = int(min(100, ((extracted + skipped) / expected) * 100))
                elapsed = time.time() - start_clock
                rate = extracted / elapsed if elapsed > 0 else 0
                
                lbl_blur = f" (Skipped {skipped})" if skipped > 0 else ""
                label = f"Frame {actual_frame:,}  |  {format_timestamp(timestamp_ms)}  |  {rate:.1f} fps{lbl_blur}"
                self.progress.emit(percent, extracted, label)
                current_frame += self.step
            
            # Drain in-flight writes by acquiring all semaphore permits
            for _ in range(max_in_flight):
                sem.acquire()
                    
        cap.release()
        
        if self.export_manifest and manifest_data and not self.cancel_requested:
            csv_path = os.path.join(self.save_dir, "extraction_manifest.csv")
            try:
                with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=["frame_number", "timestamp_ms", "formatted_time", "filename"])
                    writer.writeheader()
                    writer.writerows(manifest_data)
            except Exception as e:
                self.error.emit(f"Could not save manifest: {e}")

        self.finished.emit(extracted, time.time() - start_clock, self.cancel_requested)


class ClipExtractionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)
    
    def __init__(self, cmd, duration_ms, clip_path):
        super().__init__()
        self.cmd = cmd
        self.duration_ms = duration_ms
        self.clip_path = clip_path
        self.cancel_requested = False
        
    def run(self):
        process = None
        ffmpeg_error = ""
        
        try:
            process = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            while process.poll() is None:
                line = process.stdout.readline()
                if line:
                    line = line.strip()
                    if line.startswith("out_time_ms="):
                        try:
                            out_ms = int(line.split("=", 1)[1]) // 1000
                            pct = int(min(99, out_ms * 100 / self.duration_ms))
                            self.progress.emit(pct, f"Extracting: {format_timestamp(out_ms)} / {format_timestamp(self.duration_ms)}")
                        except (ValueError, ZeroDivisionError):
                            pass
                
                if self.cancel_requested:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                    
            process.communicate()
        except Exception as exc:
            ffmpeg_error = str(exc)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
                
        if self.cancel_requested:
            self.finished.emit(False, True, self.clip_path, "Cancelled")
            return
            
        success = (process.returncode == 0 and os.path.isfile(self.clip_path) and os.path.getsize(self.clip_path) > 0)
        self.finished.emit(success, False, self.clip_path, ffmpeg_error)


class MultiSegmentWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, int, list, str)
    
    def __init__(self, tasks):
        """
        tasks: list of dicts:
        [{
            'name': str,
            'cmd': list,
            'duration_ms': int,
            'output_path': str,
            'temp_srt': str or None
        }, ...]
        """
        super().__init__()
        self.tasks = tasks
        self.cancel_requested = False
        self.process = None
        
    def run(self):
        total_tasks = len(self.tasks)
        if total_tasks == 0:
            self.finished.emit(True, 0, [], "No segments to extract.")
            return
            
        extracted_paths = []
        total_duration_ms = sum(t["duration_ms"] for t in self.tasks) or 1
        accumulated_ms = 0
        
        for i, task in enumerate(self.tasks):
            if self.cancel_requested:
                break
                
            cmd = task["cmd"]
            duration_ms = task["duration_ms"]
            out_path = task["output_path"]
            name = task.get("name", f"Segment {i + 1}")
            
            self.progress.emit(
                int((i / total_tasks) * 100),
                f"[{i + 1}/{total_tasks}] {name}..."
            )
            
            try:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )
                while self.process.poll() is None:
                    line = self.process.stdout.readline()
                    if line:
                        line = line.strip()
                        if line.startswith("out_time_ms="):
                            try:
                                seg_out_ms = int(line.split("=", 1)[1]) // 1000
                                current_total_ms = accumulated_ms + seg_out_ms
                                pct = int(min(99, (current_total_ms / total_duration_ms) * 100))
                                self.progress.emit(pct, f"[{i + 1}/{total_tasks}] {name} ({format_timestamp(seg_out_ms)} / {format_timestamp(duration_ms)})")
                            except (ValueError, ZeroDivisionError):
                                pass
                    
                    if self.cancel_requested:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait()
                        break
                        
                self.process.communicate()
            except Exception as exc:
                if self.process is not None and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait()
                self._cleanup_temp_srts()
                self.finished.emit(False, len(extracted_paths), extracted_paths, str(exc))
                return
                
            if task.get("temp_srt") and os.path.exists(task["temp_srt"]):
                try: os.remove(task["temp_srt"])
                except OSError: pass
                
            if self.cancel_requested:
                if os.path.exists(out_path):
                    try: os.remove(out_path)
                    except OSError: pass
                break
                
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                extracted_paths.append(out_path)
                accumulated_ms += duration_ms
            else:
                self._cleanup_temp_srts()
                self.finished.emit(False, len(extracted_paths), extracted_paths, f"Failed to extract segment: {name}")
                return
                
        self._cleanup_temp_srts()
        if self.cancel_requested:
            self.finished.emit(False, len(extracted_paths), extracted_paths, "Cancelled")
        else:
            self.progress.emit(100, f"Completed {len(extracted_paths)} segments!")
            self.finished.emit(True, len(extracted_paths), extracted_paths, f"Successfully extracted {len(extracted_paths)} segments.")

    def _cleanup_temp_srts(self):
        for task in self.tasks:
            if task.get("temp_srt") and os.path.exists(task["temp_srt"]):
                try: os.remove(task["temp_srt"])
                except OSError: pass


class MotionExtractionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, int, str)
    
    def __init__(self, source_file, save_dir, extension, mode, sensitivity, min_area, cooldown_ms, path_cb, format_cb):
        super().__init__()
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.mode = mode
        self.sensitivity = sensitivity
        self.min_area = min_area
        self.cooldown_ms = cooldown_ms
        self.path_cb = path_cb
        self.format_cb = format_cb
        self.cancel_requested = False
        
    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.finished.emit(False, 0, "Could not open source file.")
            return
            
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        var_threshold = max(5, 200 - int(self.sensitivity * 1.95))
        fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=var_threshold, detectShadows=False)
        
        motion_intervals = []
        current_interval_start = None
        last_motion_time = None
        
        extracted_count = 0
        used_names = set()
        
        current_frame = 0
        
        while not self.cancel_requested:
            success, frame = cap.read()
            if not success:
                break
                
            current_ms = int(round((current_frame / fps) * 1000))
            
            small = cv2.resize(frame, (320, 180))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            
            fgmask = fgbg.apply(gray)
            
            fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)[1]
            fgmask = cv2.erode(fgmask, None, iterations=1)
            fgmask = cv2.dilate(fgmask, None, iterations=2)
            
            contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            motion_detected = False
            for contour in contours:
                if cv2.contourArea(contour) > self.min_area:
                    motion_detected = True
                    break
                    
            if motion_detected:
                if current_interval_start is None:
                    current_interval_start = current_ms
                    
                    if self.mode == "Keyframes":
                        filename = self.format_cb(current_frame, current_ms, self.extension)
                        path = self.path_cb(self.save_dir, "motion_" + filename, used_names)
                        cv2.imwrite(path, frame)
                        extracted_count += 1
                        
                last_motion_time = current_ms
            else:
                if current_interval_start is not None and last_motion_time is not None:
                    if (current_ms - last_motion_time) > self.cooldown_ms:
                        if self.mode == "Clips":
                            motion_intervals.append((current_interval_start, current_ms))
                            extracted_count += 1
                        current_interval_start = None
                        last_motion_time = None
            
            current_frame += 1
            if current_frame % 30 == 0:
                pct = int(min(100, (current_frame / max(1, frame_count)) * 100))
                self.progress.emit(pct, f"Scanning: {format_timestamp(current_ms)} / {format_timestamp(int((frame_count/fps)*1000))}")
                
        if current_interval_start is not None and self.mode == "Clips":
             motion_intervals.append((current_interval_start, int(round((frame_count / fps) * 1000))))
             extracted_count += 1
             
        cap.release()
        
        if self.cancel_requested:
            self.finished.emit(False, extracted_count, "Cancelled")
            return
            
        if self.mode == "Clips" and motion_intervals:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self.finished.emit(False, 0, "FFmpeg not found for clip extraction.")
                return
                
            self.progress.emit(100, "Extracting clips...")
            
            for i, (start_ms, end_ms) in enumerate(motion_intervals):
                if self.cancel_requested:
                    break
                    
                start_sec = start_ms / 1000.0
                dur_sec = (end_ms - start_ms) / 1000.0
                
                ext = self.extension.lstrip(".")
                clip_name = f"motion_{i+1}_{format_timestamp(start_ms, '-')}.{ext}"
                out_path = self.path_cb(self.save_dir, clip_name, used_names)
                
                cmd = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{start_sec:.6f}", "-i", self.source_file,
                    "-t", f"{dur_sec:.6f}", "-c:v", "copy", "-c:a", "copy",
                    "-avoid_negative_ts", "make_zero", out_path
                ]
                
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
        self.finished.emit(True, extracted_count, "Success")



class AnimatedExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, bool, str, str)
    
    def __init__(self, source_file, output_path, start_ms, end_ms, output_format,
                 width, fps, dither, quality, loop_count, lossless, subtitle_filter=""):
        super().__init__()
        self.source_file = source_file
        self.output_path = output_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.output_format = output_format
        self.width = width
        self.fps = fps
        self.dither = dither
        self.quality = quality
        self.loop_count = loop_count
        self.lossless = lossless
        self.subtitle_filter = subtitle_filter
        self.cancel_requested = False
        self.process = None
        
    def run(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, False, "", "FFmpeg not found on PATH.")
            return
            
        duration_ms = self.end_ms - self.start_ms
        if duration_ms <= 0:
            self.finished.emit(False, False, "", "Invalid time range.")
            return
            
        start_sec = self.start_ms / 1000.0
        dur_sec = duration_ms / 1000.0
        
        scale_filter = f"scale={self.width}:-1:flags=lanczos" if self.width > 0 else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        
        # Prepend subtitle burn filter if requested
        sub_prefix = f"{self.subtitle_filter}," if self.subtitle_filter else ""
        
        if self.output_format == "GIF":
            self._export_gif(ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix)
        else:
            self._export_webp(ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix)
            
    def _export_gif(self, ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix):
        palette_path = self.output_path + ".palette.png"
        
        # Pass 1: Generate palette
        self.progress.emit(5, "Generating palette...")
        palette_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-vf", f"{sub_prefix}fps={self.fps},{scale_filter},palettegen=stats_mode=diff",
            palette_path
        ]
        
        try:
            result = subprocess.run(palette_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not os.path.isfile(palette_path):
                self.finished.emit(False, False, "", f"Palette generation failed: {result.stderr}")
                return
        except subprocess.TimeoutExpired:
            self.finished.emit(False, False, "", "Palette generation timed out.")
            return
            
        if self.cancel_requested:
            self._cleanup(palette_path)
            self.finished.emit(False, True, "", "Cancelled")
            return
            
        # Pass 2: Render GIF using palette
        self.progress.emit(20, "Rendering GIF...")
        gif_cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-i", palette_path,
            "-lavfi", f"{sub_prefix}fps={self.fps},{scale_filter} [x]; [x][1:v] paletteuse=dither={self.dither}",
            "-loop", str(self.loop_count),
            "-progress", "pipe:1",
            self.output_path
        ]
        
        self._run_ffmpeg_with_progress(gif_cmd, duration_ms, 20)
        self._cleanup(palette_path)
        
    def _export_webp(self, ffmpeg, start_sec, dur_sec, duration_ms, scale_filter, sub_prefix):
        self.progress.emit(5, "Rendering WebP...")
        
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.6f}", "-t", f"{dur_sec:.6f}",
            "-i", self.source_file,
            "-vf", f"{sub_prefix}fps={self.fps},{scale_filter}",
            "-c:v", "libwebp_anim",
            "-quality", str(self.quality),
            "-loop", str(self.loop_count),
            "-an",
            "-progress", "pipe:1",
            self.output_path
        ]
        
        if self.lossless:
            cmd.insert(-1, "-lossless")
            cmd.insert(-1, "1")
            
        self._run_ffmpeg_with_progress(cmd, duration_ms, 5)
        
    def _run_ffmpeg_with_progress(self, cmd, duration_ms, base_pct):
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1
            )
        except Exception as e:
            self.finished.emit(False, False, "", str(e))
            return
            
        while self.process.poll() is None:
            line = self.process.stdout.readline()
            if line:
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        out_us = int(line.split("=", 1)[1])
                        out_ms = out_us // 1000
                        pct = base_pct + int(min(100 - base_pct, (out_ms / max(1, duration_ms)) * (100 - base_pct)))
                        self.progress.emit(pct, f"Encoding: {format_timestamp(out_ms)} / {format_timestamp(duration_ms)}")
                    except (ValueError, ZeroDivisionError):
                        pass
                        
            if self.cancel_requested:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.finished.emit(False, True, "", "Cancelled")
                return
                
        self.process.wait()
        stderr_output = self.process.stderr.read()
        
        if self.process.returncode == 0 and os.path.isfile(self.output_path) and os.path.getsize(self.output_path) > 0:
            size_kb = os.path.getsize(self.output_path) / 1024
            self.finished.emit(True, False, self.output_path, f"Output size: {size_kb:.1f} KB")
        else:
            self.finished.emit(False, False, "", stderr_output or "Export failed.")
            
    def _cleanup(self, *paths):
        for p in paths:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass


class SceneDetectionWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, int, float)
    error = pyqtSignal(str)
    
    def __init__(self, source_file, threshold):
        super().__init__()
        self.source_file = source_file
        self.threshold = threshold
        self.cancel_requested = False
        self.process = None
        
    def run(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("Could not open video.")
            return
            
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = (frame_count / fps) if fps > 0 else 1.0
        cap.release()
        
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            self._run_ffmpeg(ffmpeg, fps, frame_count, duration_sec)
        else:
            self._run_opencv(fps, frame_count)
            
    def _run_ffmpeg(self, ffmpeg, fps, frame_count, duration_sec):
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i", self.source_file,
            "-vf", f"select='gt(scene,{self.threshold})',showinfo",
            "-f", "null",
            "-",
            "-progress", "pipe:2"
        ]
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        except Exception as e:
            self.error.emit(f"Failed to start FFmpeg: {e}")
            return

        scenes = []
        pts_regex = re.compile(r"pts_time:([0-9.]+)")
        
        while not self.cancel_requested:
            line = self.process.stderr.readline()
            if not line and self.process.poll() is not None:
                break
            if not line:
                continue
                
            if "pts_time:" in line:
                match = pts_regex.search(line)
                if match:
                    pts_time = float(match.group(1))
                    frame_num = int(round(pts_time * fps))
                    if not scenes or scenes[-1] != frame_num:
                        scenes.append(frame_num)
            elif line.startswith("out_time_us="):
                us_val = line.split("=", 1)[1].strip()
                if us_val.isdigit() and duration_sec > 0:
                    cur_sec = int(us_val) / 1_000_000.0
                    pct = int(min(99, (cur_sec / duration_sec) * 100))
                    self.progress.emit(pct)
            elif line.startswith("out_time_ms="):
                ms_val = line.split("=", 1)[1].strip()
                if ms_val.isdigit() and duration_sec > 0:
                    cur_sec = (int(ms_val) / 1_000_000.0) if int(ms_val) > 100000 else (int(ms_val) / 1000.0)
                    pct = int(min(99, (cur_sec / duration_sec) * 100))
                    self.progress.emit(pct)

        if self.cancel_requested:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            return
            
        self.process.wait()
        self.finished.emit(scenes, frame_count, fps)

    def _run_opencv(self, fps, frame_count):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.error.emit("Could not open video.")
            return

        previous = None
        scenes = []
        frame_number = 0

        while not self.cancel_requested:
            success, frame = cap.read()
            if not success:
                break

            small = cv2.resize(frame, (160, 90))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if previous is not None:
                difference = cv2.absdiff(previous, gray)
                score = float(difference.mean()) / 255.0

                if score >= self.threshold:
                    scenes.append(frame_number)

            previous = gray
            frame_number += 1

            if frame_number % 50 == 0:
                self.progress.emit(int(frame_number / max(1, frame_count) * 100))

        cap.release()
        if not self.cancel_requested:
            self.finished.emit(scenes, frame_count, fps)

class SceneActionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, action, scenes, source_file, save_dir, extension, format_cb, path_cb, fps, frame_count):
        super().__init__()
        self.action = action
        self.scenes = scenes
        self.source_file = source_file
        self.save_dir = save_dir
        self.extension = extension
        self.format_cb = format_cb
        self.path_cb = path_cb
        self.fps = fps
        self.frame_count = frame_count
        self.cancel_requested = False
        
    def run(self):
        if self.action == "keyframes":
            self._extract_keyframes()
        elif self.action == "clips":
            self._split_clips()
            
    def _extract_keyframes(self):
        cap = cv2.VideoCapture(self.source_file)
        used_names = set()
        
        for i, frame_num in enumerate(self.scenes):
            if self.cancel_requested:
                break
                
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            success, image = cap.read()
            if success:
                ts = int(round((frame_num / self.fps) * 1000)) if self.fps else 0
                filename = self.format_cb(frame_num, ts, self.extension)
                path = self.path_cb(self.save_dir, f"scene_{i+1}_{filename}", used_names)
                cv2.imwrite(path, image)
                
            pct = int((i + 1) / len(self.scenes) * 100)
            self.progress.emit(pct, f"Extracted scene keyframe {i+1} of {len(self.scenes)}")
            
        cap.release()
        self.finished.emit(True, "Keyframes extracted successfully.")
        
    def _split_clips(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, "FFmpeg not found.")
            return
            
        scenes_ms = [int(round((f / self.fps) * 1000)) for f in self.scenes]
        # End at the end of the video
        duration_ms = int(round((self.frame_count / self.fps) * 1000))
        scenes_ms.append(duration_ms)
        
        # Scenes start at 0 if the first scene isn't 0
        if scenes_ms[0] > 0:
            scenes_ms.insert(0, 0)
            
        used_names = set()
        for i in range(len(scenes_ms) - 1):
            if self.cancel_requested:
                break
                
            start_ms = scenes_ms[i]
            end_ms = scenes_ms[i + 1]
            if end_ms <= start_ms:
                continue
                
            start_sec = start_ms / 1000.0
            dur_sec = (end_ms - start_ms) / 1000.0
            
            ext = self.extension.lstrip(".")
            clip_name = f"scene_{i+1}_{format_timestamp(start_ms, '-')}.{ext}"
            out_path = self.path_cb(self.save_dir, clip_name, used_names)
            
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_sec:.6f}", "-i", self.source_file,
                "-t", f"{dur_sec:.6f}", "-c:v", "copy", "-c:a", "copy",
                "-avoid_negative_ts", "make_zero", out_path
            ]
            
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pct = int((i + 1) / (len(scenes_ms) - 1) * 100)
            self.progress.emit(pct, f"Split scene {i+1} of {len(scenes_ms)-1}")
            
        self.finished.emit(True, "Scenes split into clips successfully.")

class OmniExtractStudio(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Throot", "OmniExtractStudio")
        self.extracting = False
        self.cancel_requested = False
        self.batch_queue = []
        self.last_extracted_paths = []
        self.current_metadata = {}
        self.initUI()
        self.load_settings()

        self.setAcceptDrops(True)

    def initUI(self):
        self.setWindowTitle("Throot Omni Extract Studio")
        self.setGeometry(100, 100, 850, 720)
        
        icon_path = get_resource_path("OmniExtract.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.tab_widget = QTabWidget(self)

        self.frame_tab = QWidget()
        self.initFrameTab()
        self.tab_widget.addTab(self.frame_tab, "Extract Frames")

        self.motion_tab = QWidget()
        self.initMotionTab()
        self.tab_widget.addTab(self.motion_tab, "Motion Extraction")

        self.clip_tab = QWidget()
        self.initClipTab()
        self.tab_widget.addTab(self.clip_tab, "Extract Clips")
        
        self.gif_webp_tab = QWidget()
        self.initGifWebpTab()
        self.tab_widget.addTab(self.gif_webp_tab, "GIF / WebP Maker")
        
        self.batch_tab = QWidget()
        self.initBatchTab()
        self.tab_widget.addTab(self.batch_tab, "Batch Queue")

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tab_widget)
        self.setLayout(main_layout)

    # -----------------------------
    # Batch Tab
    # -----------------------------
    
    def initBatchTab(self):
        layout = QVBoxLayout()
        self.batch_list = QListWidget()
        self.batch_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        self.batch_mode_combo = QComboBox()
        self.batch_mode_combo.addItems(["Extract Frames", "Extract Clips (Full Video)"])

        btn_layout = QHBoxLayout()
        self.batch_add_btn = QPushButton("Add Videos")
        self.batch_add_btn.clicked.connect(self.addBatchVideos)
        
        self.batch_remove_btn = QPushButton("Remove Selected")
        self.batch_remove_btn.clicked.connect(self.removeBatchVideos)
        
        self.batch_clear_btn = QPushButton("Clear Queue")
        self.batch_clear_btn.clicked.connect(self.clearBatchVideos)
        
        self.batch_run_btn = QPushButton("Run Batch")
        self.batch_run_btn.clicked.connect(self.processBatch)
        
        btn_layout.addWidget(self.batch_add_btn)
        btn_layout.addWidget(self.batch_remove_btn)
        btn_layout.addWidget(self.batch_clear_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(QLabel("Action:"))
        btn_layout.addWidget(self.batch_mode_combo)
        btn_layout.addWidget(self.batch_run_btn)
        
        layout.addWidget(QLabel("Videos to process:"))
        layout.addWidget(self.batch_list)
        layout.addLayout(btn_layout)
        self.batch_tab.setLayout(layout)

    # -----------------------------
    # Frame tab
    # -----------------------------

    def initFrameTab(self):
        layout = QVBoxLayout()

        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout()

        self.source_file_edit = DropLineEdit(self.load_video_path)
        self.source_file_edit.setPlaceholderText("Drop a video here or Browse...")
        self.source_file_edit.setReadOnly(True)

        source_buttons = QHBoxLayout()
        self.source_file_button = QPushButton("Browse")
        self.source_file_button.clicked.connect(self.selectSourceFile)

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.previewSourceFile)
        self.preview_button.setEnabled(False)

        self.info_button = QPushButton("Video Info")
        self.info_button.clicked.connect(self.showVideoInfo)
        self.info_button.setEnabled(False)

        source_buttons.addWidget(self.source_file_button)
        source_buttons.addWidget(self.preview_button)
        source_buttons.addWidget(self.info_button)

        source_layout.addWidget(self.source_file_edit)
        source_layout.addLayout(source_buttons)
        source_group.setLayout(source_layout)

        output_group = QGroupBox("Output")
        output_layout = QFormLayout()

        self.save_dir_edit = QLineEdit()
        self.save_dir_edit.setReadOnly(True)

        self.save_dir_button = QPushButton("Browse")
        self.save_dir_button.clicked.connect(self.selectSaveDir)

        save_row = QHBoxLayout()
        save_row.addWidget(self.save_dir_edit)
        save_row.addWidget(self.save_dir_button)

        output_layout.addRow("Save Directory:", save_row)

        self.filename_template_edit = QLineEdit("frame_{timestamp}")
        self.filename_template_edit.setToolTip(
            "Available: {frame}, {timestamp}, {milliseconds}, "
            "{hour}, {minute}, {second}"
        )
        output_layout.addRow("Filename Template:", self.filename_template_edit)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG", "JPEG", "TIFF"])
        output_layout.addRow("Image Format:", self.format_combo)

        self.quality_spinbox = QSpinBox()
        self.quality_spinbox.setRange(0, 9)
        self.quality_spinbox.setValue(9)
        
        def update_quality_label(fmt):
            if fmt == "JPEG":
                self.quality_spinbox.setRange(1, 100)
                self.quality_spinbox.setValue(95)
                self.quality_spinbox.setToolTip("JPEG Quality (1-100, higher is better quality)")
            else:
                self.quality_spinbox.setRange(0, 9)
                self.quality_spinbox.setValue(9)
                self.quality_spinbox.setToolTip("PNG Compression (0-9, higher is smaller file)")
                
        self.format_combo.currentTextChanged.connect(update_quality_label)
        update_quality_label(self.format_combo.currentText())
        output_layout.addRow("Quality/Compression:", self.quality_spinbox)

        self.export_manifest_checkbox = QCheckBox("Export Metadata Manifest (CSV)")
        self.export_manifest_checkbox.setChecked(True)
        output_layout.addRow("", self.export_manifest_checkbox)

        output_group.setLayout(output_layout)

        extraction_group = QGroupBox("Extraction")
        extraction_layout = QFormLayout()

        self.extract_part_checkbox = QCheckBox("Extract Part of Video")
        self.extract_part_checkbox.stateChanged.connect(self.toggleTimeFields)
        extraction_layout.addRow(self.extract_part_checkbox)

        self.start_time_label = QLabel("Start Time:")
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setDisplayFormat("HH:mm:ss.zzz")
        self.start_time_edit.setTime(QTime(0, 0, 0, 0))

        self.end_time_label = QLabel("End Time:")
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setDisplayFormat("HH:mm:ss.zzz")
        self.end_time_edit.setTime(QTime(0, 0, 0, 0))

        self.time_fields_widget = QWidget()
        time_layout = QHBoxLayout(self.time_fields_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.addWidget(self.start_time_label)
        time_layout.addWidget(self.start_time_edit)
        time_layout.addWidget(self.end_time_label)
        time_layout.addWidget(self.end_time_edit)
        extraction_layout.addRow(self.time_fields_widget)

        self.extraction_mode_combo = QComboBox()
        self.extraction_mode_combo.addItems([
            "Every Frame",
            "Every 2 Frames",
            "Every 5 Frames",
            "Every 10 Frames",
            "1 FPS",
            "2 FPS",
            "5 FPS",
            "10 FPS",
            "Custom FPS"
        ])
        self.extraction_mode_combo.currentTextChanged.connect(
            self.update_custom_fps_visibility
        )
        extraction_layout.addRow("Extraction Rate:", self.extraction_mode_combo)

        self.custom_fps_spin = QDoubleSpinBox()
        self.custom_fps_spin.setRange(0.001, 240.0)
        self.custom_fps_spin.setDecimals(3)
        self.custom_fps_spin.setValue(1.0)
        self.custom_fps_spin.setSuffix(" FPS")
        self.custom_fps_spin.setVisible(False)
        extraction_layout.addRow("Custom FPS:", self.custom_fps_spin)

        # Blur / Sharpness Filter
        blur_layout = QHBoxLayout()
        self.filter_blur_checkbox = QCheckBox("Filter Blurry Frames")
        self.filter_blur_checkbox.setChecked(False)
        self.blur_threshold_spinbox = QDoubleSpinBox()
        self.blur_threshold_spinbox.setRange(10.0, 2000.0)
        self.blur_threshold_spinbox.setValue(100.0)
        self.blur_threshold_spinbox.setDecimals(1)
        self.blur_threshold_spinbox.setToolTip("Minimum Laplacian variance score. Higher values require sharper frames.")
        self.blur_threshold_spinbox.setEnabled(False)
        
        self.filter_blur_checkbox.toggled.connect(self.blur_threshold_spinbox.setEnabled)
        
        blur_layout.addWidget(self.filter_blur_checkbox)
        blur_layout.addWidget(self.blur_threshold_spinbox)
        blur_layout.addStretch()
        extraction_layout.addRow("Sharpness Check:", blur_layout)

        # Subtitles
        sub_layout = QHBoxLayout()
        self.frame_subtitle_combo = QComboBox()
        self.frame_subtitle_combo.addItem("None")
        self.frame_subtitle_browse_btn = QPushButton("Browse Subtitle...")
        self.frame_subtitle_browse_btn.clicked.connect(self.selectExternalSubtitle)
        sub_layout.addWidget(self.frame_subtitle_combo)
        sub_layout.addWidget(self.frame_subtitle_browse_btn)
        extraction_layout.addRow("Burn Subtitles:", sub_layout)

        extraction_group.setLayout(extraction_layout)

        self.progress_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)

        buttons = QHBoxLayout()

        self.extract_button = QPushButton("Extract Frames")
        self.extract_button.clicked.connect(self.extractFrames)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancelExtraction)
        self.cancel_button.setEnabled(False)

        self.open_output_button = QPushButton("Open Output Folder")
        self.open_output_button.clicked.connect(self.openOutputFolder)
        self.open_output_button.setEnabled(False)

        self.contact_sheet_button = QPushButton("Contact Sheet")
        self.contact_sheet_button.clicked.connect(self.showContactSheet)
        self.contact_sheet_button.setEnabled(False)

        buttons.addWidget(self.extract_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.open_output_button)
        buttons.addWidget(self.contact_sheet_button)

        self.auto_close_checkbox = QCheckBox("Auto Close")
        self.remember_settings_checkbox = QCheckBox("Remember Settings")
        self.remember_settings_checkbox.setChecked(True)

        layout.addWidget(source_group)
        layout.addWidget(output_group)
        layout.addWidget(extraction_group)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(buttons)
        layout.addWidget(self.auto_close_checkbox)
        layout.addWidget(self.remember_settings_checkbox)

        self.frame_tab.setLayout(layout)
        self.toggleTimeFields()
        self.update_custom_fps_visibility(self.extraction_mode_combo.currentText())

    # -----------------------------
    # Motion-Triggered Tab
    # -----------------------------

    def initMotionTab(self):
        layout = QVBoxLayout()
        
        settings_group = QGroupBox("Motion Detection Settings")
        settings_layout = QFormLayout()
        
        self.motion_mode_combo = QComboBox()
        self.motion_mode_combo.addItems(["Keyframes", "Clips"])
        self.motion_mode_combo.setToolTip("Keyframes saves 1 image per motion event. Clips exports the entire motion duration.")
        settings_layout.addRow("Extraction Mode:", self.motion_mode_combo)
        
        self.motion_ext_combo = QComboBox()
        self.motion_ext_combo.addItems([".jpg", ".png"])
        settings_layout.addRow("Extension:", self.motion_ext_combo)
        
        self.motion_sensitivity_spin = QSpinBox()
        self.motion_sensitivity_spin.setRange(0, 100)
        self.motion_sensitivity_spin.setValue(80)
        self.motion_sensitivity_spin.setToolTip("Higher = more sensitive to motion (lower MOG2 threshold).")
        settings_layout.addRow("Sensitivity:", self.motion_sensitivity_spin)
        
        self.motion_min_area_spin = QSpinBox()
        self.motion_min_area_spin.setRange(10, 100000)
        self.motion_min_area_spin.setValue(1000)
        self.motion_min_area_spin.setToolTip("Minimum contour area to trigger motion (filters small noise).")
        settings_layout.addRow("Min Area (px):", self.motion_min_area_spin)
        
        self.motion_cooldown_spin = QSpinBox()
        self.motion_cooldown_spin.setRange(500, 60000)
        self.motion_cooldown_spin.setValue(2000)
        self.motion_cooldown_spin.setSuffix(" ms")
        self.motion_cooldown_spin.setToolTip("Time with no motion before ending a clip event.")
        settings_layout.addRow("Cooldown Time:", self.motion_cooldown_spin)
        
        settings_group.setLayout(settings_layout)
        
        # Progress and buttons
        self.motion_progress_label = QLabel("Ready")
        self.motion_progress_bar = QProgressBar()
        self.motion_progress_bar.setRange(0, 100)
        
        btn_layout = QHBoxLayout()
        self.motion_extract_button = QPushButton("Start Motion Extraction")
        self.motion_extract_button.clicked.connect(self.extractMotion)
        
        self.motion_cancel_button = QPushButton("Cancel")
        self.motion_cancel_button.clicked.connect(self._cancel_motion_extraction)
        self.motion_cancel_button.setEnabled(False)
        
        btn_layout.addWidget(self.motion_extract_button)
        btn_layout.addWidget(self.motion_cancel_button)
        btn_layout.addStretch()
        
        layout.addWidget(settings_group)
        layout.addWidget(self.motion_progress_label)
        layout.addWidget(self.motion_progress_bar)
        layout.addLayout(btn_layout)
        layout.addStretch()
        
        self.motion_tab.setLayout(layout)
        
    def _cancel_motion_extraction(self):
        worker = getattr(self, "motion_worker", None)
        if worker and worker.isRunning():
            worker.cancel_requested = True

    # -----------------------------
    # GIF / WebP Maker Tab
    # -----------------------------

    def initGifWebpTab(self):
        layout = QVBoxLayout()

        # Video Source & Preview Toolbar
        source_group = QGroupBox("Video Source")
        source_layout = QVBoxLayout()
        self.gif_source_file_label = QLabel("Source File: None")
        source_layout.addWidget(self.gif_source_file_label)
        
        source_btns = QHBoxLayout()
        self.gif_source_file_button = QPushButton("Browse")
        self.gif_source_file_button.clicked.connect(self.selectSourceFile)
        self.gif_preview_button = QPushButton("Preview Video")
        self.gif_preview_button.clicked.connect(self.previewSourceFile)
        self.gif_preview_button.setEnabled(False)
        self.gif_info_button = QPushButton("Video Info")
        self.gif_info_button.clicked.connect(self.showVideoInfo)
        self.gif_info_button.setEnabled(False)
        
        source_btns.addWidget(self.gif_source_file_button)
        source_btns.addWidget(self.gif_preview_button)
        source_btns.addWidget(self.gif_info_button)
        source_layout.addLayout(source_btns)
        source_group.setLayout(source_layout)
        layout.addWidget(source_group)

        # Trim range
        trim_group = QGroupBox("Trim Range")
        trim_layout = QFormLayout()

        self.gif_start_time = QTimeEdit()
        self.gif_start_time.setDisplayFormat("HH:mm:ss.zzz")
        self.gif_start_time.setTime(QTime(0, 0, 0, 0))
        trim_layout.addRow("Start Time:", self.gif_start_time)

        self.gif_end_time = QTimeEdit()
        self.gif_end_time.setDisplayFormat("HH:mm:ss.zzz")
        self.gif_end_time.setTime(QTime(0, 0, 10, 0))
        trim_layout.addRow("End Time:", self.gif_end_time)
        
        trim_preview_btn = QPushButton("Open Preview (Interactive Scrub & Set In/Out)")
        trim_preview_btn.clicked.connect(self.previewSourceFile)
        trim_layout.addRow("", trim_preview_btn)

        trim_group.setLayout(trim_layout)

        # Format & output settings
        settings_group = QGroupBox("Output Settings")
        settings_layout = QFormLayout()

        self.gif_format_combo = QComboBox()
        self.gif_format_combo.addItems(["GIF", "Animated WebP"])
        self.gif_format_combo.currentTextChanged.connect(self._update_gif_format_options)
        settings_layout.addRow("Format:", self.gif_format_combo)

        self.gif_resolution_combo = QComboBox()
        self.gif_resolution_combo.addItems(["Original", "1080p", "720p", "480p", "360p", "Custom"])
        self.gif_resolution_combo.currentTextChanged.connect(self._update_gif_resolution)
        settings_layout.addRow("Resolution:", self.gif_resolution_combo)

        self.gif_custom_width_spin = QSpinBox()
        self.gif_custom_width_spin.setRange(32, 7680)
        self.gif_custom_width_spin.setValue(480)
        self.gif_custom_width_spin.setSuffix(" px")
        self.gif_custom_width_spin.setVisible(False)
        settings_layout.addRow("Custom Width:", self.gif_custom_width_spin)

        self.gif_fps_combo = QComboBox()
        self.gif_fps_combo.addItems(["10", "12", "15", "20", "24", "30", "Original"])
        self.gif_fps_combo.setCurrentText("15")
        settings_layout.addRow("Frame Rate:", self.gif_fps_combo)

        # GIF-specific: Dither
        self.gif_dither_combo = QComboBox()
        self.gif_dither_combo.addItems(["sierra2_4a", "bayer", "floyd_steinberg", "none"])
        self.gif_dither_combo.setToolTip("Dithering algorithm for GIF color reduction")
        settings_layout.addRow("Dither Mode:", self.gif_dither_combo)

        # WebP-specific: Quality and Lossless
        self.gif_quality_spin = QSpinBox()
        self.gif_quality_spin.setRange(1, 100)
        self.gif_quality_spin.setValue(80)
        self.gif_quality_spin.setToolTip("WebP compression quality (1-100)")
        self.gif_quality_spin.setVisible(False)
        settings_layout.addRow("WebP Quality:", self.gif_quality_spin)

        self.gif_lossless_checkbox = QCheckBox("Lossless")
        self.gif_lossless_checkbox.setVisible(False)
        settings_layout.addRow("", self.gif_lossless_checkbox)

        self.gif_loop_combo = QComboBox()
        self.gif_loop_combo.addItems(["Infinite Loop", "Play Once", "2", "3", "5"])
        settings_layout.addRow("Loop Count:", self.gif_loop_combo)

        settings_group.setLayout(settings_layout)

        # Progress and buttons
        self.gif_progress_label = QLabel("Ready")
        self.gif_progress_bar = QProgressBar()
        self.gif_progress_bar.setRange(0, 100)

        btn_layout = QHBoxLayout()
        self.gif_export_button = QPushButton("Export Animation")
        self.gif_export_button.clicked.connect(self.exportAnimation)

        self.gif_cancel_button = QPushButton("Cancel")
        self.gif_cancel_button.clicked.connect(self._cancel_gif_export)
        self.gif_cancel_button.setEnabled(False)

        btn_layout.addWidget(self.gif_export_button)
        btn_layout.addWidget(self.gif_cancel_button)
        btn_layout.addStretch()

        layout.addWidget(trim_group)
        layout.addWidget(settings_group)
        
        gif_sub_layout = QHBoxLayout()
        gif_sub_layout.addWidget(QLabel("Burn Subtitles:"))
        self.gif_subtitle_combo = QComboBox()
        self.gif_subtitle_combo.addItem("None")
        self.gif_subtitle_browse_btn = QPushButton("Browse Subtitle...")
        self.gif_subtitle_browse_btn.clicked.connect(self.selectExternalSubtitle)
        gif_sub_layout.addWidget(self.gif_subtitle_combo)
        gif_sub_layout.addWidget(self.gif_subtitle_browse_btn)
        gif_sub_layout.addStretch()
        layout.addLayout(gif_sub_layout)
        layout.addWidget(self.gif_progress_label)
        layout.addWidget(self.gif_progress_bar)
        layout.addLayout(btn_layout)
        layout.addStretch()
        self.gif_webp_tab.setLayout(layout)

    def _update_gif_format_options(self, fmt):
        is_gif = (fmt == "GIF")
        self.gif_dither_combo.setVisible(is_gif)
        # Find the label for dither row
        parent_layout = self.gif_dither_combo.parentWidget().layout()
        if isinstance(parent_layout, QFormLayout):
            label = parent_layout.labelForField(self.gif_dither_combo)
            if label:
                label.setVisible(is_gif)
        self.gif_quality_spin.setVisible(not is_gif)
        self.gif_lossless_checkbox.setVisible(not is_gif)
        if isinstance(parent_layout, QFormLayout):
            label_q = parent_layout.labelForField(self.gif_quality_spin)
            if label_q:
                label_q.setVisible(not is_gif)

    def _update_gif_resolution(self, text):
        self.gif_custom_width_spin.setVisible(text == "Custom")

    def _get_gif_width(self):
        mapping = {"Original": 0, "1080p": 1920, "720p": 1280, "480p": 854, "360p": 640}
        text = self.gif_resolution_combo.currentText()
        if text == "Custom":
            return self.gif_custom_width_spin.value()
        return mapping.get(text, 0)

    def _get_gif_loop_count(self):
        text = self.gif_loop_combo.currentText()
        if text == "Infinite Loop":
            return 0
        if text == "Play Once":
            return 1
        try:
            return int(text)
        except ValueError:
            return 0

    def _cancel_gif_export(self):
        worker = getattr(self, "gif_worker", None)
        if worker and worker.isRunning():
            worker.cancel_requested = True

    # -----------------------------
    # Clip tab
    # -----------------------------

    def initClipTab(self):
        layout = QVBoxLayout()

        self.clip_source_file_label = QLabel("Source File:")
        self.clip_source_file_button = QPushButton("Browse")
        self.clip_source_file_button.clicked.connect(self.selectSourceFile)

        self.clip_save_dir_label = QLabel("Save Directory:")
        self.clip_save_dir_button = QPushButton("Browse")
        self.clip_save_dir_button.clicked.connect(self.selectSaveDir)

        self.clip_time_label = QLabel("Video Length: ")
        self.clip_frames_label = QLabel("Total Frames: ")

        self.clip_start_time_label = QLabel("Start Time:")
        self.clip_start_time_edit = QTimeEdit()
        self.clip_start_time_edit.setDisplayFormat("HH:mm:ss.zzz")

        self.clip_end_time_label = QLabel("End Time:")
        self.clip_end_time_edit = QTimeEdit()
        self.clip_end_time_edit.setDisplayFormat("HH:mm:ss.zzz")

        self.clip_start_frame_label = QLabel("Start Frame:")
        self.clip_start_frame_spinbox = QSpinBox()
        self.clip_start_frame_spinbox.setMaximum(999999999)

        self.clip_end_frame_label = QLabel("End Frame:")
        self.clip_end_frame_spinbox = QSpinBox()
        self.clip_end_frame_spinbox.setMaximum(999999999)

        # Keep the original frame-based clip workflow, but make time/frame
        # fields synchronize in both directions.
        self.clip_start_time_edit.editingFinished.connect(
            self.sync_clip_time_to_frame
        )
        self.clip_end_time_edit.editingFinished.connect(
            self.sync_clip_time_to_frame
        )
        self.clip_start_frame_spinbox.valueChanged.connect(
            self.sync_clip_frame_to_time
        )
        self.clip_end_frame_spinbox.valueChanged.connect(
            self.sync_clip_frame_to_time
        )

        self.clip_format_combo = QComboBox()
        self.clip_format_combo.addItems(["MP4", "AVI", "MKV", "MOV", "FLV", "COPY (Lossless)"])

        self.clip_progress_bar = QProgressBar()

        self.clip_extract_button = QPushButton("Extract Clip")
        self.clip_extract_button.clicked.connect(self.extractClip)

        self.clip_cancel_button = QPushButton("Cancel")
        self.clip_cancel_button.clicked.connect(self.cancelExtraction)
        self.clip_cancel_button.setEnabled(False)

        self.clip_preview_button = QPushButton("Preview")
        self.clip_preview_button.clicked.connect(self.previewSourceFile)
        self.clip_preview_button.setEnabled(False)

        self.clip_info_button = QPushButton("Video Info")
        self.clip_info_button.clicked.connect(self.showVideoInfo)
        self.clip_info_button.setEnabled(False)

        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self.clip_source_file_button)
        source_buttons.addWidget(self.clip_preview_button)
        source_buttons.addWidget(self.clip_info_button)

        layout.addWidget(self.clip_source_file_label)
        layout.addLayout(source_buttons)
        layout.addWidget(self.clip_save_dir_label)
        layout.addWidget(self.clip_save_dir_button)
        layout.addWidget(self.clip_time_label)
        layout.addWidget(self.clip_frames_label)
        
        # Single Trim Range
        trim_group = QGroupBox("Clip Range")
        trim_layout = QGridLayout()
        trim_layout.addWidget(self.clip_start_time_label, 0, 0)
        trim_layout.addWidget(self.clip_start_time_edit, 0, 1)
        trim_layout.addWidget(self.clip_start_frame_label, 0, 2)
        trim_layout.addWidget(self.clip_start_frame_spinbox, 0, 3)
        trim_layout.addWidget(self.clip_end_time_label, 1, 0)
        trim_layout.addWidget(self.clip_end_time_edit, 1, 1)
        trim_layout.addWidget(self.clip_end_frame_label, 1, 2)
        trim_layout.addWidget(self.clip_end_frame_spinbox, 1, 3)
        trim_group.setLayout(trim_layout)
        layout.addWidget(trim_group)
        
        # Multi-Segment / Chapter Cutting
        segment_group = QGroupBox("Multi-Segment / Chapter Cutting")
        segment_layout = QVBoxLayout()
        
        self.segments_table = QTableWidget(0, 4)
        self.segments_table.setHorizontalHeaderLabels(["Name / Chapter", "Start Time", "End Time", "Duration"])
        self.segments_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.segments_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.segments_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.segments_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.segments_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segments_table.setAlternatingRowColors(True)
        self.segments_table.cellClicked.connect(self.onSegmentTableRowClicked)
        segment_layout.addWidget(self.segments_table)
        
        seg_btn_layout = QHBoxLayout()
        self.add_segment_btn = QPushButton("+ Add Current Range")
        self.add_segment_btn.clicked.connect(self.addCurrentRangeToSegments)
        self.import_chapters_btn = QPushButton("Import Chapters")
        self.import_chapters_btn.clicked.connect(self.importChaptersToSegments)
        self.remove_segment_btn = QPushButton("Remove")
        self.remove_segment_btn.clicked.connect(self.removeSelectedSegment)
        self.clear_segments_btn = QPushButton("Clear All")
        self.clear_segments_btn.clicked.connect(self.clearAllSegments)
        
        seg_btn_layout.addWidget(self.add_segment_btn)
        seg_btn_layout.addWidget(self.import_chapters_btn)
        seg_btn_layout.addWidget(self.remove_segment_btn)
        seg_btn_layout.addWidget(self.clear_segments_btn)
        segment_layout.addLayout(seg_btn_layout)
        segment_group.setLayout(segment_layout)
        layout.addWidget(segment_group)
        
        # Format & Output Options
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("Clip Format:"))
        format_layout.addWidget(self.clip_format_combo)
        format_layout.addWidget(QLabel("HW Accel:"))
        
        self.clip_hw_accel_combo = QComboBox()
        self.clip_hw_accel_combo.addItems(["Auto", "CPU (libx264)", "NVIDIA (h264_nvenc)", "Intel (h264_qsv)", "macOS (h264_videotoolbox)"])
        format_layout.addWidget(self.clip_hw_accel_combo)
        layout.addLayout(format_layout)
        
        # Subtitles
        sub_layout = QHBoxLayout()
        sub_layout.addWidget(QLabel("Subtitles:"))
        self.clip_subtitle_combo = QComboBox()
        self.clip_subtitle_combo.addItem("None")
        sub_layout.addWidget(self.clip_subtitle_combo)
        
        self.clip_subtitle_action = QComboBox()
        self.clip_subtitle_action.addItems(["Burn into Video", "Rip to .srt"])
        self.clip_subtitle_action.setEnabled(False)
        sub_layout.addWidget(self.clip_subtitle_action)
        
        self.clip_subtitle_browse_btn = QPushButton("Browse Subtitle...")
        self.clip_subtitle_browse_btn.clicked.connect(self.selectExternalSubtitle)
        sub_layout.addWidget(self.clip_subtitle_browse_btn)
        layout.addLayout(sub_layout)
        
        layout.addWidget(self.clip_progress_bar)

        clip_buttons = QHBoxLayout()
        clip_buttons.addWidget(self.clip_extract_button)
        self.extract_segments_btn = QPushButton("Extract All Segments")
        self.extract_segments_btn.clicked.connect(self.extractAllSegments)
        clip_buttons.addWidget(self.extract_segments_btn)
        clip_buttons.addWidget(self.clip_cancel_button)
        layout.addLayout(clip_buttons)

        self.clip_tab.setLayout(layout)

    # -----------------------------
    # General video handling
    # -----------------------------

    def load_video_path(self, path):
        if not path or not os.path.isfile(path):
            return
        self.source_file = path
        self.source_file_edit.setText(path)
        self.source_file_label_update()
        self.update_video_metadata()

    def source_file_label_update(self):
        if hasattr(self, "clip_source_file_label"):
            self.clip_source_file_label.setText(
                f"Source File: {self.source_file}"
            )
        if hasattr(self, "gif_source_file_label"):
            self.gif_source_file_label.setText(
                f"Source File: {os.path.basename(self.source_file)}"
            )

    def selectSourceFile(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Select Source File",
            self.settings.value("last_source_dir", ""),
            VIDEO_FILTER,
            options=QFileDialog.Option.ReadOnly
        )
        if file_name:
            self.settings.setValue(
                "last_source_dir", os.path.dirname(file_name)
            )
            self.load_video_path(file_name)

    def selectSaveDir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Save Directory",
            self.settings.value("last_output_dir", "")
        )
        if not directory:
            return
        self.save_dir = directory
        self.save_dir_edit.setText(directory)
        self.settings.setValue("last_output_dir", directory)
        self.clip_save_dir_label.setText(f"Save Directory: {directory}")

    def update_video_metadata(self):
        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            self.preview_button.setEnabled(False)
            self.info_button.setEnabled(False)
            self.clip_preview_button.setEnabled(False)
            self.clip_info_button.setEnabled(False)
            if hasattr(self, "gif_preview_button"):
                self.gif_preview_button.setEnabled(False)
            if hasattr(self, "gif_info_button"):
                self.gif_info_button.setEnabled(False)
            QMessageBox.warning(
                self, "Unable to Open Video",
                "The selected file could not be opened as a video."
            )
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        codec_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(
            chr((codec_int >> (8 * i)) & 0xFF) for i in range(4)
        ).strip("\x00")

        duration_ms = int((frame_count / fps) * 1000) if fps > 0 else 0
        cap.release()

        self.current_metadata = {
            "File": os.path.basename(self.source_file),
            "Resolution": f"{width} × {height}",
            "FPS": f"{fps:.3f}" if fps else "Unknown",
            "Total Frames": f"{frame_count:,}",
            "Duration": duration_text(duration_ms),
            "Codec": codec or "Unknown",
            "File Size": self.format_file_size(
                os.path.getsize(self.source_file)
            )
        }

        self.preview_button.setEnabled(True)
        self.info_button.setEnabled(True)
        self.clip_preview_button.setEnabled(True)
        self.clip_info_button.setEnabled(True)
        if hasattr(self, "gif_preview_button"):
            self.gif_preview_button.setEnabled(True)
        if hasattr(self, "gif_info_button"):
            self.gif_info_button.setEnabled(True)
        if hasattr(self, "gif_source_file_label"):
            self.gif_source_file_label.setText(f"Source File: {os.path.basename(self.source_file)}")

        self.start_time_edit.setTime(QTime(0, 0, 0, 0))
        self.end_time_edit.setTime(ms_to_qtime(duration_ms))
        self.clip_start_time_edit.setTime(QTime(0, 0, 0, 0))
        self.clip_end_time_edit.setTime(ms_to_qtime(duration_ms))
        if hasattr(self, "gif_start_time"):
            self.gif_start_time.setTime(QTime(0, 0, 0, 0))
        if hasattr(self, "gif_end_time"):
            self.gif_end_time.setTime(ms_to_qtime(min(duration_ms, 10000)))

        self.clip_time_label.setText(
            f"Video Length: {duration_text(duration_ms)}"
        )
        self.clip_frames_label.setText(
            f"Total Frames: {frame_count:,}"
        )
        self.clip_end_frame_spinbox.setValue(frame_count)
        
        self._probe_subtitle_tracks()

    @staticmethod
    def format_file_size(size):
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.2f} {unit}"
            value /= 1024

    def selectExternalSubtitle(self):
        """Allow user to browse and select an external .srt or .vtt subtitle file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select External Subtitle File",
            os.path.dirname(getattr(self, "source_file", "")) or self.settings.value("last_source_dir", ""),
            "Subtitle Files (*.srt *.vtt);;All Files (*)"
        )
        if file_path:
            self.external_subtitle_file = file_path
            self._probe_subtitle_tracks(selected_external=file_path)

    def _probe_subtitle_tracks(self, selected_external=None):
        """Use ffprobe to detect embedded subtitle streams and check for external .srt/.vtt files."""
        self.subtitle_tracks = []
        
        if not hasattr(self, "external_subtitle_file"):
            self.external_subtitle_file = ""
            
        if selected_external:
            self.external_subtitle_file = selected_external
            
        # 1. Check for external subtitle files alongside source video
        if hasattr(self, "source_file") and self.source_file:
            base_dir = os.path.dirname(self.source_file)
            base_name = os.path.splitext(os.path.basename(self.source_file))[0]
            
            # Check for name.srt, name.vtt, name.en.srt, etc.
            auto_detected_subs = []
            for ext in (".srt", ".vtt", ".en.srt", ".eng.srt", ".en.vtt"):
                candidate = os.path.join(base_dir, base_name + ext)
                if os.path.isfile(candidate) and candidate != self.external_subtitle_file:
                    auto_detected_subs.append(candidate)
                    
            if not self.external_subtitle_file and auto_detected_subs:
                self.external_subtitle_file = auto_detected_subs[0]
                
        # 2. Add user-selected or auto-detected external subtitle
        if self.external_subtitle_file and os.path.isfile(self.external_subtitle_file):
            self.subtitle_tracks.append({
                "type": "external",
                "path": self.external_subtitle_file,
                "label": f"External: {os.path.basename(self.external_subtitle_file)}"
            })
            
        # 3. Probe embedded subtitle streams via ffprobe
        ffprobe = shutil.which("ffprobe")
        if ffprobe and hasattr(self, "source_file") and self.source_file:
            try:
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "s",
                     "-show_entries", "stream=index,codec_name:stream_tags=language,title",
                     "-of", "json", self.source_file],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10
                )
                if result.returncode == 0:
                    import json
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
                        
                        self.subtitle_tracks.append({
                            "type": "embedded",
                            "index": idx,
                            "sub_index": i,
                            "codec": codec,
                            "language": lang,
                            "title": title,
                            "label": label
                        })
            except (subprocess.TimeoutExpired, Exception):
                pass
                
        self._populate_subtitle_combos(select_external=bool(self.external_subtitle_file))
        
    def _populate_subtitle_combos(self, select_external=False):
        """Fill all subtitle track combo boxes across all tabs with probed and external tracks."""
        combos = []
        if hasattr(self, "frame_subtitle_combo"):
            combos.append(self.frame_subtitle_combo)
        if hasattr(self, "clip_subtitle_combo"):
            combos.append(self.clip_subtitle_combo)
        if hasattr(self, "gif_subtitle_combo"):
            combos.append(self.gif_subtitle_combo)
            
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None")
            for track in self.subtitle_tracks:
                combo.addItem(track["label"])
            combo.addItem("<Browse External Subtitle (.srt, .vtt)...>")
            
            # If external subtitle is loaded, default select it, otherwise None
            if select_external and self.subtitle_tracks and self.subtitle_tracks[0].get("type") == "external":
                combo.setCurrentIndex(1)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
            
            # Reconnect activation
            try:
                combo.activated.disconnect()
            except TypeError:
                pass
            combo.activated.connect(self._on_subtitle_combo_activated)

    def _on_subtitle_combo_activated(self, index):
        sender = self.sender()
        if not sender:
            return
        # If user picked "<Browse External Subtitle...>"
        if index == sender.count() - 1:
            self.selectExternalSubtitle()
        elif hasattr(self, "clip_subtitle_action") and sender == getattr(self, "clip_subtitle_combo", None):
            self.clip_subtitle_action.setEnabled(index > 0 and index < sender.count() - 1)

    def previewSourceFile(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(
                self, "No Source File",
                "Please select a source video first."
            )
            return
            
        def handle_in(ms):
            q_time = ms_to_qtime(ms)
            if self.tab_widget.currentWidget() == self.frame_tab:
                self.extract_part_checkbox.setChecked(True)
                self.start_time_edit.setTime(q_time)
            elif self.tab_widget.currentWidget() == self.clip_tab:
                self.clip_start_time_edit.setTime(q_time)
                self.sync_clip_time_to_frame()
            elif hasattr(self, "gif_webp_tab") and self.tab_widget.currentWidget() == self.gif_webp_tab:
                self.gif_start_time.setTime(q_time)
                
        def handle_out(ms):
            q_time = ms_to_qtime(ms)
            if self.tab_widget.currentWidget() == self.frame_tab:
                self.extract_part_checkbox.setChecked(True)
                self.end_time_edit.setTime(q_time)
            elif self.tab_widget.currentWidget() == self.clip_tab:
                self.clip_end_time_edit.setTime(q_time)
                self.sync_clip_time_to_frame()
            elif hasattr(self, "gif_webp_tab") and self.tab_widget.currentWidget() == self.gif_webp_tab:
                self.gif_end_time.setTime(q_time)
                
        # Determine active subtitle index from current tab
        active_sub_idx = 0
        if self.tab_widget.currentWidget() == self.clip_tab and hasattr(self, "clip_subtitle_combo"):
            active_sub_idx = self.clip_subtitle_combo.currentIndex()
        elif hasattr(self, "gif_webp_tab") and self.tab_widget.currentWidget() == self.gif_webp_tab and hasattr(self, "gif_subtitle_combo"):
            active_sub_idx = self.gif_subtitle_combo.currentIndex()
        elif self.tab_widget.currentWidget() == self.frame_tab and hasattr(self, "frame_subtitle_combo"):
            active_sub_idx = self.frame_subtitle_combo.currentIndex()

        preview = VideoPreviewDialog(
            self.source_file,
            subtitle_tracks=getattr(self, "subtitle_tracks", []),
            initial_sub_index=active_sub_idx,
            parent=self
        )
        preview.in_selected.connect(handle_in)
        preview.out_selected.connect(handle_out)
        preview.exec()

    def showVideoInfo(self):
        if self.current_metadata:
            MetadataDialog(self.current_metadata, self).exec()

    # -----------------------------
    # Settings / drag & drop
    # -----------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load_video_path(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()

    def load_settings(self):
        self.resize(
            self.settings.value("window_width", 850, type=int),
            self.settings.value("window_height", 720, type=int)
        )

        if self.settings.value("remember_settings", "true") == "true":
            self.filename_template_edit.setText(
                self.settings.value("filename_template", "frame_{timestamp}")
            )
            self.format_combo.setCurrentText(
                self.settings.value("image_format", "PNG")
            )
            self.extraction_mode_combo.setCurrentText(
                self.settings.value("extraction_mode", "Every Frame")
            )
            self.auto_close_checkbox.setChecked(
                self.settings.value("auto_close", "false") == "true"
            )

    def save_settings(self):
        if not self.remember_settings_checkbox.isChecked():
            return
        self.settings.setValue("window_width", self.width())
        self.settings.setValue("window_height", self.height())
        self.settings.setValue("filename_template",
                               self.filename_template_edit.text())
        self.settings.setValue("image_format",
                               self.format_combo.currentText())
        self.settings.setValue("extraction_mode",
                               self.extraction_mode_combo.currentText())
        self.settings.setValue("auto_close",
                               str(self.auto_close_checkbox.isChecked()).lower())
        self.settings.setValue("remember_settings", "true")

    def closeEvent(self, event):
        self.cancel_requested = True
        self.extracting = False
        
        for worker_attr in ("frame_worker", "clip_worker", "scene_worker", "scene_action_worker", "gif_worker", "motion_worker", "multi_segment_worker"):
            worker = getattr(self, worker_attr, None)
            if worker and worker.isRunning():
                worker.cancel_requested = True
                worker.wait(2000)
                
        self.save_settings()
        event.accept()

    # -----------------------------
    # Frame extraction controls
    # -----------------------------

    def toggleTimeFields(self):
        enabled = self.extract_part_checkbox.isChecked()
        self.time_fields_widget.setVisible(enabled)
        self.time_fields_widget.setEnabled(enabled)

    def update_custom_fps_visibility(self, text):
        self.custom_fps_spin.setVisible(text == "Custom FPS")

    def get_extraction_step(self, source_fps):
        mode = self.extraction_mode_combo.currentText()

        if mode == "Every Frame":
            return 1
        if mode == "Every 2 Frames":
            return 2
        if mode == "Every 5 Frames":
            return 5
        if mode == "Every 10 Frames":
            return 10

        if mode.endswith(" FPS"):
            target_fps = float(mode.split()[0])
        else:
            target_fps = self.custom_fps_spin.value()

        if target_fps <= 0:
            return 1

        return max(1, source_fps / target_fps)

    def render_filename(self, frame_number, timestamp_ms, extension):
        timestamp_file = format_timestamp(timestamp_ms, "-")

        template = self.filename_template_edit.text().strip()
        if not template:
            template = "frame_{timestamp}"

        values = {
            "frame": str(frame_number),
            "timestamp": timestamp_file,
            "milliseconds": str(timestamp_ms),
            "hour": f"{timestamp_ms // 3_600_000:02d}",
            "minute": f"{(timestamp_ms // 60_000) % 60:02d}",
            "second": f"{(timestamp_ms // 1_000) % 60:02d}"
        }

        try:
            name = template.format(**values)
        except (KeyError, ValueError):
            name = f"frame_{timestamp_file}"

        # Keep the useful timestamp fallback available while allowing custom
        # templates. Timestamp itself uses filesystem-safe hyphens.
        name = "".join(
            c if c not in '<>:"/\\|?*' else "_"
            for c in name
        ).strip()

        if not name:
            name = f"frame_{timestamp_file}"

        if not name.lower().endswith(extension.lower()):
            name += extension

        return name

    def unique_path(self, directory, filename, used):
        stem, extension = os.path.splitext(filename)
        candidate = filename
        counter = 1

        while candidate in used or os.path.exists(
            os.path.join(directory, candidate)
        ):
            candidate = f"{stem}_{counter:03d}{extension}"
            counter += 1

        used.add(candidate)
        return os.path.join(directory, candidate)

    def cancelExtraction(self):
        if self.extracting:
            self.cancel_requested = True
            for worker_attr in ("frame_worker", "clip_worker", "scene_worker", "scene_action_worker", "gif_worker", "motion_worker", "multi_segment_worker"):
                worker = getattr(self, worker_attr, None)
                if worker and worker.isRunning():
                    worker.cancel_requested = True
            self.progress_label.setText("Cancelling...")

    def set_extracting_state(self, active):
        self.extracting = active
        self.cancel_requested = False
        self.extract_button.setEnabled(not active)
        self.cancel_button.setEnabled(active)
        self.clip_extract_button.setEnabled(not active)
        self.clip_cancel_button.setEnabled(active)
        if hasattr(self, "extract_segments_btn"):
            self.extract_segments_btn.setEnabled(not active)
        self.source_file_button.setEnabled(not active)
        self.save_dir_button.setEnabled(not active)

    def extractFrames(self):
        if not hasattr(self, "source_file") or not hasattr(self, "save_dir"):
            QMessageBox.warning(
                self, "Missing Information",
                "Please select a source file and save directory first."
            )
            return

        format_mapping = {
            "PNG": ".png",
            "JPEG": ".jpg",
            "TIFF": ".tiff"
        }
        extension = format_mapping.get(self.format_combo.currentText(), ".png")

        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            QMessageBox.warning(
                self, "Unable to Open Video",
                "The selected source file could not be opened."
            )
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if not fps or fps <= 0 or frame_count <= 0:
            QMessageBox.warning(
                self, "Invalid Video",
                "Could not determine the video's frame rate or frame count."
            )
            return

        start_frame = 0
        end_frame = frame_count

        if self.extract_part_checkbox.isChecked():
            start_ms = qtime_to_ms(self.start_time_edit.time())
            end_ms = qtime_to_ms(self.end_time_edit.time())

            if end_ms <= start_ms:
                QMessageBox.warning(
                    self, "Invalid Time Range",
                    "End Time must be greater than Start Time."
                )
                return

            start_frame = max(0, min(frame_count - 1, int(start_ms * fps / 1000)))
            end_frame = min(frame_count, max(start_frame + 1, int(end_ms * fps / 1000)))

        step = self.get_extraction_step(fps)

        # Resolve Subtitle Burn for Frames
        subtitles_data = []
        sub_idx = getattr(self, "frame_subtitle_combo", None).currentIndex() if hasattr(self, "frame_subtitle_combo") else 0
        if sub_idx > 0 and sub_idx <= len(getattr(self, "subtitle_tracks", [])):
            track = self.subtitle_tracks[sub_idx - 1]
            if track.get("type") == "external":
                subtitles_data = parse_subtitles(track["path"])
            elif track.get("type") == "embedded":
                temp_srt = extract_subtitles_to_temp(self.source_file, track["sub_index"])
                if temp_srt:
                    subtitles_data = parse_subtitles(temp_srt)
                    try:
                        os.remove(temp_srt)
                    except OSError:
                        pass

        self.set_extracting_state(True)
        self.progress_bar.setValue(0)
        self.last_extracted_paths = []

        self.frame_worker = FrameExtractionWorker(
            self.source_file, self.save_dir, extension, 
            start_frame, end_frame, step, 
            self.render_filename, self.unique_path,
            self.quality_spinbox.value(),
            self.export_manifest_checkbox.isChecked(),
            self.format_combo.currentText(),
            self.filter_blur_checkbox.isChecked(),
            self.blur_threshold_spinbox.value(),
            subtitles=subtitles_data
        )
        
        self.frame_worker.progress.connect(
            lambda pct, ext, lbl: self._update_frame_progress(pct, lbl)
        )
        self.frame_worker.error.connect(
            lambda msg: QMessageBox.warning(self, "Extraction Error", msg)
        )
        self.frame_worker.finished.connect(self._on_frame_extraction_finished)
        self.frame_worker.start()

    def _update_frame_progress(self, percent, label):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(label)
        
    def _on_frame_extraction_finished(self, extracted, elapsed, cancelled):
        self.set_extracting_state(False)
        self.open_output_button.setEnabled(True)
        self.contact_sheet_button.setEnabled(True)
        
        if hasattr(self, "frame_worker") and self.frame_worker is not None:
            self.frame_worker.deleteLater()
            self.frame_worker = None
            

        if cancelled:
            self.progress_label.setText(f"Cancelled — {extracted:,} frames saved.")
            QMessageBox.information(
                self, "Extraction Cancelled",
                f"Extraction cancelled.\n\n{extracted:,} frames were saved before cancellation."
            )
            return
            
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"Complete — {extracted:,} frames in {elapsed:.2f}s.")
        
        if getattr(self, "in_batch_mode", False):
            return
        
        if self.auto_close_checkbox.isChecked():
            self.close()
            return
            
        QMessageBox.information(
            self, "Extraction Complete",
            f"Done!\n\nSuccessfully extracted {extracted:,} images.\nTime: {elapsed:.2f} seconds."
        )

    # -----------------------------
    # Clip extraction
    # -----------------------------

    def sync_clip_time_to_frame(self):
        if not self.current_metadata:
            return
        fps = float(self.current_metadata.get("FPS", 0))
        if fps <= 0:
            return

        self.clip_start_frame_spinbox.blockSignals(True)
        self.clip_end_frame_spinbox.blockSignals(True)

        self.clip_start_frame_spinbox.setValue(
            int(qtime_to_ms(self.clip_start_time_edit.time()) * fps / 1000)
        )
        self.clip_end_frame_spinbox.setValue(
            int(qtime_to_ms(self.clip_end_time_edit.time()) * fps / 1000)
        )

        self.clip_start_frame_spinbox.blockSignals(False)
        self.clip_end_frame_spinbox.blockSignals(False)

    def sync_clip_frame_to_time(self):
        if not self.current_metadata:
            return
        fps = float(self.current_metadata.get("FPS", 0))
        if fps <= 0:
            return

        self.clip_start_time_edit.blockSignals(True)
        self.clip_end_time_edit.blockSignals(True)

        self.clip_start_time_edit.setTime(
            ms_to_qtime(
                frame_to_ms(self.clip_start_frame_spinbox.value(), fps)
            )
        )
        self.clip_end_time_edit.setTime(
            ms_to_qtime(
                frame_to_ms(self.clip_end_frame_spinbox.value(), fps)
            )
        )

        self.clip_start_time_edit.blockSignals(False)
        self.clip_end_time_edit.blockSignals(False)

    def extractClip(self):
        """Extract the selected range with FFmpeg and explicitly include source audio."""
        if not hasattr(self, "source_file") or not hasattr(self, "save_dir"):
            QMessageBox.warning(self, "Missing Information",
                                "Please select a source file and save directory first.")
            return

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            QMessageBox.critical(
                self, "FFmpeg/FFprobe Not Found",
                "FFmpeg and FFprobe are required for clip extraction.\n\n"
                "Check with: ffmpeg -version && ffprobe -version"
            )
            return

        cap = cv2.VideoCapture(self.source_file)
        if not cap.isOpened():
            QMessageBox.warning(self, "Unable to Open Video",
                                "The selected source file could not be opened.")
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        start_frame = self.clip_start_frame_spinbox.value()
        end_frame = self.clip_end_frame_spinbox.value()
        if not fps or fps <= 0 or end_frame <= start_frame:
            QMessageBox.warning(self, "Invalid Range",
                                "Please choose a valid start/end frame range.")
            return

        if frame_count > 0:
            start_frame = max(0, min(start_frame, frame_count - 1))
            end_frame = max(start_frame + 1, min(end_frame, frame_count))
        if end_frame <= start_frame:
            QMessageBox.warning(self, "Invalid Range", "The selected range is outside the video's frame range.")
            return

        selected_format = self.clip_format_combo.currentText().upper()
        start_ms = frame_to_ms(start_frame, fps)
        end_ms = frame_to_ms(end_frame, fps)
        duration_ms = max(1, end_ms - start_ms)
        start_seconds = start_ms / 1000.0
        duration_seconds = duration_ms / 1000.0

        ext = "mp4" if selected_format == "COPY (LOSSLESS)" else selected_format.lower()
        clip_name = f"clip_{format_timestamp(start_ms, '-')}_to_{format_timestamp(end_ms, '-')}.{ext}"
        clip_path = self.unique_path(self.save_dir, clip_name, set())

        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index,codec_name,sample_rate,channels",
             "-of", "default=noprint_wrappers=1", self.source_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        source_audio_info = probe.stdout.strip()
        has_audio = bool(source_audio_info)

        hw_accel = self.clip_hw_accel_combo.currentText()
        cv_codec = "libx264"
        if hw_accel.startswith("NVIDIA"):
            cv_codec = "h264_nvenc"
        elif hw_accel.startswith("Intel"):
            cv_codec = "h264_qsv"
        elif hw_accel.startswith("macOS"):
            cv_codec = "h264_videotoolbox"

        if selected_format == "COPY (LOSSLESS)":
            video_args = ["-c:v", "copy"]
            audio_args = ["-c:a", "copy"]
            mux_args = []
        elif selected_format == "AVI":
            video_args = ["-c:v", "mpeg4", "-q:v", "3"]
            audio_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = []
        else:
            video_args = ["-c:v", cv_codec]
            if cv_codec == "libx264":
                video_args += ["-preset", "medium", "-crf", "18"]
            elif cv_codec == "h264_nvenc":
                video_args += ["-preset", "p4", "-tune", "hq", "-cq", "19"]
            elif cv_codec == "h264_videotoolbox":
                video_args += ["-q:v", "65"]
                
            video_args += ["-pix_fmt", "yuv420p"]
            audio_args = ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = ["-movflags", "+faststart"] if selected_format in ("MP4", "MOV") else []

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", f"{start_seconds:.6f}",
               "-i", self.source_file,
               "-t", f"{duration_seconds:.6f}",
               "-map", "0:v:0"]

        if has_audio:
            cmd += ["-map", "0:a:0"] + audio_args

        cmd += video_args + mux_args
        
        # Subtitle handling
        sub_idx = self.clip_subtitle_combo.currentIndex()
        sub_action = self.clip_subtitle_action.currentText() if sub_idx > 0 else None
        sub_track = self.subtitle_tracks[sub_idx - 1] if sub_idx > 0 and sub_idx <= len(getattr(self, "subtitle_tracks", [])) else None
        
        self._clip_temp_srt = None
        if sub_track and sub_action == "Burn into Video":
            subs_list = []
            if sub_track.get("type") == "external":
                subs_list = parse_subtitles(sub_track["path"])
            else:
                si = sub_track["sub_index"]
                temp_ext = extract_subtitles_to_temp(self.source_file, si)
                if temp_ext:
                    subs_list = parse_subtitles(temp_ext)
                    try:
                        os.remove(temp_ext)
                    except OSError:
                        pass
                        
            shifted_srt = write_shifted_srt(subs_list, start_ms, end_ms)
            if shifted_srt:
                self._clip_temp_srt = shifted_srt
                escaped = shifted_srt.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                sub_filter = f"subtitles='{escaped}'"
                
                cmd.insert(cmd.index("-map") if "-map" in cmd else len(cmd), "-vf")
                vf_idx = cmd.index("-vf")
                cmd.insert(vf_idx + 1, sub_filter)
                
                # Burning subtitles requires re-encoding, so remove -c:v copy if present
                if "-c:v" in cmd:
                    cv_idx = cmd.index("-c:v")
                    if cmd[cv_idx + 1] == "copy":
                        cmd[cv_idx + 1] = "libx264"
                        cmd.insert(cv_idx + 2, "-crf")
                        cmd.insert(cv_idx + 3, "18")
        
        cmd += ["-avoid_negative_ts", "make_zero", "-shortest",
                "-progress", "pipe:1", "-nostats", clip_path]

        # Rip subtitles to .srt as a sidecar file
        if sub_track and sub_action == "Rip to .srt":
            if sub_track.get("type") == "external":
                ext = os.path.splitext(sub_track["path"])[1]
                target_srt = os.path.splitext(clip_path)[0] + ext
                try:
                    shutil.copyfile(sub_track["path"], target_srt)
                except Exception:
                    pass
            else:
                si = sub_track["sub_index"]
                srt_path = os.path.splitext(clip_path)[0] + f".{sub_track.get('language', 'und')}.srt"
                srt_cmd = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{start_seconds:.6f}", "-i", self.source_file,
                    "-t", f"{duration_seconds:.6f}",
                    "-map", f"0:s:{si}", "-c:s", "srt", srt_path
                ]
                try:
                    subprocess.run(srt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                except (subprocess.TimeoutExpired, Exception):
                    pass

        self.set_extracting_state(True)
        self.clip_progress_bar.setValue(0)
        self.progress_label.setText("Extracting video" + (" + audio" if has_audio else "") + " with FFmpeg...")
        
        self.clip_worker = ClipExtractionWorker(cmd, duration_ms, clip_path)
        self.clip_worker.progress.connect(
            lambda pct, lbl: self._update_clip_progress(pct, lbl)
        )
        self.clip_worker.finished.connect(
            lambda s, c, p, e: self._on_clip_extraction_finished(s, c, p, e, duration_ms, has_audio, ffprobe, source_audio_info)
        )
        self.clip_worker.start()

    def _update_clip_progress(self, percent, label):
        self.clip_progress_bar.setValue(percent)
        self.progress_label.setText(label)
        
    def _on_clip_extraction_finished(self, success, cancelled, clip_path, error_msg, duration_ms, has_audio, ffprobe, source_audio_info):
        self.set_extracting_state(False)
        
        if getattr(self, "_clip_temp_srt", None) and os.path.exists(self._clip_temp_srt):
            try: os.remove(self._clip_temp_srt)
            except OSError: pass
            self._clip_temp_srt = None
            
        if hasattr(self, "clip_worker") and self.clip_worker is not None:
            self.clip_worker.deleteLater()
            self.clip_worker = None
            

        if cancelled:
            if os.path.exists(clip_path):
                try: os.remove(clip_path)
                except OSError: pass
            self.clip_progress_bar.setValue(0)
            QMessageBox.information(self, "Clip Extraction Cancelled",
                                    "Clip extraction was cancelled and the partial file was removed.")
            return

        if not success:
            if os.path.exists(clip_path):
                try: os.remove(clip_path)
                except OSError: pass
            self.clip_progress_bar.setValue(0)
            QMessageBox.critical(self, "FFmpeg Error",
                                 "FFmpeg could not create the clip.\n\n" + (error_msg or "No additional error."))
            return

        # Verify the FINAL FILE. This is deliberately strict: if the source
        # had audio, a video-only output is considered a failed extraction.
        verify = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,profile,sample_rate,channels",
             "-of", "default=noprint_wrappers=1", clip_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        final_audio_info = verify.stdout.strip()
        final_has_audio = bool(final_audio_info)

        if has_audio and not final_has_audio:
            try: os.remove(clip_path)
            except OSError: pass
            self.clip_progress_bar.setValue(0)
            QMessageBox.critical(
                self, "Audio Stream Missing",
                "The source video contains an audio stream, but FFmpeg produced a clip without one.\n\n"
                "The output was deleted so a silent clip cannot be mistaken for a successful extraction.\n\n"
                f"FFmpeg error: {error_msg or 'none'}\n\n"
                f"Source audio probe:\n{source_audio_info}"
            )
            return

        self.clip_progress_bar.setValue(100)
        self.progress_label.setText("Complete — clip created successfully.")
        
        if getattr(self, "in_batch_mode", False):
            return
            
        audio_status = "Included" if final_has_audio else "None in source"
        QMessageBox.information(
            self, "Clip Extraction Complete",
            f"Done!\n\nClip duration: {format_timestamp(duration_ms)}\n"
            f"Audio: {audio_status}\n\n"
            f"Saved to:\n{clip_path}"
        )

    # -----------------------------
    # Multi-Segment & Chapter Cutting Methods
    # -----------------------------

    def addCurrentRangeToSegments(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(self, "No Source File", "Please select a source video first.")
            return
        start_ms = qtime_to_ms(self.clip_start_time_edit.time())
        end_ms = qtime_to_ms(self.clip_end_time_edit.time())
        if end_ms <= start_ms:
            QMessageBox.warning(self, "Invalid Range", "End Time must be greater than Start Time.")
            return
            
        row = self.segments_table.rowCount()
        self.segments_table.insertRow(row)
        name = f"Segment {row + 1}"
        self.segments_table.setItem(row, 0, QTableWidgetItem(name))
        self.segments_table.setItem(row, 1, QTableWidgetItem(format_timestamp(start_ms)))
        self.segments_table.setItem(row, 2, QTableWidgetItem(format_timestamp(end_ms)))
        self.segments_table.setItem(row, 3, QTableWidgetItem(duration_text(end_ms - start_ms)))
        
        self.segments_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, (start_ms, end_ms, name))

    def importChaptersToSegments(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(self, "No Source File", "Please select a source video first.")
            return
        chapters = probe_video_chapters(self.source_file)
        if not chapters:
            QMessageBox.information(self, "No Chapters", "No embedded chapters found in this video.")
            return
            
        for chap in chapters:
            row = self.segments_table.rowCount()
            self.segments_table.insertRow(row)
            name = chap["name"]
            start_ms = chap["start_ms"]
            end_ms = chap["end_ms"]
            self.segments_table.setItem(row, 0, QTableWidgetItem(name))
            self.segments_table.setItem(row, 1, QTableWidgetItem(format_timestamp(start_ms)))
            self.segments_table.setItem(row, 2, QTableWidgetItem(format_timestamp(end_ms)))
            self.segments_table.setItem(row, 3, QTableWidgetItem(duration_text(end_ms - start_ms)))
            self.segments_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, (start_ms, end_ms, name))
            
        QMessageBox.information(self, "Chapters Imported", f"Imported {len(chapters)} chapters into the segments list.")

    def removeSelectedSegment(self):
        selected = self.segments_table.selectedRanges()
        if not selected:
            return
        for r in reversed(range(self.segments_table.rowCount())):
            if self.segments_table.isItemSelected(self.segments_table.item(r, 0)):
                self.segments_table.removeRow(r)

    def clearAllSegments(self):
        self.segments_table.setRowCount(0)

    def onSegmentTableRowClicked(self, row, col):
        item = self.segments_table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and len(data) >= 2:
            start_ms, end_ms = data[0], data[1]
            self.clip_start_time_edit.setTime(ms_to_qtime(start_ms))
            self.clip_end_time_edit.setTime(ms_to_qtime(end_ms))
            self.sync_clip_time_to_frame()

    def extractAllSegments(self):
        if not hasattr(self, "source_file") or not hasattr(self, "save_dir"):
            QMessageBox.warning(self, "Missing Information", "Please select a source file and save directory first.")
            return
            
        row_count = self.segments_table.rowCount()
        if row_count == 0:
            QMessageBox.warning(self, "No Segments", "No segments in the list. Use '+ Add Current Range' or 'Import Chapters' first.")
            return
            
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            QMessageBox.critical(self, "FFmpeg Not Found", "FFmpeg is required for segment cutting.")
            return

        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index,codec_name",
             "-of", "default=noprint_wrappers=1", self.source_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        has_audio = bool(probe.stdout.strip())

        selected_format = self.clip_format_combo.currentText().upper()
        hw_accel = self.clip_hw_accel_combo.currentText()
        cv_codec = "libx264"
        if hw_accel.startswith("NVIDIA"):
            cv_codec = "h264_nvenc"
        elif hw_accel.startswith("Intel"):
            cv_codec = "h264_qsv"
        elif hw_accel.startswith("macOS"):
            cv_codec = "h264_videotoolbox"

        if selected_format == "COPY (LOSSLESS)":
            video_args = ["-c:v", "copy"]
            audio_args = ["-c:a", "copy"]
            mux_args = []
        elif selected_format == "AVI":
            video_args = ["-c:v", "mpeg4", "-q:v", "3"]
            audio_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = []
        else:
            video_args = ["-c:v", cv_codec]
            if cv_codec == "libx264":
                video_args += ["-preset", "medium", "-crf", "18"]
            elif cv_codec == "h264_nvenc":
                video_args += ["-preset", "p4", "-tune", "hq", "-cq", "19"]
            elif cv_codec == "h264_videotoolbox":
                video_args += ["-q:v", "65"]
            video_args += ["-pix_fmt", "yuv420p"]
            audio_args = ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = ["-movflags", "+faststart"] if selected_format in ("MP4", "MOV") else []

        ext = "mp4" if selected_format == "COPY (LOSSLESS)" else selected_format.lower()
        used_names = set()
        
        # Subtitle handling
        sub_idx = self.clip_subtitle_combo.currentIndex()
        sub_action = self.clip_subtitle_action.currentText() if sub_idx > 0 else None
        sub_track = self.subtitle_tracks[sub_idx - 1] if sub_idx > 0 and sub_idx <= len(getattr(self, "subtitle_tracks", [])) else None
        
        subs_list = []
        if sub_track and sub_action == "Burn into Video":
            if sub_track.get("type") == "external":
                subs_list = parse_subtitles(sub_track["path"])
            else:
                si = sub_track["sub_index"]
                temp_ext = extract_subtitles_to_temp(self.source_file, si)
                if temp_ext:
                    subs_list = parse_subtitles(temp_ext)
                    try: os.remove(temp_ext)
                    except OSError: pass

        tasks = []
        for r in range(row_count):
            item_name = self.segments_table.item(r, 0)
            data = item_name.data(Qt.ItemDataRole.UserRole) if item_name else None
            if data and len(data) >= 2:
                start_ms, end_ms = data[0], data[1]
                name = data[2] if len(data) >= 3 else item_name.text()
            else:
                name = item_name.text() if item_name else f"Segment {r + 1}"
                start_ms = 0
                end_ms = 0
                
            duration_ms = max(1, end_ms - start_ms)
            start_sec = start_ms / 1000.0
            dur_sec = duration_ms / 1000.0
            
            clean_name = re.sub(r'[^\w\-_\. ]', '_', name).strip()
            out_filename = f"{r + 1:02d}_{clean_name}.{ext}"
            out_path = self.unique_path(self.save_dir, out_filename, used_names)
            
            seg_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_sec:.6f}", "-i", self.source_file,
                "-t", f"{dur_sec:.6f}", "-map", "0:v:0"
            ]
            if has_audio:
                seg_cmd += ["-map", "0:a:0"] + list(audio_args)
            seg_cmd += list(video_args) + list(mux_args)
            
            # Burn subtitles if active
            temp_srt = None
            if subs_list and sub_action == "Burn into Video":
                shifted_srt = write_shifted_srt(subs_list, start_ms, end_ms)
                if shifted_srt:
                    temp_srt = shifted_srt
                    escaped = shifted_srt.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                    sub_filter = f"subtitles='{escaped}'"
                    seg_cmd.insert(seg_cmd.index("-map") if "-map" in seg_cmd else len(seg_cmd), "-vf")
                    vf_idx = seg_cmd.index("-vf")
                    seg_cmd.insert(vf_idx + 1, sub_filter)
                    if "-c:v" in seg_cmd:
                        cv_idx = seg_cmd.index("-c:v")
                        if seg_cmd[cv_idx + 1] == "copy":
                            seg_cmd[cv_idx + 1] = "libx264"
                            seg_cmd.insert(cv_idx + 2, "-crf")
                            seg_cmd.insert(cv_idx + 3, "18")
                            
            seg_cmd += ["-avoid_negative_ts", "make_zero", "-shortest", "-progress", "pipe:1", "-nostats", out_path]
            
            tasks.append({
                "name": name,
                "cmd": seg_cmd,
                "duration_ms": duration_ms,
                "output_path": out_path,
                "temp_srt": temp_srt
            })

        self.set_extracting_state(True)
        self.extract_segments_btn.setEnabled(False)
        self.clip_progress_bar.setValue(0)
        self.progress_label.setText(f"Extracting {len(tasks)} segments...")
        
        self.multi_segment_worker = MultiSegmentWorker(tasks)
        self.multi_segment_worker.progress.connect(self._update_multi_segment_progress)
        self.multi_segment_worker.finished.connect(self._on_multi_segment_finished)
        self.multi_segment_worker.start()

    def _update_multi_segment_progress(self, percent, label):
        self.clip_progress_bar.setValue(percent)
        self.progress_label.setText(label)

    def _on_multi_segment_finished(self, success, count, paths, message):
        self.set_extracting_state(False)
        self.extract_segments_btn.setEnabled(True)
        
        if hasattr(self, "multi_segment_worker") and self.multi_segment_worker is not None:
            self.multi_segment_worker.deleteLater()
            self.multi_segment_worker = None
            
        if success:
            self.clip_progress_bar.setValue(100)
            self.progress_label.setText(f"Extracted {count} segments successfully.")
            QMessageBox.information(
                self, "Segments Extraction Complete",
                f"Successfully extracted {count} segments to:\n{self.save_dir}"
            )
        else:
            self.clip_progress_bar.setValue(0)
            self.progress_label.setText("Segment extraction cancelled or failed.")
            if message != "Cancelled":
                QMessageBox.critical(self, "Error", message)

    # -----------------------------
    # Motion Extraction Logic
    # -----------------------------
    
    def extractMotion(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(self, "No Source File", "Select a video first.")
            return
            
        self.motion_extract_button.setEnabled(False)
        self.motion_cancel_button.setEnabled(True)
        self.motion_progress_bar.setValue(0)
        
        mode = self.motion_mode_combo.currentText()
        ext = self.motion_ext_combo.currentText()
        sensitivity = self.motion_sensitivity_spin.value()
        min_area = self.motion_min_area_spin.value()
        cooldown = self.motion_cooldown_spin.value()
        
        self.motion_worker = MotionExtractionWorker(
            self.source_file, self.save_dir, ext, mode,
            sensitivity, min_area, cooldown,
            self.get_unique_filename, self.render_filename
        )
        self.motion_worker.progress.connect(self._update_motion_progress)
        self.motion_worker.finished.connect(self._on_motion_finished)
        self.motion_worker.start()
        
    def _update_motion_progress(self, pct, label):
        self.motion_progress_bar.setValue(pct)
        self.motion_progress_label.setText(label)
        
    def _on_motion_finished(self, success, count, msg):
        self.motion_extract_button.setEnabled(True)
        self.motion_cancel_button.setEnabled(False)
        
        if hasattr(self, "motion_worker") and self.motion_worker is not None:
            self.motion_worker.deleteLater()
            self.motion_worker = None
            
        if success:
            self.motion_progress_bar.setValue(100)
            self.motion_progress_label.setText("Complete!")
            QMessageBox.information(self, "Success", f"Extracted {count} motion events.\n{msg}")
        else:
            self.motion_progress_bar.setValue(0)
            self.motion_progress_label.setText("Failed or cancelled.")
            if msg != "Cancelled":
                QMessageBox.critical(self, "Error", msg)

    # -----------------------------
    # GIF / WebP export logic
    # -----------------------------

    def exportAnimation(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(self, "No Source File", "Select a video first.")
            return

        start_ms = qtime_to_ms(self.gif_start_time.time())
        end_ms = qtime_to_ms(self.gif_end_time.time())

        if end_ms <= start_ms:
            QMessageBox.warning(self, "Invalid Range", "End time must be after start time.")
            return

        fmt = self.gif_format_combo.currentText()
        ext = ".gif" if fmt == "GIF" else ".webp"

        default_name = os.path.splitext(os.path.basename(self.source_file))[0] + ext
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Animation",
            os.path.join(getattr(self, "save_dir", ""), default_name),
            f"{'GIF Files (*.gif)' if fmt == 'GIF' else 'WebP Files (*.webp)'}"
        )
        if not save_path:
            return

        fps_text = self.gif_fps_combo.currentText()
        if fps_text == "Original":
            cap = cv2.VideoCapture(self.source_file)
            fps_val = cap.get(cv2.CAP_PROP_FPS) or 15
            cap.release()
        else:
            fps_val = int(fps_text)

        width = self._get_gif_width()
        dither = self.gif_dither_combo.currentText()
        quality = self.gif_quality_spin.value()
        loop_count = self._get_gif_loop_count()
        lossless = self.gif_lossless_checkbox.isChecked()

        self.gif_export_button.setEnabled(False)
        self.gif_cancel_button.setEnabled(True)
        self.gif_progress_bar.setValue(0)
        self.gif_progress_label.setText("Starting export...")
        
        # Build subtitle burn filter if a track is selected
        subtitle_filter = ""
        self._gif_temp_srt = None
        gif_sub_idx = self.gif_subtitle_combo.currentIndex()
        if gif_sub_idx > 0 and gif_sub_idx <= len(getattr(self, "subtitle_tracks", [])):
            track = self.subtitle_tracks[gif_sub_idx - 1]
            subs_list = []
            if track.get("type") == "external":
                subs_list = parse_subtitles(track["path"])
            else:
                si = track["sub_index"]
                temp_ext = extract_subtitles_to_temp(self.source_file, si)
                if temp_ext:
                    subs_list = parse_subtitles(temp_ext)
                    try:
                        os.remove(temp_ext)
                    except OSError:
                        pass
                        
            shifted_srt = write_shifted_srt(subs_list, start_ms, end_ms)
            if shifted_srt:
                self._gif_temp_srt = shifted_srt
                escaped = shifted_srt.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                subtitle_filter = f"subtitles='{escaped}'"

        self.gif_worker = AnimatedExportWorker(
            self.source_file, save_path, start_ms, end_ms,
            "GIF" if fmt == "GIF" else "WebP",
            width, fps_val, dither, quality, loop_count, lossless,
            subtitle_filter
        )
        self.gif_worker.progress.connect(self._update_gif_progress)
        self.gif_worker.finished.connect(self._on_gif_export_finished)
        self.gif_worker.start()

    def _update_gif_progress(self, pct, label):
        self.gif_progress_bar.setValue(pct)
        self.gif_progress_label.setText(label)

    def _on_gif_export_finished(self, success, cancelled, output_path, message):
        self.gif_export_button.setEnabled(True)
        self.gif_cancel_button.setEnabled(False)

        if getattr(self, "_gif_temp_srt", None) and os.path.exists(self._gif_temp_srt):
            try: os.remove(self._gif_temp_srt)
            except OSError: pass
            self._gif_temp_srt = None

        if hasattr(self, "gif_worker") and self.gif_worker is not None:
            self.gif_worker.deleteLater()
            self.gif_worker = None

        if cancelled:
            self.gif_progress_bar.setValue(0)
            self.gif_progress_label.setText("Cancelled.")
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return

        if not success:
            self.gif_progress_bar.setValue(0)
            self.gif_progress_label.setText("Export failed.")
            QMessageBox.critical(self, "Export Failed", message or "An unknown error occurred.")
            return

        self.gif_progress_bar.setValue(100)
        self.gif_progress_label.setText(f"Done — {message}")
        QMessageBox.information(
            self, "Export Complete",
            f"Animation saved successfully.\n\n{message}\n\nSaved to:\n{output_path}"
        )

    # -----------------------------
    # Output helpers
    # -----------------------------

    def openOutputFolder(self):
        directory = getattr(self, "save_dir", "")
        if not directory or not os.path.isdir(directory):
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def showContactSheet(self):
        paths = self.last_extracted_paths
        if not paths:
            return
        existing = [p for p in paths if os.path.exists(p)]
        if not existing:
            return
        ContactSheetDialog(existing, self).exec()

    # -----------------------------
    # Scene detection
    # -----------------------------

    def detectScenes(self):
        if not hasattr(self, "source_file"):
            QMessageBox.warning(
                self, "No Source File",
                "Select a video first."
            )
            return

        threshold, ok = QInputDialog.getDouble(
            self,
            "Scene Detection",
            "Change threshold (higher = fewer scenes):",
            0.35, 0.05, 1.0, 2
        )
        if not ok:
            return

        self.set_extracting_state(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Detecting scenes...")

        self.scene_worker = SceneDetectionWorker(self.source_file, threshold)
        self.scene_worker.progress.connect(self.progress_bar.setValue)
        self.scene_worker.error.connect(
            lambda msg: QMessageBox.warning(self, "Scene Detection Error", msg)
        )
        self.scene_worker.finished.connect(self._on_scene_detection_finished)
        self.scene_worker.start()

    def _on_scene_detection_finished(self, scenes, frame_count, fps):
        self.set_extracting_state(False)
        self.progress_bar.setValue(100)
        
        if hasattr(self, "scene_worker") and self.scene_worker is not None:
            self.scene_worker.deleteLater()
            self.scene_worker = None

        if not scenes:
            QMessageBox.information(
                self, "Scene Detection",
                "No scene changes were detected with that threshold."
            )
            return

        dialog = SceneResultsDialog(scenes, frame_count, fps, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.action:
            if not hasattr(self, "save_dir"):
                QMessageBox.warning(self, "Missing Output", "Choose a save directory first.")
                return
                
            action = dialog.action
            ext = f".{self.format_combo.currentText().lower()}" if action == "keyframes" else ".mp4"
            
            self.set_extracting_state(True)
            self.progress_bar.setValue(0)
            self.progress_label.setText(f"Starting {action} extraction...")
            
            self.scene_action_worker = SceneActionWorker(
                action, scenes, self.source_file, self.save_dir, ext,
                self.render_filename, self.unique_path, fps, frame_count
            )
            self.scene_action_worker.progress.connect(
                lambda pct, lbl: (self.progress_bar.setValue(pct), self.progress_label.setText(lbl))
            )
            self.scene_action_worker.finished.connect(self._on_scene_action_finished)
            self.scene_action_worker.start()
            
    def _on_scene_action_finished(self, success, msg):
        self.set_extracting_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Complete.")
        
        if hasattr(self, "scene_action_worker") and self.scene_action_worker is not None:
            self.scene_action_worker.deleteLater()
            self.scene_action_worker = None
            
        if success:
            QMessageBox.information(self, "Success", msg)
            self.open_output_button.setEnabled(True)
        else:
            QMessageBox.critical(self, "Error", msg)

    # -----------------------------
    # Batch processing
    # -----------------------------

    def addBatchVideos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Videos to Queue",
            self.settings.value("last_source_dir", ""),
            VIDEO_FILTER
        )
        for path in files:
            if path not in self.batch_queue:
                self.batch_queue.append(path)
                self.batch_list.addItem(path)

    def removeBatchVideos(self):
        selected = self.batch_list.selectedItems()
        for item in selected:
            row = self.batch_list.row(item)
            self.batch_list.takeItem(row)
            if item.text() in self.batch_queue:
                self.batch_queue.remove(item.text())

    def clearBatchVideos(self):
        self.batch_queue.clear()
        self.batch_list.clear()

    def processBatch(self):
        if not self.batch_queue:
            QMessageBox.information(
                self, "Batch Queue",
                "The batch queue is empty."
            )
            return

        if not hasattr(self, "save_dir"):
            QMessageBox.warning(
                self, "Missing Output",
                "Choose a save directory first."
            )
            return

        self.batch_queue_copy = list(self.batch_queue)
        self.batch_completed = 0
        self.batch_original_file = getattr(self, "source_file", None)
        
        mode = self.batch_mode_combo.currentText()
        if "Clip" in mode:
            self.tab_widget.setCurrentWidget(self.clip_tab)
        else:
            self.tab_widget.setCurrentWidget(self.frame_tab)
            
        self.process_next_batch_item()

    def process_next_batch_item(self):
        if hasattr(self, "frame_worker") and self.frame_worker is not None:
            self.frame_worker.deleteLater()
            self.frame_worker = None
            
        if hasattr(self, "clip_worker") and self.clip_worker is not None:
            self.clip_worker.deleteLater()
            self.clip_worker = None

        gc.collect()

        if sys.platform.startswith("linux"):
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass

        if self.cancel_requested or not self.batch_queue_copy:
            self.source_file = self.batch_original_file if self.batch_original_file else getattr(self, "source_file", None)
            self.in_batch_mode = False
            QMessageBox.information(
                self, "Batch Complete",
                f"Batch processing finished.\nProcessed {self.batch_completed} videos."
            )
            return
            
        path = self.batch_queue_copy.pop(0)
        self.load_video_path(path)
        self.in_batch_mode = True
        
        mode = self.batch_mode_combo.currentText()
        if "Clip" in mode:
            if self.current_metadata:
                frames = int(self.current_metadata.get("Total Frames", "0").replace(",", ""))
                self.clip_start_frame_spinbox.setValue(0)
                self.clip_end_frame_spinbox.setValue(frames)
                
            self.extractClip()
            
            # extractClip handles its own _on_clip_extraction_finished, but we need to loop
            if hasattr(self, "clip_worker") and self.clip_worker is not None:
                def on_batch_clip_finished(success, cancelled, path, err, dur, has_aud, ff, src_aud):
                    if not cancelled:
                        self.batch_completed += 1
                        QTimer.singleShot(100, self.process_next_batch_item)
                    else:
                        self.in_batch_mode = False
                
                # intercept using a disconnected wrapper
                self.clip_worker.finished.disconnect()
                self.clip_worker.finished.connect(
                    lambda s, c, p, e, dur, has_aud, ff, src_aud: self._on_clip_extraction_finished(s, c, p, e, dur, has_aud, ff, src_aud)
                )
                self.clip_worker.finished.connect(on_batch_clip_finished)
                
        else:
            self.extractFrames()
            
            if hasattr(self, "frame_worker") and self.frame_worker is not None:
                def on_batch_frame_finished(extracted, elapsed, cancelled):
                    if not cancelled:
                        self.batch_completed += 1
                        QTimer.singleShot(100, self.process_next_batch_item)
                    else:
                        self.in_batch_mode = False
                        
                self.frame_worker.finished.disconnect()
                self.frame_worker.finished.connect(self._on_frame_extraction_finished)
                self.frame_worker.finished.connect(on_batch_frame_finished)

    # -----------------------------
    # Presets
    # -----------------------------

    def savePreset(self):
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:"
        )
        if not ok or not name.strip():
            return

        preset = {
            "filename_template": self.filename_template_edit.text(),
            "format": self.format_combo.currentText(),
            "mode": self.extraction_mode_combo.currentText(),
            "custom_fps": self.custom_fps_spin.value(),
            "extract_part": self.extract_part_checkbox.isChecked(),
            "start_ms": qtime_to_ms(self.start_time_edit.time()),
            "end_ms": qtime_to_ms(self.end_time_edit.time()),
            "auto_close": self.auto_close_checkbox.isChecked()
        }

        presets = json.loads(
            self.settings.value("presets", "{}")
        )
        presets[name.strip()] = preset
        self.settings.setValue("presets", json.dumps(presets))

        self.refresh_preset_combo()

    def loadPreset(self):
        name = self.preset_combo.currentText()
        if not name or name == "Presets...":
            return

        presets = json.loads(
            self.settings.value("presets", "{}")
        )
        preset = presets.get(name)
        if not preset:
            return

        self.filename_template_edit.setText(
            preset.get("filename_template", "frame_{timestamp}")
        )
        self.format_combo.setCurrentText(
            preset.get("format", "PNG")
        )
        self.extraction_mode_combo.setCurrentText(
            preset.get("mode", "Every Frame")
        )
        self.custom_fps_spin.setValue(
            float(preset.get("custom_fps", 1.0))
        )
        self.extract_part_checkbox.setChecked(
            bool(preset.get("extract_part", False))
        )
        self.start_time_edit.setTime(
            ms_to_qtime(int(preset.get("start_ms", 0)))
        )
        self.end_time_edit.setTime(
            ms_to_qtime(int(preset.get("end_ms", 0)))
        )
        self.auto_close_checkbox.setChecked(
            bool(preset.get("auto_close", False))
        )

    def refresh_preset_combo(self):
        if not hasattr(self, "preset_combo"):
            return

        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItem("Presets...")

        presets = json.loads(
            self.settings.value("presets", "{}")
        )
        self.preset_combo.addItems(sorted(presets.keys()))

        if current:
            index = self.preset_combo.findText(current)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)

    # -----------------------------
    # Extra utility UI
    # -----------------------------

    def addUtilityControls(self):
        pass


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("throot.omniextractstudio.app.1.0")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Throot Omni Extract Studio")
    app.setOrganizationName("Throot")
    
    icon_path = get_resource_path("OmniExtract.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    ex = OmniExtractStudio()

    # Add the utility controls after the main UI has been constructed.
    # Presets and batch controls live in a compact toolbar above the tabs.
    toolbar = QHBoxLayout()

    ex.preset_combo = QComboBox()
    ex.preset_combo.setMinimumWidth(150)
    ex.preset_combo.addItem("Presets...")
    ex.refresh_preset_combo()
    ex.preset_combo.currentIndexChanged.connect(
        lambda _: ex.loadPreset()
    )

    save_preset = QPushButton("Save Preset")
    save_preset.clicked.connect(ex.savePreset)

    scenes = QPushButton("Scene Detection")
    scenes.clicked.connect(ex.detectScenes)

    toolbar.addWidget(QLabel("Preset:"))
    toolbar.addWidget(ex.preset_combo)
    toolbar.addWidget(save_preset)
    toolbar.addStretch()
    toolbar.addWidget(scenes)

    ex.layout().insertLayout(0, toolbar)

    ex.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
