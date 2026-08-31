import os

from PyQt6.QtCore import QRectF, QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..media.metadata import probe_video_metadata
from ..media.subtitles import extract_subtitles_to_temp, parse_subtitles
from ..utils.timestamps import format_timestamp


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

        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(4)
        from PyQt6.QtGui import QColor
        shadow.setColor(QColor(0, 0, 0, 255))
        shadow.setOffset(2, 2)
        self.sub_proxy.setGraphicsEffect(shadow)

        self._update_subtitle_style()

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

        lbl_h = max(100, int(h / 3))
        y = h - lbl_h

        self.sub_label.setFixedSize(w, lbl_h)
        self.sub_proxy.setPos(0, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()


class ThumbnailWorker(QThread):
    """Asynchronously generates timeline thumbnails in the background using ffmpeg."""
    thumbnail_ready = pyqtSignal(int, int, QImage)

    def __init__(self, video_path, fps, frame_count, count=12):
        super().__init__()
        self.video_path = video_path
        self.fps = fps if fps > 0 else 30.0
        self.frame_count = frame_count
        self.count = count
        self.cancel_requested = False
        self.current_proc = None

    def cancel(self):
        self.cancel_requested = True
        if self.current_proc:
            try:
                self.current_proc.kill()
            except Exception:
                pass

    def run(self):
        if self.frame_count <= 0 or not os.path.isfile(self.video_path):
            return

        import shutil
        import subprocess
        import sys
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return

        count = min(self.count, max(6, self.frame_count))

        with tempfile.TemporaryDirectory() as temp_dir:
            for i in range(count):
                if self.cancel_requested:
                    break

                frame_number = int((self.frame_count - 1) * i / max(1, count - 1))
                timestamp_sec = frame_number / self.fps
                timestamp_ms = int(timestamp_sec * 1000)

                out_path = os.path.join(temp_dir, f"thumb_{i}.jpg")
                
                # Robust output-seeking fallback for broken MKV indices
                cmd = [
                    ffmpeg, "-y", "-v", "error",
                    "-ss", str(timestamp_sec),
                    "-i", self.video_path,
                    "-vframes", "1",
                    "-vf", "scale=130:-2",
                    out_path
                ]
                try:
                    self.current_proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                    )
                    _, stderr = self.current_proc.communicate(timeout=4.0)
                    if self.current_proc.returncode != 0:
                        print(f"ffmpeg thumbnail error: {stderr}", file=sys.stderr)
                except subprocess.TimeoutExpired:
                    if self.current_proc:
                        try:
                            self.current_proc.kill()
                            self.current_proc.wait()
                        except Exception:
                            pass
                    continue
                except OSError as e:
                    print(f"ffmpeg os error: {e}", file=sys.stderr)
                    continue
                finally:
                    self.current_proc = None

                if self.cancel_requested:
                    break

                if os.path.isfile(out_path):
                    qimg = QImage(out_path)
                    if not qimg.isNull():
                        self.thumbnail_ready.emit(i, timestamp_ms, qimg)


class VideoPreviewDialog(QDialog):
    """Video preview dialog with playback, subtitles, and scrubbing."""

    in_selected = pyqtSignal(int)
    out_selected = pyqtSignal(int)

    def __init__(self, video_path, metadata=None, subtitle_tracks=None, initial_sub_index=0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.subtitle_tracks = subtitle_tracks or []
        self.initial_sub_index = initial_sub_index
        self.subtitles_data = []
        self._temp_extracted_srt = None
        self._slider_dragging = False

        if not metadata:
            metadata = probe_video_metadata(video_path)

        self.fps = metadata.get("RawFPS", 30.0) if metadata.get("RawFPS") else 30.0
        self.frame_count = metadata.get("RawFrames", 0)
        self.duration_ms = metadata.get("DurationMs", 0)

        self.setWindowTitle(f"Preview - {os.path.basename(video_path)} ({self.fps:.2f} fps)")
        self.resize(1020, 740)

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

        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25x", "0.5x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self.change_speed)

        self.sub_combo = QComboBox()
        self.sub_combo.addItem("Subtitles: Off")
        for track in self.subtitle_tracks:
            self.sub_combo.addItem(f"Sub: {track['label']}")
        self.sub_combo.addItem("<Browse External Subtitle...>")
        self.sub_combo.currentIndexChanged.connect(self._on_subtitle_track_changed)

        self.color_combo = QComboBox()
        self.color_combo.addItems(["White", "Black", "Yellow", "Red", "Green", "Blue"])
        self.color_combo.currentTextChanged.connect(self.video_widget.set_subtitle_color)

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
        self.media_player.errorOccurred.connect(self._on_media_error)
        self.media_player.setSource(QUrl.fromLocalFile(video_path))

        if self.initial_sub_index > 0 and self.initial_sub_index <= len(self.subtitle_tracks):
            self.sub_combo.setCurrentIndex(self.initial_sub_index)

        self.thumb_worker = ThumbnailWorker(video_path, self.fps, self.frame_count, count=12)
        self.thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.thumb_worker.start()

    def _on_thumbnail_ready(self, index, timestamp_ms, qimg):
        button = QToolButton()
        pixmap = QPixmap.fromImage(qimg)
        button.setIcon(QIcon(pixmap))
        button.setText(format_timestamp(timestamp_ms))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(130, 75))
        button.clicked.connect(lambda checked=False, ms=timestamp_ms: self.media_player.setPosition(ms))
        self.thumbnail_layout.addWidget(button)

    def _on_media_error(self, error, error_string):
        QMessageBox.warning(self, "Playback Error", f"Cannot play video:\n{error_string}")

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
        delta = max(1, round(1000.0 / self.fps))
        new_pos = max(0, self.media_player.position() - delta)
        self.media_player.setPosition(new_pos)
        self._refresh_subtitle_at(new_pos)

    def next_single_frame(self):
        self.media_player.pause()
        delta = max(1, round(1000.0 / self.fps))
        new_pos = min(self.duration_ms, self.media_player.position() + delta)
        self.media_player.setPosition(new_pos)
        self._refresh_subtitle_at(new_pos)

    def save_current_frame(self):
        self.media_player.pause()
        pos_ms = self.media_player.position()
        timestamp_sec = pos_ms / 1000.0

        import shutil
        import subprocess
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            QMessageBox.warning(self, "Missing Dependency", "FFmpeg is required.")
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
            cmd = [
                ffmpeg, "-y", "-v", "error", "-ss", str(timestamp_sec),
                "-i", self.video_path, "-vframes", "1", path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.isfile(path):
                QMessageBox.information(self, "Frame Saved", f"Frame saved successfully.\n\n{path}")
            else:
                QMessageBox.critical(self, "Save Failed", "FFmpeg could not write the image.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save the frame.\n\n{exc}")

    def closeEvent(self, event):
        self.media_player.stop()
        if hasattr(self, "thumb_worker") and self.thumb_worker.isRunning():
            self.thumb_worker.cancel()
            self.thumb_worker.wait(1500)
        if self._temp_extracted_srt and os.path.exists(self._temp_extracted_srt):
            try: os.remove(self._temp_extracted_srt)
            except OSError: pass
        event.accept()
