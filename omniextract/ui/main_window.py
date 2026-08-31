import gc
import os
import re
import shutil
import subprocess
import sys

from PyQt6.QtCore import QSettings, Qt, QTime, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..media.metadata import (
    probe_video_chapters,
    probe_video_metadata,
)
from ..media.subtitles import (
    extract_subtitles_to_temp,
    parse_subtitles,
    write_shifted_srt,
)
from ..media.video import VIDEO_FILTER
from ..utils.filenames import render_filename as util_render_filename, unique_path as util_unique_path
from ..utils.presets import PresetManager
from ..utils.resources import get_resource_path
from ..utils.timestamps import (
    duration_text,
    format_timestamp,
    frame_to_ms,
    ms_to_qtime,
    qtime_to_ms,
)
from ..workers import (
    AnimatedExportWorker,
    ClipExtractionWorker,
    FrameExtractionWorker,
    MotionExtractionWorker,
    MultiSegmentWorker,
    SceneActionWorker,
    SceneDetectionWorker,
)
from .components.drop_line_edit import DropLineEdit
from .metadata_dialog import (
    ContactSheetDialog,
    MetadataDialog,
    SceneResultsDialog,
)
from .preview_dialog import VideoPreviewDialog


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
        self.setWindowTitle("OmniExtract Studio v1.1.0")
        self.resize(1000, 700)
        
        icon_candidates = [
            "OmniExtract.ico",
            "OmniExtract.png",
            "resources/icon.ico",
            "resources/icon.png",
        ]
        for candidate in icon_candidates:
            cand_path = get_resource_path(candidate)
            if os.path.exists(cand_path):
                icon = QIcon(cand_path)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    break

        self.preset_manager = PresetManager()
        self._block_preset_signals = False

        # Presets Toolbar
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(10, 5, 10, 5)
        preset_layout.addWidget(QLabel("<b>Profile Preset:</b>"))
        
        self.preset_combo = QComboBox()
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.preset_combo.addItems(self.preset_manager.get_all_preset_names())
        self.preset_combo.currentTextChanged.connect(self.on_preset_selected)
        
        self.save_preset_btn = QPushButton("Save as Preset...")
        self.save_preset_btn.clicked.connect(self.save_custom_preset)
        
        self.delete_preset_btn = QPushButton("Delete Preset")
        self.delete_preset_btn.clicked.connect(self.delete_custom_preset)
        
        preset_layout.addWidget(self.preset_combo)
        preset_layout.addWidget(self.save_preset_btn)
        preset_layout.addWidget(self.delete_preset_btn)
        preset_layout.addStretch()

        self.tab_widget = QTabWidget(self)

        self.frame_tab = QWidget()
        self.initFrameTab()
        self.tab_widget.addTab(self.frame_tab, "Extract Frames")

        self.motion_tab = QWidget()
        self.initMotionTab()
        self.tab_widget.addTab(self.motion_tab, "Motion Extraction")

        self.clip_tab = QWidget()
        self.initClipTab()
        self.tab_widget.addTab(self.clip_tab, "Clip Cutting")
        
        self.gif_webp_tab = QWidget()
        self.initGifWebpTab()
        self.tab_widget.addTab(self.gif_webp_tab, "GIF & WebP Maker")
        
        self.batch_tab = QWidget()
        self.initBatchTab()
        self.tab_widget.addTab(self.batch_tab, "Batch Queue")

        self.shared_io_panel = self.init_shared_io_panel()

        main_layout = QVBoxLayout()
        main_layout.addLayout(preset_layout)
        main_layout.addWidget(self.shared_io_panel)
        main_layout.addWidget(self.tab_widget)
        self.setLayout(main_layout)
        
        # Connect generic change signals to mark "Custom..."
        self._connect_preset_signals()
        
        # Select Default preset at startup
        self.preset_combo.setCurrentText("Default")
        self._update_preset_buttons()

    # -----------------------------
    # Shared I/O Panel
    # -----------------------------
    
    def init_shared_io_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 5, 0, 5)

        # Source Section
        source_layout = QHBoxLayout()
        source_label = QLabel("<b>Source File:</b>")
        self.source_file_edit = DropLineEdit(self.load_video_path)
        self.source_file_edit.setPlaceholderText("Drop a video here or Browse...")
        self.source_file_edit.setReadOnly(True)
        
        self.source_file_button = QPushButton("Browse")
        self.source_file_button.clicked.connect(self.selectSourceFile)
        
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.previewSourceFile)
        self.preview_button.setEnabled(False)
        
        self.info_button = QPushButton("Video Info")
        self.info_button.clicked.connect(self.showVideoInfo)
        self.info_button.setEnabled(False)

        source_layout.addWidget(source_label)
        source_layout.addWidget(self.source_file_edit)
        source_layout.addWidget(self.source_file_button)
        source_layout.addWidget(self.preview_button)
        source_layout.addWidget(self.info_button)

        # Output Section
        output_layout = QHBoxLayout()
        output_label = QLabel("<b>Save Directory:</b>")
        self.save_dir_edit = DropLineEdit(self.selectSaveDirFromDrop) if hasattr(self, 'selectSaveDirFromDrop') else QLineEdit()
        self.save_dir_edit.setPlaceholderText("Select output directory...")
        self.save_dir_edit.setReadOnly(True)
        
        self.save_dir_button = QPushButton("Browse")
        self.save_dir_button.clicked.connect(self.selectSaveDir)
        
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.save_dir_edit)
        output_layout.addWidget(self.save_dir_button)
        
        layout.addLayout(source_layout)
        layout.addLayout(output_layout)
        return panel

    # -----------------------------
    # Full-App Preset Logic
    # -----------------------------
    
    def initBatchTab(self):
        layout = QVBoxLayout()
        
        self.batch_list = QListWidget()
        self.batch_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        btn_layout = QHBoxLayout()
        self.batch_add_btn = QPushButton("Add Videos (Bulk Job)")
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

        output_group = QGroupBox("Extraction Settings")
        output_layout = QFormLayout()

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
        
        self.frame_queue_button = QPushButton("Send to Batch Queue (Current Settings)")
        self.frame_queue_button.clicked.connect(self.queueCurrentFrameJob)

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
        buttons.addWidget(self.frame_queue_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.open_output_button)
        buttons.addWidget(self.contact_sheet_button)

        self.auto_close_checkbox = QCheckBox("Auto Close")
        self.remember_settings_checkbox = QCheckBox("Remember Settings")
        self.remember_settings_checkbox.setChecked(True)

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
        
        self.motion_engine_combo = QComboBox()
        self.motion_engine_combo.addItems(["MOG2 (Pixel-Based)", "YOLOv8 (AI-Based)"])
        settings_layout.addRow("Processing Engine:", self.motion_engine_combo)
        
        self.motion_mode_combo = QComboBox()
        self.motion_mode_combo.addItems(["Keyframes", "Clips"])
        self.motion_mode_combo.setToolTip("Keyframes saves 1 image per motion event. Clips exports the entire motion duration.")
        settings_layout.addRow("Extraction Mode:", self.motion_mode_combo)
        
        self.motion_ext_combo = QComboBox()
        self.motion_ext_combo.addItems([".jpg", ".png"])
        settings_layout.addRow("Extension:", self.motion_ext_combo)
        
        def update_motion_ext(mode):
            self.motion_ext_combo.clear()
            if mode == "Clips":
                self.motion_ext_combo.addItems([".mp4", ".mkv", ".avi"])
            else:
                self.motion_ext_combo.addItems([".jpg", ".png"])
                
        self.motion_mode_combo.currentTextChanged.connect(update_motion_ext)
        
        self.motion_cooldown_spin = QSpinBox()
        self.motion_cooldown_spin.setRange(0, 60000)
        self.motion_cooldown_spin.setValue(2000)
        self.motion_cooldown_spin.setSuffix(" ms")
        self.motion_cooldown_spin.setToolTip("Time with no motion before ending a clip event.")
        settings_layout.addRow("Cooldown Time:", self.motion_cooldown_spin)
        
        # MOG2 Specific Settings
        self.mog2_widget = QWidget()
        mog2_layout = QFormLayout(self.mog2_widget)
        mog2_layout.setContentsMargins(0, 0, 0, 0)
        
        self.motion_sensitivity_spin = QSpinBox()
        self.motion_sensitivity_spin.setRange(0, 100)
        self.motion_sensitivity_spin.setValue(80)
        self.motion_sensitivity_spin.setToolTip("Higher = more sensitive to motion (lower MOG2 threshold).")
        mog2_layout.addRow("Sensitivity:", self.motion_sensitivity_spin)
        
        self.motion_min_area_spin = QSpinBox()
        self.motion_min_area_spin.setRange(10, 100000)
        self.motion_min_area_spin.setValue(1000)
        self.motion_min_area_spin.setToolTip("Minimum contour area to trigger motion (filters small noise).")
        mog2_layout.addRow("Min Area (px):", self.motion_min_area_spin)
        
        # YOLO Specific Settings
        self.yolo_widget = QWidget()
        yolo_layout = QFormLayout(self.yolo_widget)
        yolo_layout.setContentsMargins(0, 0, 0, 0)
        
        self.motion_yolo_conf = QDoubleSpinBox()
        self.motion_yolo_conf.setRange(0.01, 1.0)
        self.motion_yolo_conf.setSingleStep(0.05)
        self.motion_yolo_conf.setValue(0.40)
        self.motion_yolo_conf.setToolTip("Confidence threshold for object detection.")
        yolo_layout.addRow("Confidence Threshold:", self.motion_yolo_conf)
        self.yolo_widget.setVisible(False)
        
        settings_layout.addRow(self.mog2_widget)
        settings_layout.addRow(self.yolo_widget)
        
        def update_engine_ui(engine):
            if "YOLO" in engine:
                self.mog2_widget.setVisible(False)
                self.yolo_widget.setVisible(True)
            else:
                self.mog2_widget.setVisible(True)
                self.yolo_widget.setVisible(False)
                
        self.motion_engine_combo.currentTextChanged.connect(update_engine_ui)
        update_engine_ui(self.motion_engine_combo.currentText())
        
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
        layout.addWidget(trim_group)


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
        
        self.gif_queue_button = QPushButton("Send to Batch Queue")
        self.gif_queue_button.clicked.connect(self.queueCurrentAnimationJob)

        self.gif_cancel_button = QPushButton("Cancel")
        self.gif_cancel_button.clicked.connect(self._cancel_gif_export)
        self.gif_cancel_button.setEnabled(False)

        btn_layout.addWidget(self.gif_export_button)
        btn_layout.addWidget(self.gif_queue_button)
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
    def initClipTab(self):
        layout = QVBoxLayout()

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
        
        self.clip_queue_button = QPushButton("Send to Batch Queue")
        self.clip_queue_button.clicked.connect(self.queueCurrentClipJob)
        clip_buttons.addWidget(self.clip_queue_button)
        
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
        self.update_video_metadata()

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

    def update_video_metadata(self):
        self.current_metadata = probe_video_metadata(self.source_file)
        
        if not self.current_metadata:
            self.preview_button.setEnabled(False)
            self.info_button.setEnabled(False)
            QMessageBox.warning(
                self, "Unable to Open Video",
                "The selected file could not be opened as a video."
            )
            return

        duration_ms = self.current_metadata.get("DurationMs", 0)

        self.preview_button.setEnabled(True)
        self.info_button.setEnabled(True)
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
        
        frame_count = self.current_metadata.get("RawFrames", 0)
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
                    capture_output=True, text=True, timeout=10
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
            metadata=getattr(self, "current_metadata", None),
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
        template = self.filename_template_edit.text() if hasattr(self, "filename_template_edit") else ""
        return util_render_filename(template, frame_number, timestamp_ms, extension)

    def unique_path(self, directory, filename, used):
        return util_unique_path(directory, filename, used)

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

    def queueCurrentFrameJob(self):
        if not getattr(self, "source_file", None):
            QMessageBox.warning(self, "No Video", "Please load a video first.")
            return
            
        state = self.gather_app_state(is_custom_job=True)
        state["target_tab"] = 0 # Force to Frame Tab
        
        name = os.path.basename(self.source_file)
        item = QListWidgetItem(f"{name} [Extract Frames Job]")
        item.setData(Qt.ItemDataRole.UserRole, {
            "path": self.source_file,
            "state": state,
            "mode": "Extract Frames"
        })
        self.batch_list.addItem(item)
        
        # Add to legacy path queue as fallback just in case
        if self.source_file not in self.batch_queue:
            self.batch_queue.append(self.source_file)
            
        QMessageBox.information(self, "Queued", f"Added {name} to Batch Queue with current frame settings.")

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

        if not self.current_metadata:
            QMessageBox.warning(
                self, "Unable to Open Video",
                "No video metadata available. Please reload the source file."
            )
            return

        fps = self.current_metadata.get("RawFPS", 0)
        frame_count = self.current_metadata.get("RawFrames", 0)

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

    def queueCurrentClipJob(self):
        if not getattr(self, "source_file", None):
            QMessageBox.warning(self, "No Video", "Please load a video first.")
            return
            
        state = self.gather_app_state(is_custom_job=True)
        state["target_tab"] = 2 # Force to Clip Tab
        
        name = os.path.basename(self.source_file)
        item = QListWidgetItem(f"{name} [Clip Extraction Job]")
        item.setData(Qt.ItemDataRole.UserRole, {
            "path": self.source_file,
            "state": state,
            "mode": "Extract Clips (Full Video)" # Legacy backward compatibility
        })
        self.batch_list.addItem(item)
        
        if self.source_file not in self.batch_queue:
            self.batch_queue.append(self.source_file)
            
        QMessageBox.information(self, "Queued", f"Added {name} to Batch Queue with current clip settings.")

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

        if not self.current_metadata:
            QMessageBox.warning(self, "Unable to Open Video",
                                "No video metadata available. Please reload the source file.")
            return
        fps = self.current_metadata.get("RawFPS", 0)
        frame_count = self.current_metadata.get("RawFrames", 0)

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
            capture_output=True, text=True
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
                    subprocess.run(srt_cmd, capture_output=True, timeout=60)
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
            capture_output=True, text=True
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
            capture_output=True, text=True
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
        if not hasattr(self, "source_file") or not hasattr(self, "save_dir"):
            QMessageBox.warning(self, "Missing Information", "Please select a source file and save directory first.")
            return
            
        self.motion_extract_button.setEnabled(False)
        self.motion_cancel_button.setEnabled(True)
        self.motion_progress_bar.setValue(0)
        
        engine = self.motion_engine_combo.currentText()
        mode = self.motion_mode_combo.currentText()
        ext = self.motion_ext_combo.currentText()
        sensitivity = self.motion_sensitivity_spin.value()
        min_area = self.motion_min_area_spin.value()
        cooldown = self.motion_cooldown_spin.value()
        yolo_conf = self.motion_yolo_conf.value()
        
        if "YOLO" in engine:
            try:
                import onnxruntime
            except ImportError:
                QMessageBox.warning(self, "Missing Dependency", "YOLOv8 ONNX mode requires the 'onnxruntime' package.\n\nPlease run: pip install onnxruntime")
                self.motion_extract_button.setEnabled(True)
                self.motion_cancel_button.setEnabled(False)
                return
                
            model_path = get_resource_path("assets/models/yolov8n.onnx")
            if not os.path.exists(model_path):
                QMessageBox.warning(self, "Missing Model", "Could not find 'yolov8n.onnx' in assets/models/")
                self.motion_extract_button.setEnabled(True)
                self.motion_cancel_button.setEnabled(False)
                return

        self.motion_worker = MotionExtractionWorker(
            self.source_file, self.save_dir, ext, mode,
            sensitivity, min_area, cooldown,
            self.unique_path, self.render_filename,
            engine, yolo_conf
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

    def queueCurrentAnimationJob(self):
        if not getattr(self, "source_file", None):
            QMessageBox.warning(self, "No Video", "Please load a video first.")
            return
            
        state = self.gather_app_state(is_custom_job=True)
        state["target_tab"] = 3 # Force to GIF/WebP Tab
        
        name = os.path.basename(self.source_file)
        item = QListWidgetItem(f"{name} [Animation Job]")
        item.setData(Qt.ItemDataRole.UserRole, {
            "path": self.source_file,
            "state": state,
            "mode": "GIF/WebP Conversion Batch"
        })
        self.batch_list.addItem(item)
        
        if self.source_file not in self.batch_queue:
            self.batch_queue.append(self.source_file)
            
        QMessageBox.information(self, "Queued", f"Added {name} to Batch Queue with current animation settings.")

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
        if getattr(self, "in_batch_mode", False) and hasattr(self, "save_dir"):
            # Auto-generate name in batch mode
            base = default_name
            save_path = os.path.join(self.save_dir, base)
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(self.save_dir, f"{os.path.splitext(base)[0]}_{counter}{ext}")
                counter += 1
        else:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Animation",
                os.path.join(getattr(self, "save_dir", ""), default_name),
                f"{'GIF Files (*.gif)' if fmt == 'GIF' else 'WebP Files (*.webp)'}"
            )
            if not save_path:
                return

        fps_text = self.gif_fps_combo.currentText()
        if fps_text == "Original":
            fps_val = self.current_metadata.get("RawFPS", 15) if self.current_metadata else 15
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
        if not files:
            return
            
        mode, ok = QInputDialog.getItem(
            self, "Select Job Type", 
            "What action should be performed on these videos?", 
            ["Extract Frames", "Extract Clip", "Motion Highlights", "GIF/WebP"], 
            0, False
        )
        if not ok:
            return
            
        state = self.gather_app_state(is_custom_job=True)
        # Ensure target_tab matches the selected mode
        if mode == "Motion Highlights":
            state["target_tab"] = 1
        elif mode == "Extract Clip":
            state["target_tab"] = 2
        elif mode == "GIF/WebP":
            state["target_tab"] = 3
        else:
            state["target_tab"] = 0
            
        for path in files:
            if path not in self.batch_queue:
                self.batch_queue.append(path)
                
                name = os.path.basename(path)
                item = QListWidgetItem(f"{name} [{mode} Bulk Job]")
                item.setData(Qt.ItemDataRole.UserRole, {
                    "path": path,
                    "state": state,
                    "mode": mode
                })
                self.batch_list.addItem(item)

    def removeBatchVideos(self):
        selected = self.batch_list.selectedItems()
        for item in selected:
            row = self.batch_list.row(item)
            data = item.data(Qt.ItemDataRole.UserRole)
            path = data["path"] if data else item.text()
            
            self.batch_list.takeItem(row)
            if path in self.batch_queue:
                self.batch_queue.remove(path)

    def clearBatchVideos(self):
        self.batch_queue.clear()
        self.batch_list.clear()

    def processBatch(self):
        if self.batch_list.count() == 0:
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

        self.batch_jobs = []
        for i in range(self.batch_list.count()):
            item = self.batch_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None:
                self.batch_jobs.append(data)
            else:
                self.batch_jobs.append({
                    "path": item.text(),
                    "state": None,
                    "mode": "Extract Frames"
                })

        self.batch_completed = 0
        self.batch_original_file = getattr(self, "source_file", None)
        
        self.process_next_batch_item()

    def process_next_batch_item(self):
        # Clean up any leftover workers
        for w_name in ["frame_worker", "clip_worker", "motion_worker", "animation_worker"]:
            worker = getattr(self, w_name, None)
            if worker is not None:
                worker.deleteLater()
                setattr(self, w_name, None)

        gc.collect()

        if sys.platform.startswith("linux"):
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass

        if self.cancel_requested or not self.batch_jobs:
            self.source_file = self.batch_original_file if self.batch_original_file else getattr(self, "source_file", None)
            self.in_batch_mode = False
            QMessageBox.information(
                self, "Batch Complete",
                f"Batch processing finished.\nProcessed {self.batch_completed} videos."
            )
            return
            
        job = self.batch_jobs.pop(0)
        path = job["path"]
        self.load_video_path(path)
        self.in_batch_mode = True
        
        state = job["state"]
        mode = job["mode"]
        
        if state is not None:
            self.apply_app_state(state)
        
        if "Motion" in mode:
            self.tab_widget.setCurrentWidget(self.motion_tab)
        elif "Convert" in mode or "Extract Clip" in mode:
            self.tab_widget.setCurrentWidget(self.clip_tab)
        elif "GIF" in mode or "WebP" in mode:
            self.tab_widget.setCurrentWidget(self.gif_webp_tab)
        else:
            self.tab_widget.setCurrentWidget(self.frame_tab)

        if "Convert" in mode or "Extract Clip" in mode:
            if state is None and self.current_metadata:
                frames = int(self.current_metadata.get("Total Frames", "0").replace(",", ""))
                self.clip_start_frame_spinbox.setValue(0)
                self.clip_end_frame_spinbox.setValue(frames)
                
            self.extractClip()
            
            if hasattr(self, "clip_worker") and self.clip_worker is not None:
                def on_batch_clip_finished(success, cancelled, path, err, dur, has_aud, ff, src_aud):
                    if not cancelled:
                        self.batch_completed += 1
                        QTimer.singleShot(100, self.process_next_batch_item)
                    else:
                        self.in_batch_mode = False
                
                self.clip_worker.finished.disconnect()
                self.clip_worker.finished.connect(
                    lambda s, c, p, e, dur, has_aud, ff, src_aud: self._on_clip_extraction_finished(s, c, p, e, dur, has_aud, ff, src_aud)
                )
                self.clip_worker.finished.connect(on_batch_clip_finished)
                
        elif "Motion" in mode:
            self.extractMotion()
            if hasattr(self, "motion_worker") and self.motion_worker is not None:
                def on_batch_motion_finished(extracted, cancelled):
                    if not cancelled:
                        self.batch_completed += 1
                        QTimer.singleShot(100, self.process_next_batch_item)
                    else:
                        self.in_batch_mode = False
                self.motion_worker.finished.disconnect()
                self.motion_worker.finished.connect(self._on_motion_extraction_finished)
                self.motion_worker.finished.connect(on_batch_motion_finished)
                
        elif "GIF" in mode or "WebP" in mode:
            if state is None:
                self.gif_start_time.setTime(QTime(0,0,0,0))
                if self.current_metadata:
                    ms = self.current_metadata.get("DurationMs", 0)
                    self.gif_end_time.setTime(ms_to_qtime(ms))
                
            self.exportAnimation()
            
            if hasattr(self, "animation_worker") and self.animation_worker is not None:
                def on_batch_animation_finished(success, err, cancelled):
                    if not cancelled:
                        self.batch_completed += 1
                        QTimer.singleShot(100, self.process_next_batch_item)
                    else:
                        self.in_batch_mode = False
                self.animation_worker.finished.disconnect()
                self.animation_worker.finished.connect(self._on_animation_export_finished)
                self.animation_worker.finished.connect(on_batch_animation_finished)

        else:
            if state is None:
                self.extract_part_checkbox.setChecked(False)
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
    # Full-App Presets
    # -----------------------------

    def _update_preset_buttons(self):
        name = self.preset_combo.currentText()
        if name == "Custom..." or self.preset_manager.is_builtin(name):
            self.delete_preset_btn.setEnabled(False)
        else:
            self.delete_preset_btn.setEnabled(True)

    def on_preset_selected(self, name):
        if self._block_preset_signals:
            return
        
        self._update_preset_buttons()
        if name == "Custom...":
            return
            
        preset = self.preset_manager.get_preset(name)
        if preset:
            self.apply_app_state(preset)

    def save_custom_preset(self):
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Enter a name for this preset:"
        )
        if not ok or not name.strip():
            return
            
        name = name.strip()
        if self.preset_manager.is_builtin(name) or name == "Custom...":
            QMessageBox.warning(self, "Invalid Name", "You cannot overwrite built-in presets.")
            return

        state = self.gather_app_state()
        self.preset_manager.add_preset(name, state)
        
        self._block_preset_signals = True
        self.preset_combo.clear()
        self.preset_combo.addItems(self.preset_manager.get_all_preset_names())
        self.preset_combo.setCurrentText(name)
        self._block_preset_signals = False
        self._update_preset_buttons()

    def delete_custom_preset(self):
        name = self.preset_combo.currentText()
        if self.preset_manager.is_builtin(name) or name == "Custom...":
            return
            
        reply = QMessageBox.question(
            self, "Delete Preset", f"Are you sure you want to delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.preset_manager.remove_preset(name)
            self._block_preset_signals = True
            self.preset_combo.clear()
            self.preset_combo.addItems(self.preset_manager.get_all_preset_names())
            self.preset_combo.setCurrentText("Default")
            self._block_preset_signals = False
            self.apply_app_state(self.preset_manager.get_preset("Default"))

    def gather_app_state(self, is_custom_job=False):
        state = {
            "target_tab": self.tab_widget.currentIndex(),
            "format": self.format_combo.currentText(),
            "extraction_mode": self.extraction_mode_combo.currentText(),
            "custom_fps": self.custom_fps_spin.value(),
            "quality": self.quality_spinbox.value(),
            "filter_blur": self.filter_blur_checkbox.isChecked(),
            "extract_part": self.extract_part_checkbox.isChecked(),
            "motion_mode": self.motion_mode_combo.currentText(),
            "motion_sensitivity": self.motion_sensitivity_spin.value(),
            "motion_min_area": self.motion_min_area_spin.value(),
            "gif_format": self.gif_format_combo.currentText(),
            "gif_resolution": self.gif_resolution_combo.currentText(),
            "gif_fps": self.gif_fps_combo.currentText(),
            "gif_quality": self.gif_quality_spin.value(),
        }
        
        if is_custom_job:
            state["frame_start_time"] = qtime_to_ms(self.start_time_edit.time())
            state["frame_end_time"] = qtime_to_ms(self.end_time_edit.time())
            
            state["clip_start_frame"] = self.clip_start_frame_spinbox.value()
            state["clip_end_frame"] = self.clip_end_frame_spinbox.value()
            state["clip_start_time"] = qtime_to_ms(self.clip_start_time_edit.time())
            state["clip_end_time"] = qtime_to_ms(self.clip_end_time_edit.time())
            state["clip_format"] = self.clip_format_combo.currentText()
            state["clip_hw_accel"] = self.clip_hw_accel_combo.currentText()
            
            state["gif_start_ms"] = qtime_to_ms(self.gif_start_time.time())
            state["gif_end_ms"] = qtime_to_ms(self.gif_end_time.time())
            state["gif_dither"] = self.gif_dither_combo.currentText()
            state["gif_lossless"] = self.gif_lossless_checkbox.isChecked()
            state["gif_loop"] = self.gif_loop_combo.currentText()
            
        return state

    def apply_app_state(self, state):
        self._block_preset_signals = True
        
        if "target_tab" in state:
            self.tab_widget.setCurrentIndex(state["target_tab"])
            
        if "format" in state: self.format_combo.setCurrentText(state["format"])
        if "extraction_mode" in state: self.extraction_mode_combo.setCurrentText(state["extraction_mode"])
        if "custom_fps" in state: self.custom_fps_spin.setValue(state["custom_fps"])
        if "quality" in state: self.quality_spinbox.setValue(state["quality"])
        if "filter_blur" in state: self.filter_blur_checkbox.setChecked(state["filter_blur"])
        if "extract_part" in state: self.extract_part_checkbox.setChecked(state["extract_part"])
        if "motion_mode" in state: self.motion_mode_combo.setCurrentText(state["motion_mode"])
        if "motion_sensitivity" in state: self.motion_sensitivity_spin.setValue(state["motion_sensitivity"])
        if "motion_min_area" in state: self.motion_min_area_spin.setValue(state["motion_min_area"])
        if "gif_format" in state: self.gif_format_combo.setCurrentText(state["gif_format"])
        if "gif_resolution" in state: self.gif_resolution_combo.setCurrentText(state["gif_resolution"])
        if "gif_fps" in state: self.gif_fps_combo.setCurrentText(state["gif_fps"])
        if "gif_quality" in state: self.gif_quality_spin.setValue(state["gif_quality"])
        
        if "frame_start_time" in state: self.start_time_edit.setTime(ms_to_qtime(state["frame_start_time"]))
        if "frame_end_time" in state: self.end_time_edit.setTime(ms_to_qtime(state["frame_end_time"]))
        
        if "clip_start_frame" in state: self.clip_start_frame_spinbox.setValue(state["clip_start_frame"])
        if "clip_end_frame" in state: self.clip_end_frame_spinbox.setValue(state["clip_end_frame"])
        if "clip_start_time" in state: self.clip_start_time_edit.setTime(ms_to_qtime(state["clip_start_time"]))
        if "clip_end_time" in state: self.clip_end_time_edit.setTime(ms_to_qtime(state["clip_end_time"]))
        if "clip_format" in state: self.clip_format_combo.setCurrentText(state["clip_format"])
        if "clip_hw_accel" in state: self.clip_hw_accel_combo.setCurrentText(state["clip_hw_accel"])
        
        if "gif_start_ms" in state: self.gif_start_time.setTime(ms_to_qtime(state["gif_start_ms"]))
        if "gif_end_ms" in state: self.gif_end_time.setTime(ms_to_qtime(state["gif_end_ms"]))
        if "gif_dither" in state: self.gif_dither_combo.setCurrentText(state["gif_dither"])
        if "gif_lossless" in state: self.gif_lossless_checkbox.setChecked(state["gif_lossless"])
        if "gif_loop" in state: self.gif_loop_combo.setCurrentText(state["gif_loop"])
        
        self._block_preset_signals = False

    def mark_custom(self, *args, **kwargs):
        if self._block_preset_signals:
            return
        
        current = self.preset_combo.currentText()
        if current != "Custom...":
            self._block_preset_signals = True
            if self.preset_combo.findText("Custom...") == -1:
                self.preset_combo.insertItem(0, "Custom...")
            self.preset_combo.setCurrentText("Custom...")
            self._block_preset_signals = False
            self._update_preset_buttons()

    def _connect_preset_signals(self):
        widgets = [
            (self.format_combo, "currentTextChanged"),
            (self.extraction_mode_combo, "currentTextChanged"),
            (self.custom_fps_spin, "valueChanged"),
            (self.quality_spinbox, "valueChanged"),
            (self.filter_blur_checkbox, "toggled"),
            (self.extract_part_checkbox, "toggled"),
            (self.motion_mode_combo, "currentTextChanged"),
            (self.motion_sensitivity_spin, "valueChanged"),
            (self.motion_min_area_spin, "valueChanged"),
            (self.gif_format_combo, "currentTextChanged"),
            (self.gif_resolution_combo, "currentTextChanged"),
            (self.gif_fps_combo, "currentTextChanged"),
            (self.gif_quality_spin, "valueChanged"),
        ]
        
        for widget, signal_name in widgets:
            signal = getattr(widget, signal_name, None)
            if signal:
                signal.connect(self.mark_custom)

    # -----------------------------
    # Extra utility UI
    # -----------------------------

    def addUtilityControls(self):
        pass

