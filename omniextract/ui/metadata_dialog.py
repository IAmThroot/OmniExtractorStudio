import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGridLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..media.ffmpeg import get_ffmpeg_version
from ..utils.timestamps import format_timestamp, frame_to_ms


class AboutDialog(QDialog):
    """About dialog showing application version, FFmpeg status, and repository info."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About OmniExtract Studio")
        self.resize(420, 260)

        layout = QVBoxLayout(self)

        title = QLabel(f"<h2>OmniExtract Studio <span style='font-size: 14pt; color: #4A90E2;'>v{__version__}</span></h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "An all-in-one desktop video processing and dataset creation toolkit.<br>"
            "Designed for high-precision frame extraction, clipping, motion analysis, "
            "subtitles, and GIF/WebP generation."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        ffmpeg_info = QLabel(f"<b>FFmpeg Core:</b> {get_ffmpeg_version()}")
        ffmpeg_info.setWordWrap(True)
        ffmpeg_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(ffmpeg_info)

        repo_btn = QPushButton("Visit GitHub Repository")
        repo_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/IAmThroot/OmniExtractorStudio")))
        layout.addWidget(repo_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


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
