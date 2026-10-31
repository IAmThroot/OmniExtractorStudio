import copy
from functools import wraps
import gc
import os
import re
import shutil
import subprocess
import sys

from PyQt6.QtCore import QSettings, Qt, QTime, QTimer, QUrl, QSignalBlocker, QThread
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

from ..workers.base import JobOutcome, JobStatus
from .. import __version__
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
from ..media.hwaccel import get_hwaccel_encode_profile
from ..workers import (
    AnimatedExportWorker,
    ClipExtractionWorker,
    FrameExtractionWorker,
    InterpolationWorker,
    MetadataProbeWorker,
    probe_subtitle_tracks_for_video,
    MotionExtractionWorker,
    MultiSegmentWorker,
    SceneActionWorker,
    SceneDetectionWorker,
)
from ..ai.rife import (
    find_rife_model,
    is_rife_available,
    get_model_status_info,
    RIFE_ONNX_URL,
    RIFE_REPO_URL,
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
        self.job_state = "idle"
        self._active_worker = None
        self._live_workers = set()
        self._closing = False
        self._probe_generation = 0
        self._probes = set()
        self.in_batch_mode = False
        self._dispatching_batch = False
        self.batch_results = []
        self.last_extracted_paths = []
        self.source_file = ""
        self.save_dir = ""
        self.current_metadata = None
        self.frame_worker = None
        self.clip_worker = None
        self.motion_worker = None
        self.gif_worker = None
        self.interp_worker = None
        self.conv_worker = None
        self.multi_segment_worker = None
        self.scene_worker = None
        self.initUI()
        self.load_settings()
        self._refresh_job_controls()

        self.setAcceptDrops(True)

    def _message(self, kind, title, text):
        if self._closing:
            return
        if self.in_batch_mode:
            if kind in ('critical', 'warning'):
                self._batch_error = text
            return
        return getattr(QMessageBox, kind)(self, title, text)

    def _filename_renderer(self):
        template = self.filename_template_edit.text()
        return lambda number, timestamp, extension: util_render_filename(template, number, timestamp, extension)

    def _connect_worker_result(self, worker, callback):
        worker._ui_callbacks = getattr(worker, '_ui_callbacks', []) + [callback]

    def _start_worker(self, worker):
        if self._active_worker is not None:
            raise RuntimeError('A processing job is already running')
        self._active_worker = worker
        self._live_workers.add(worker)
        self.job_state = 'running'
        self.cancel_requested = False
        worker.native_finished().connect(lambda: self._worker_done(worker))
        self._refresh_job_controls()
        worker.start()

    def _worker_done(self, worker):
        self._live_workers.discard(worker)
        if self._active_worker is worker:
            self._active_worker = None
        self.job_state = 'completed'
        outcome = worker.outcome
        self.last_extracted_paths = list(outcome.paths)
        self.set_extracting_state(False)
        if not self._closing:
            if worker.result_args is not None:
                for callback in getattr(worker, '_ui_callbacks', []):
                    try:
                        callback(*worker.result_args)
                    except Exception as exc:
                        outcome = JobOutcome(JobStatus.FAILED, error=str(exc))
            if getattr(self, '_batch_error', '') and self.in_batch_mode:
                outcome = JobOutcome(JobStatus.FAILED, outcome.count, outcome.paths, self._batch_error)
            if outcome.status == JobStatus.FAILED:
                self.progress_label.setText('Failed: ' + outcome.error)
                self._message('critical', 'Processing Failed', outcome.error)
            self._refresh_job_controls()
            if self.in_batch_mode:
                self.batch_results.append((self._batch_job['path'], outcome))
                if outcome.status == JobStatus.CANCELLED:
                    self.cancel_requested = True
                QTimer.singleShot(0, self.process_next_batch_item)
        for attr in ('frame_worker', 'clip_worker', 'motion_worker', 'gif_worker', 'interp_worker',
                     'conv_worker', 'multi_segment_worker', 'scene_worker', 'scene_action_worker'):
            if getattr(self, attr, None) is worker:
                setattr(self, attr, None)
        worker.deleteLater()

    def _refresh_job_controls(self):
        busy = self._active_worker is not None or self.in_batch_mode or self._closing
        ready = bool(self.current_metadata)
        for name in ('extract_button', 'clip_extract_button', 'extract_segments_btn',
                     'motion_extract_button', 'gif_export_button', 'conv_start_btn', 'interp_start_btn',
                     'scene_detect_button', 'frame_queue_button', 'clip_queue_button', 'gif_queue_button',
                     'conv_queue_btn', 'interp_queue_btn'):
            button = getattr(self, name, None)
            if button:
                button.setEnabled(ready and not busy)
        for name in ('source_file_button', 'save_dir_button', 'batch_run_btn', 'preset_combo', 'source_file_edit', 'save_dir_edit'):
            widget = getattr(self, name, None)
            if widget:
                widget.setEnabled(not busy)
        for name in ('cancel_button', 'clip_cancel_button', 'motion_cancel_button', 'gif_cancel_button',
                     'conv_cancel_btn', 'interp_cancel_btn'):
            button = getattr(self, name, None)
            if button:
                button.setEnabled(self._active_worker is not None)

    def selectSaveDirFromDrop(self, path):
        """Handle folder drag and drop into save directory line edit."""
        if not path:
            return
        directory = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.isdir(directory):
            self.save_dir = directory
            self.save_dir_edit.setText(directory)
            self.settings.setValue("last_output_dir", directory)

    def initUI(self):
        self.setWindowTitle(f"OmniExtract Studio v{__version__}")
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
        
        self.interp_tab = QWidget()
        self.initInterpolationTab()
        self.tab_widget.addTab(self.interp_tab, "FPS & Interpolation")

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
        self.cancelExtraction()


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
        self.cancelExtraction()


    def initInterpolationTab(self):
        layout = QVBoxLayout()

        # 1. Source Video Properties Summary
        info_group = QGroupBox("Source Video Properties")
        info_layout = QHBoxLayout()
        self.interp_src_fps_label = QLabel("<b>Source FPS:</b> --")
        self.interp_src_duration_label = QLabel("<b>Duration:</b> --")
        self.interp_src_resolution_label = QLabel("<b>Resolution:</b> --")
        self.interp_src_frames_label = QLabel("<b>Frames:</b> --")
        info_layout.addWidget(self.interp_src_fps_label)
        info_layout.addWidget(self.interp_src_duration_label)
        info_layout.addWidget(self.interp_src_resolution_label)
        info_layout.addWidget(self.interp_src_frames_label)
        info_layout.addStretch()
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # 2. Sub-Tabs Widget: Framerate Conversion vs Frame Interpolation
        self.fps_subtab_widget = QTabWidget()

        # =========================================================
        # SUB-TAB 1: Framerate Conversion
        # =========================================================
        self.framerate_conv_subtab = QWidget()
        conv_layout = QVBoxLayout(self.framerate_conv_subtab)

        # Conversion Mode
        conv_mode_group = QGroupBox("Conversion Mode")
        conv_mode_layout = QVBoxLayout()

        self.conv_mode_combo = QComboBox()
        self.conv_mode_combo.addItems([
            "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
            "Frame Blending (Blend adjacent frames to smooth cadence)",
            "Conform Playback Speed (Change FPS interpretation; speeds up / slows down video)",
        ])
        self.conv_mode_combo.currentTextChanged.connect(self._on_conv_mode_changed)
        conv_mode_layout.addWidget(self.conv_mode_combo)

        self.conv_mode_desc_label = QLabel(
            "<i>Drops or duplicates frames to reach the target rate while keeping video playback speed and duration unchanged.</i>"
        )
        self.conv_mode_desc_label.setWordWrap(True)
        conv_mode_layout.addWidget(self.conv_mode_desc_label)
        conv_mode_group.setLayout(conv_mode_layout)
        conv_layout.addWidget(conv_mode_group)

        # Target Frame Rate for Conversion
        conv_fps_group = QGroupBox("Target Frame Rate")
        conv_fps_layout = QFormLayout()

        self.conv_fps_preset_combo = QComboBox()
        self.conv_fps_preset_combo.addItems([
            "24 FPS (Cinematic standard)",
            "25 FPS (PAL)",
            "29.97 FPS (NTSC)",
            "30 FPS (Standard web/mobile)",
            "50 FPS (PAL High)",
            "59.94 FPS (NTSC High)",
            "60 FPS (High smoothness)",
            "120 FPS",
            "0.5x Half Speed",
            "2x Double Speed",
            "Custom",
        ])
        self.conv_fps_preset_combo.setCurrentText("24 FPS (Cinematic standard)")
        self.conv_fps_preset_combo.currentTextChanged.connect(self._on_conv_preset_changed)
        conv_fps_layout.addRow("Preset:", self.conv_fps_preset_combo)

        self.conv_fps_spin = QDoubleSpinBox()
        self.conv_fps_spin.setRange(1.0, 360.0)
        self.conv_fps_spin.setValue(24.0)
        self.conv_fps_spin.setDecimals(3)
        self.conv_fps_spin.setSuffix(" FPS")
        conv_fps_layout.addRow("Target FPS:", self.conv_fps_spin)

        conv_fps_group.setLayout(conv_fps_layout)
        conv_layout.addWidget(conv_fps_group)

        # Output & Encoding for Conversion
        conv_enc_group = QGroupBox("Output & Encoding")
        conv_enc_layout = QFormLayout()

        self.conv_codec_combo = QComboBox()
        self.conv_codec_combo.addItems([
            "H.264 (Default - libx264)",
            "H.265 / HEVC (libx265)",
            "Hardware Accelerated (Auto)",
        ])
        conv_enc_layout.addRow("Video Codec:", self.conv_codec_combo)

        self.conv_crf_spin = QSpinBox()
        self.conv_crf_spin.setRange(0, 51)
        self.conv_crf_spin.setValue(20)
        self.conv_crf_spin.setToolTip("Lower means higher quality. 18-23 is visually transparent.")
        conv_enc_layout.addRow("Quality (CRF):", self.conv_crf_spin)

        self.conv_audio_combo = QComboBox()
        self.conv_audio_combo.addItems([
            "Keep Audio (Stream Copy)",
            "Mute / Strip Audio",
        ])
        conv_enc_layout.addRow("Audio Track:", self.conv_audio_combo)

        conv_enc_group.setLayout(conv_enc_layout)
        conv_layout.addWidget(conv_enc_group)

        # Progress & Actions for Conversion
        self.conv_progress_label = QLabel("Ready")
        self.conv_progress_bar = QProgressBar()
        self.conv_progress_bar.setRange(0, 100)

        conv_btn_layout = QHBoxLayout()
        self.conv_start_btn = QPushButton("Convert Framerate")
        self.conv_start_btn.clicked.connect(self.startFramerateConversion)

        self.conv_queue_btn = QPushButton("Send to Batch Queue")
        self.conv_queue_btn.clicked.connect(self.queueCurrentFramerateConversionJob)

        self.conv_cancel_btn = QPushButton("Cancel")
        self.conv_cancel_btn.clicked.connect(self.cancelFramerateConversion)
        self.conv_cancel_btn.setEnabled(False)

        conv_btn_layout.addWidget(self.conv_start_btn)
        conv_btn_layout.addWidget(self.conv_queue_btn)
        conv_btn_layout.addWidget(self.conv_cancel_btn)
        conv_btn_layout.addStretch()

        conv_layout.addWidget(self.conv_progress_label)
        conv_layout.addWidget(self.conv_progress_bar)
        conv_layout.addLayout(conv_btn_layout)
        conv_layout.addStretch()

        self.fps_subtab_widget.addTab(self.framerate_conv_subtab, "Framerate Conversion")

        # =========================================================
        # SUB-TAB 2: Frame Interpolation
        # =========================================================
        self.frame_interp_subtab = QWidget()
        interp_layout = QVBoxLayout(self.frame_interp_subtab)

        # Target Frame Rate for Interpolation
        interp_fps_group = QGroupBox("Target Frame Rate")
        interp_fps_layout = QFormLayout()

        self.interp_fps_preset_combo = QComboBox()
        self.interp_fps_preset_combo.addItems([
            "2x Source FPS",
            "4x Source FPS",
            "60 FPS (Smooth)",
            "120 FPS (High Refresh)",
            "144 FPS",
            "Custom",
        ])
        self.interp_fps_preset_combo.setCurrentText("60 FPS (Smooth)")
        self.interp_fps_preset_combo.currentTextChanged.connect(self._on_interp_preset_changed)
        interp_fps_layout.addRow("Preset:", self.interp_fps_preset_combo)

        self.interp_fps_spin = QDoubleSpinBox()
        self.interp_fps_spin.setRange(1.0, 360.0)
        self.interp_fps_spin.setValue(60.0)
        self.interp_fps_spin.setDecimals(3)
        self.interp_fps_spin.setSuffix(" FPS")
        self.interp_fps_spin.valueChanged.connect(self._on_interp_fps_spin_changed)
        interp_fps_layout.addRow("Target FPS:", self.interp_fps_spin)

        interp_fps_group.setLayout(interp_fps_layout)
        interp_layout.addWidget(interp_fps_group)

        # Interpolation Engine & Algorithm
        engine_group = QGroupBox("Interpolation Engine & Algorithm")
        engine_layout = QFormLayout()

        self.interp_engine_combo = QComboBox()
        self.interp_engine_combo.addItems([
            "FFmpeg Motion Interpolation (MCI)",
            "AI Neural Interpolation (RIFE ONNX)",
        ])
        self.interp_engine_combo.currentTextChanged.connect(self._on_interp_engine_changed)
        engine_layout.addRow("Engine:", self.interp_engine_combo)

        # Motion Compensated Options Container (MCI)
        self.interp_mci_container = QWidget()
        mci_layout = QFormLayout(self.interp_mci_container)
        mci_layout.setContentsMargins(0, 0, 0, 0)

        self.interp_mc_combo = QComboBox()
        self.interp_mc_combo.addItems([
            "aobmc (Adaptive Overlapped Block - Recommended)",
            "obmc (Overlapped Block)"
        ])
        mci_layout.addRow("Motion Compensation:", self.interp_mc_combo)

        self.interp_me_combo = QComboBox()
        self.interp_me_combo.addItems([
            "epzs (Fast Diamond Search)",
            "hexbs (Balanced Hexagonal)",
            "esa (Exhaustive High Quality)"
        ])
        mci_layout.addRow("Motion Estimation:", self.interp_me_combo)

        self.interp_scd_check = QCheckBox("Prevent morphing/ghosting across scene cuts")
        self.interp_scd_check.setChecked(True)
        self.interp_scd_check.toggled.connect(lambda checked: self.interp_scd_thresh_spin.setEnabled(checked))
        mci_layout.addRow("Scene Cut Guard:", self.interp_scd_check)

        self.interp_scd_thresh_spin = QDoubleSpinBox()
        self.interp_scd_thresh_spin.setRange(1.0, 100.0)
        self.interp_scd_thresh_spin.setValue(10.0)
        self.interp_scd_thresh_spin.setSuffix(" %")
        mci_layout.addRow("Cut Sensitivity:", self.interp_scd_thresh_spin)

        engine_layout.addRow(self.interp_mci_container)

        # RIFE AI Container
        self.interp_rife_container = QWidget()
        rife_layout = QVBoxLayout(self.interp_rife_container)
        rife_layout.setContentsMargins(0, 0, 0, 0)

        self.rife_status_label = QLabel()
        self.rife_status_label.setWordWrap(True)
        self._update_rife_status_display()
        rife_layout.addWidget(self.rife_status_label)

        rife_btn_layout = QHBoxLayout()
        self.rife_browse_btn = QPushButton("Select Custom Model...")
        self.rife_browse_btn.clicked.connect(self._browse_rife_model)
        self.rife_guide_btn = QPushButton("Model Download Instructions")
        self.rife_guide_btn.clicked.connect(self._show_rife_download_guide)
        rife_btn_layout.addWidget(self.rife_browse_btn)
        rife_btn_layout.addWidget(self.rife_guide_btn)
        rife_btn_layout.addStretch()
        rife_layout.addLayout(rife_btn_layout)

        self.interp_rife_container.setVisible(False)
        engine_layout.addRow(self.interp_rife_container)

        engine_group.setLayout(engine_layout)
        interp_layout.addWidget(engine_group)

        # Output & Encoding for Interpolation
        enc_group = QGroupBox("Output & Encoding")
        enc_layout = QFormLayout()

        self.interp_codec_combo = QComboBox()
        self.interp_codec_combo.addItems([
            "H.264 (Default - libx264)",
            "H.265 / HEVC (libx265)",
            "Hardware Accelerated (Auto)"
        ])
        enc_layout.addRow("Video Codec:", self.interp_codec_combo)

        self.interp_crf_spin = QSpinBox()
        self.interp_crf_spin.setRange(0, 51)
        self.interp_crf_spin.setValue(20)
        self.interp_crf_spin.setToolTip("Lower means higher quality. 18-23 is visually transparent.")
        enc_layout.addRow("Quality (CRF):", self.interp_crf_spin)

        self.interp_audio_combo = QComboBox()
        self.interp_audio_combo.addItems([
            "Keep Audio (Stream Copy)",
            "Mute / Strip Audio"
        ])
        enc_layout.addRow("Audio Track:", self.interp_audio_combo)

        enc_group.setLayout(enc_layout)
        interp_layout.addWidget(enc_group)

        # Progress & Actions for Interpolation
        self.interp_progress_label = QLabel("Ready")
        self.interp_progress_bar = QProgressBar()
        self.interp_progress_bar.setRange(0, 100)

        btn_layout = QHBoxLayout()
        self.interp_start_btn = QPushButton("Start Interpolation")
        self.interp_start_btn.clicked.connect(self.startInterpolation)

        self.interp_queue_btn = QPushButton("Send to Batch Queue")
        self.interp_queue_btn.clicked.connect(self.queueCurrentInterpolationJob)

        self.interp_cancel_btn = QPushButton("Cancel")
        self.interp_cancel_btn.clicked.connect(self.cancelInterpolation)
        self.interp_cancel_btn.setEnabled(False)

        btn_layout.addWidget(self.interp_start_btn)
        btn_layout.addWidget(self.interp_queue_btn)
        btn_layout.addWidget(self.interp_cancel_btn)
        btn_layout.addStretch()

        interp_layout.addWidget(self.interp_progress_label)
        interp_layout.addWidget(self.interp_progress_bar)
        interp_layout.addLayout(btn_layout)
        interp_layout.addStretch()

        self.fps_subtab_widget.addTab(self.frame_interp_subtab, "Frame Interpolation")

        layout.addWidget(self.fps_subtab_widget)
        self.interp_tab.setLayout(layout)

    def _on_conv_mode_changed(self, text):
        if hasattr(self, 'conv_audio_combo'):
            self.conv_audio_combo.setItemText(0, 'Keep Audio (Retimed AAC)' if 'Conform' in text else 'Keep Audio (Stream Copy)')
        if "Resample" in text:
            self.conv_mode_desc_label.setText(
                "<i>Drops or duplicates frames to reach the target rate while preserving video playback speed and duration.</i>"
            )
        elif "Blend" in text:
            self.conv_mode_desc_label.setText(
                "<i>Blends adjacent frames together to reach the target rate, softening judder for framerate reductions.</i>"
            )
        elif "Conform" in text:
            self.conv_mode_desc_label.setText(
                "<i>Changes playback speed without dropping or duplicating frames. Kept audio is retimed with pitch preservation and encoded as AAC.</i>"
            )

    def _on_conv_preset_changed(self, text):
        fps = 0.0
        if getattr(self, "current_metadata", None):
            fps = self.current_metadata.get("RawFPS", 0.0) or self.current_metadata.get("Fps", 0.0)
        if "24 FPS" in text:
            self.conv_fps_spin.setValue(24.0)
        elif "25 FPS" in text:
            self.conv_fps_spin.setValue(25.0)
        elif "29.97 FPS" in text:
            self.conv_fps_spin.setValue(29.970)
        elif "30 FPS" in text:
            self.conv_fps_spin.setValue(30.0)
        elif "50 FPS" in text:
            self.conv_fps_spin.setValue(50.0)
        elif "59.94 FPS" in text:
            self.conv_fps_spin.setValue(59.940)
        elif "60 FPS" in text:
            self.conv_fps_spin.setValue(60.0)
        elif "120 FPS" in text:
            self.conv_fps_spin.setValue(120.0)
        elif "0.5x" in text and fps > 0:
            self.conv_fps_spin.setValue(fps * 0.5)
        elif "2x" in text and fps > 0:
            self.conv_fps_spin.setValue(fps * 2.0)

    def _update_conv_target_fps(self):
        self._on_conv_preset_changed(self.conv_fps_preset_combo.currentText())

    def _update_rife_status_display(self):
        status_text, is_ready, model_path = get_model_status_info(getattr(self, "custom_rife_path", None))
        if is_ready:
            color = "#e67e22" if ("CPU Only" in status_text or "⚠" in status_text) else "#27ae60"
            self.rife_status_label.setText(
                f"<span style='color: {color};'><b>{status_text}</b></span>"
            )
        elif model_path:
            self.rife_status_label.setText(
                f"<span style='color: #e67e22;'><b>{status_text}</b></span>"
            )
        else:
            self.rife_status_label.setText(
                "<span style='color: #e67e22;'><b>⚠ Model Not Found:</b> Place <code>RIFE_fp32.onnx</code> in <code>assets/models/</code> or click Browse.</span>"
            )

    def _browse_rife_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Interpolation Model", "",
            "ONNX Models (*.onnx);;All Files (*)"
        )
        if path and os.path.isfile(path):
            self.custom_rife_path = path
            self._update_rife_status_display()

    def _show_rife_download_guide(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("AI Interpolation Models Download Guide")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<h3>AI Frame Interpolation Models</h3>"
            "<p>For high-quality neural frame interpolation, download the pre-converted ONNX model:</p>"
            "<h4>RIFE FP32 ONNX (<code>RIFE_fp32.onnx</code> - Recommended)</h4>"
            "<ul>"
            f"<li><b>Direct Download:</b> <br><a href='{RIFE_ONNX_URL}'>{RIFE_ONNX_URL}</a></li>"
            f"<li><b>Hugging Face Repository:</b> <br><a href='{RIFE_REPO_URL}'>{RIFE_REPO_URL}</a></li>"
            "</ul>"
            "<p><b>Where to place:</b> Save into <code>assets/models/RIFE_fp32.onnx</code> or use the <b>Select Custom Model</b> button.</p>"
            "<p><i>Runs natively via the built-in ONNX Runtime with zero PyTorch dependencies. Alternatively, you can use <b>FFmpeg Motion Interpolation (MCI)</b> which runs out of the box with zero external model downloads.</i></p>"
        )
        msg.exec()

    def _on_interp_engine_changed(self, text):
        is_mci = ("MCI" in text)
        is_rife = ("RIFE" in text or "FILM" in text)
        self.interp_mci_container.setVisible(is_mci)
        self.interp_rife_container.setVisible(is_rife)

    def _on_interp_preset_changed(self, text):
        fps = 0.0
        if getattr(self, "current_metadata", None):
            fps = self.current_metadata.get("RawFPS", 0.0) or self.current_metadata.get("Fps", 0.0)
        if "2x" in text and fps > 0:
            self.interp_fps_spin.setValue(fps * 2.0)
        elif "4x" in text and fps > 0:
            self.interp_fps_spin.setValue(fps * 4.0)
        elif "60 FPS" in text:
            self.interp_fps_spin.setValue(60.0)
        elif "30 FPS" in text:
            self.interp_fps_spin.setValue(30.0)
        elif "24 FPS" in text:
            self.interp_fps_spin.setValue(24.0)
        elif "120 FPS" in text:
            self.interp_fps_spin.setValue(120.0)
        elif "144 FPS" in text:
            self.interp_fps_spin.setValue(144.0)

    def _on_interp_fps_spin_changed(self, val):
        pass

    def _update_interp_target_fps(self):
        self._on_interp_preset_changed(self.interp_fps_preset_combo.currentText())

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

    def load_video_path(self, path, async_probe=True):
        if self._active_worker is not None or (self.in_batch_mode and not self._dispatching_batch):
            return
        if not path or not os.path.isfile(path):
            return
        self.source_file = path
        self.source_file_edit.setText(path)
        self.update_video_metadata(async_probe=async_probe)

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
            self.load_video_path(file_name, async_probe=True)

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

    def update_video_metadata(self, async_probe=False):
        if not hasattr(self, "source_file") or not self.source_file or not os.path.isfile(self.source_file):
            return

        self._probe_generation += 1
        generation = self._probe_generation
        self.current_metadata = None
        self.subtitle_tracks = []
        self.preview_button.setEnabled(False)
        self.info_button.setEnabled(False)
        self._populate_subtitle_combos()
        self._refresh_job_controls()
        for worker in list(self._probes):
            worker.cancel()
        if async_probe:
            worker = MetadataProbeWorker(self.source_file, getattr(self, 'external_subtitle_file', ''))
            self._probe_worker = worker
            self._probes.add(worker)
            worker.metadata_ready.connect(lambda *args: self._on_metadata_probed(*args) if generation == self._probe_generation and not self._closing else None)
            worker.error.connect(lambda *args: self._on_metadata_probe_error(*args) if generation == self._probe_generation and not self._closing else None)
            worker.finished.connect(lambda: (self._probes.discard(worker), worker.deleteLater()))
            worker.start()
            return

        # Synchronous execution (for batch mode or explicit synchronous calls)
        metadata = probe_video_metadata(self.source_file)
        if not metadata:
            self._on_metadata_probe_error(self.source_file, "Unable to open video")
            return
        tracks, external_sub = probe_subtitle_tracks_for_video(
            self.source_file, getattr(self, "external_subtitle_file", "")
        )
        self._on_metadata_probed(self.source_file, metadata, tracks, external_sub)

    def _on_metadata_probe_error(self, video_path, err_msg):
        if video_path != self.source_file:
            return
        self.current_metadata = None
        self.subtitle_tracks = []
        self._refresh_job_controls()
        self.preview_button.setEnabled(False)
        self.info_button.setEnabled(False)
        self._message("warning", "Unable to Open Video",
            "The selected file could not be opened as a video."
        )

    def _on_metadata_probed(self, video_path, metadata, subtitle_tracks, external_sub):
        if video_path != getattr(self, "source_file", None):
            return
        self.current_metadata = metadata
        self.subtitle_tracks = subtitle_tracks
        self.external_subtitle_file = external_sub

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
        
        if hasattr(self, "interp_src_fps_label"):
            fps = self.current_metadata.get("RawFPS", 0.0) or self.current_metadata.get("Fps", 0.0)
            self.interp_src_fps_label.setText(
                f"<b>Source FPS:</b> {fps:.2f}" if fps > 0 else "<b>Source FPS:</b> Unknown"
            )
            self.interp_src_duration_label.setText(f"<b>Duration:</b> {duration_text(duration_ms)}")
            w = self.current_metadata.get("Width", 0)
            h = self.current_metadata.get("Height", 0)
            self.interp_src_resolution_label.setText(f"<b>Resolution:</b> {w}x{h}")
            self.interp_src_frames_label.setText(f"<b>Frames:</b> {frame_count:,}")
            if hasattr(self, "_update_conv_target_fps"):
                self._update_conv_target_fps()
            self._update_interp_target_fps()

        self._populate_subtitle_combos(select_external=bool(self.external_subtitle_file))
        self._refresh_job_controls()

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
        """Detect embedded subtitle streams and check for external .srt/.vtt files."""
        if selected_external:
            self.external_subtitle_file = selected_external
        self.subtitle_tracks, self.external_subtitle_file = probe_subtitle_tracks_for_video(
            getattr(self, "source_file", ""), getattr(self, "external_subtitle_file", "")
        )
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
            self._message("warning", "No Source File",
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
        self._closing = True
        self.cancel_requested = True
        self.job_state = 'cancelling'
        for worker in list(self._live_workers) + list(self._probes):
            worker.cancel()
        if any(w.isRunning() for w in self._live_workers | self._probes):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
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

        if mode == "Custom FPS":
            target_fps = self.custom_fps_spin.value()
        elif mode.endswith(" FPS"):
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
        self.cancel_requested = True
        self.job_state = 'cancelling'
        if self._active_worker:
            self._active_worker.cancel()
        self.progress_label.setText('Cancelling...')

    def set_extracting_state(self, active):
        self.extracting = active
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
            self._message("warning", "No Video", "Please load a video first.")
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
            
        self._message("information", "Queued", f"Added {name} to Batch Queue with current frame settings.")

    def extractFrames(self):
        if not self.source_file or not self.save_dir:
            self._message("warning", "Missing Information",
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
            self._message("warning", "Unable to Open Video",
                "No video metadata available. Please reload the source file."
            )
            return

        fps = self.current_metadata.get("RawFPS", 0)
        frame_count = self.current_metadata.get("RawFrames", 0)

        if not fps or fps <= 0 or frame_count <= 0:
            self._message("warning", "Invalid Video",
                "Could not determine the video's frame rate or frame count."
            )
            return

        start_frame = 0
        end_frame = frame_count

        if self.extract_part_checkbox.isChecked():
            start_ms = qtime_to_ms(self.start_time_edit.time())
            end_ms = qtime_to_ms(self.end_time_edit.time())

            if end_ms <= start_ms:
                self._message("warning", "Invalid Time Range",
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
            self._filename_renderer(), self.unique_path,
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
            lambda msg: self._message("warning", "Extraction Error", msg)
        )
        self._connect_worker_result(self.frame_worker, self._on_frame_extraction_finished)
        self._start_worker(self.frame_worker)

    def _update_frame_progress(self, percent, label):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(label)
        
    def _on_frame_extraction_finished(self, extracted, elapsed, cancelled):
        self.set_extracting_state(False)
        self.open_output_button.setEnabled(True)
        self.contact_sheet_button.setEnabled(True)
        
            

        if self.frame_worker and self.frame_worker.outcome.status == JobStatus.FAILED:
            self.progress_label.setText(f'Failed — {extracted:,} frames saved.')
            return
        if cancelled:
            self.progress_label.setText(f"Cancelled — {extracted:,} frames saved.")
            self._message("information", "Extraction Cancelled",
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
            
        self._message("information", "Extraction Complete",
            f"Done!\n\nSuccessfully extracted {extracted:,} images.\nTime: {elapsed:.2f} seconds."
        )

    # -----------------------------
    # Clip extraction
    # -----------------------------

    def _get_current_fps(self) -> float:
        """Safely retrieve video FPS as float from metadata without raising ValueError."""
        if not self.current_metadata:
            return 0.0
        fps = self.current_metadata.get("RawFPS") or self.current_metadata.get("Fps")
        if isinstance(fps, (int, float)) and fps > 0:
            return float(fps)
        raw_val = self.current_metadata.get("FPS", "")
        if isinstance(raw_val, (int, float)):
            return float(raw_val) if raw_val > 0 else 0.0
        if isinstance(raw_val, str):
            cleaned = raw_val.replace("FPS", "").strip()
            try:
                val = float(cleaned)
                return val if val > 0 else 0.0
            except ValueError:
                return 0.0
        return 0.0

    def _resolve_clip_video_args(self, hw_accel):
        """Resolve FFmpeg video-encode args (excluding -pix_fmt) for the clip workflow.

        Centralizes the hardware-acceleration mapping so the subtitle-burn
        re-encode path and both clip exporters stay in sync. The underlying
        capability probe is cached, so this is cheap to call repeatedly.
        """
        fallback = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        if hw_accel == "Auto":
            profile = list(get_hwaccel_encode_profile(preset="medium", crf=18).get("video_args", fallback))
            # The caller appends a single -pix_fmt, so drop the profile's own.
            if "-pix_fmt" in profile:
                idx = profile.index("-pix_fmt")
                del profile[idx:idx + 2]
            return profile
        if hw_accel.startswith("NVIDIA"):
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-cq", "19"]
        if hw_accel.startswith("Intel"):
            return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "19"]
        if hw_accel.startswith("macOS"):
            return ["-c:v", "h264_videotoolbox", "-q:v", "65"]
        return fallback

    def sync_clip_time_to_frame(self):
        fps = self._get_current_fps()
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
        fps = self._get_current_fps()
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
            self._message("warning", "No Video", "Please load a video first.")
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
        
            
        self._message("information", "Queued", f"Added {name} to Batch Queue with current clip settings.")

    def extractClip(self):
        """Extract the selected range with FFmpeg and explicitly include source audio."""
        if not self.source_file or not self.save_dir:
            self._message("warning", "Missing Information",
                                "Please select a source file and save directory first.")
            return

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self._message("critical", "FFmpeg/FFprobe Not Found",
                "FFmpeg and FFprobe are required for clip extraction.\n\n"
                "Check with: ffmpeg -version && ffprobe -version"
            )
            return

        if not self.current_metadata:
            self._message("warning", "Unable to Open Video",
                                "No video metadata available. Please reload the source file.")
            return
        fps = self.current_metadata.get("RawFPS", 0)
        frame_count = self.current_metadata.get("RawFrames", 0)

        start_frame = self.clip_start_frame_spinbox.value()
        end_frame = self.clip_end_frame_spinbox.value()
        if not fps or fps <= 0 or end_frame <= start_frame:
            self._message("warning", "Invalid Range",
                                "Please choose a valid start/end frame range.")
            return

        if frame_count > 0:
            start_frame = max(0, min(start_frame, frame_count - 1))
            end_frame = max(start_frame + 1, min(end_frame, frame_count))
        if end_frame <= start_frame:
            self._message("warning", "Invalid Range", "The selected range is outside the video's frame range.")
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
            capture_output=True, text=True, timeout=10
        )
        source_audio_info = probe.stdout.strip()
        has_audio = bool(source_audio_info)

        hw_accel = self.clip_hw_accel_combo.currentText()
        if selected_format == "COPY (LOSSLESS)":
            video_args = ["-c:v", "copy"]
            audio_args = ["-c:a", "copy"]
            mux_args = []
        elif selected_format == "AVI":
            video_args = ["-c:v", "mpeg4", "-q:v", "3"]
            audio_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = []
        else:
            video_args = self._resolve_clip_video_args(hw_accel)
                
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
                        cmd.pop(cv_idx + 1)
                        cmd.pop(cv_idx)
                        cmd.extend(self._resolve_clip_video_args(hw_accel) + ["-pix_fmt", "yuv420p"])
        
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
        self._connect_worker_result(self.clip_worker,
            lambda s, c, p, e: self._on_clip_extraction_finished(s, c, p, e, duration_ms, has_audio, ffprobe, source_audio_info)
        )
        self._start_worker(self.clip_worker)

    def _update_clip_progress(self, percent, label):
        self.clip_progress_bar.setValue(percent)
        self.progress_label.setText(label)
        
    def _on_clip_extraction_finished(self, success, cancelled, clip_path, error_msg, duration_ms, has_audio, ffprobe, source_audio_info):
        self.set_extracting_state(False)
        
        if getattr(self, "_clip_temp_srt", None) and os.path.exists(self._clip_temp_srt):
            try: os.remove(self._clip_temp_srt)
            except OSError: pass
            self._clip_temp_srt = None
            
            

        if cancelled:
            if os.path.exists(clip_path):
                try: os.remove(clip_path)
                except OSError: pass
            self.clip_progress_bar.setValue(0)
            self._message("information", "Clip Extraction Cancelled",
                                    "Clip extraction was cancelled and the partial file was removed.")
            return

        if not success:
            if os.path.exists(clip_path):
                try: os.remove(clip_path)
                except OSError: pass
            self.clip_progress_bar.setValue(0)
            self._message("critical", "FFmpeg Error",
                                 "FFmpeg could not create the clip.\n\n" + (error_msg or "No additional error."))
            return

        # Verify the FINAL FILE. This is deliberately strict: if the source
        # had audio, a video-only output is considered a failed extraction.
        verify = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,profile,sample_rate,channels",
             "-of", "default=noprint_wrappers=1", clip_path],
            capture_output=True, text=True, timeout=10
        )
        final_audio_info = verify.stdout.strip()
        final_has_audio = bool(final_audio_info)

        if has_audio and not final_has_audio:
            try: os.remove(clip_path)
            except OSError: pass
            self.clip_progress_bar.setValue(0)
            self._message("critical", "Audio Stream Missing",
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
        self._message("information", "Clip Extraction Complete",
            f"Done!\n\nClip duration: {format_timestamp(duration_ms)}\n"
            f"Audio: {audio_status}\n\n"
            f"Saved to:\n{clip_path}"
        )

    # -----------------------------
    # Multi-Segment & Chapter Cutting Methods
    # -----------------------------

    def addCurrentRangeToSegments(self):
        if not hasattr(self, "source_file"):
            self._message("warning", "No Source File", "Please select a source video first.")
            return
        start_ms = qtime_to_ms(self.clip_start_time_edit.time())
        end_ms = qtime_to_ms(self.clip_end_time_edit.time())
        if end_ms <= start_ms:
            self._message("warning", "Invalid Range", "End Time must be greater than Start Time.")
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
            self._message("warning", "No Source File", "Please select a source video first.")
            return
        chapters = probe_video_chapters(self.source_file)
        if not chapters:
            self._message("information", "No Chapters", "No embedded chapters found in this video.")
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
            
        self._message("information", "Chapters Imported", f"Imported {len(chapters)} chapters into the segments list.")

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
        if not self.source_file or not self.save_dir:
            self._message("warning", "Missing Information", "Please select a source file and save directory first.")
            return
            
        row_count = self.segments_table.rowCount()
        if row_count == 0:
            self._message("warning", "No Segments", "No segments in the list. Use '+ Add Current Range' or 'Import Chapters' first.")
            return
            
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self._message("critical", "FFmpeg Not Found", "FFmpeg is required for segment cutting.")
            return

        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index,codec_name",
             "-of", "default=noprint_wrappers=1", self.source_file],
            capture_output=True, text=True, timeout=10
        )
        has_audio = bool(probe.stdout.strip())

        selected_format = self.clip_format_combo.currentText().upper()
        hw_accel = self.clip_hw_accel_combo.currentText()
        if selected_format == "COPY (LOSSLESS)":
            video_args = ["-c:v", "copy"]
            audio_args = ["-c:a", "copy"]
            mux_args = []
        elif selected_format == "AVI":
            video_args = ["-c:v", "mpeg4", "-q:v", "3"]
            audio_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            mux_args = []
        else:
            video_args = self._resolve_clip_video_args(hw_accel)
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
                            seg_cmd.pop(cv_idx + 1)
                            seg_cmd.pop(cv_idx)
                            seg_cmd.extend(self._resolve_clip_video_args(hw_accel) + ["-pix_fmt", "yuv420p"])
                            
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
        self._connect_worker_result(self.multi_segment_worker, self._on_multi_segment_finished)
        self._start_worker(self.multi_segment_worker)

    def _update_multi_segment_progress(self, percent, label):
        self.clip_progress_bar.setValue(percent)
        self.progress_label.setText(label)

    def _on_multi_segment_finished(self, success, count, paths, message):
        self.set_extracting_state(False)
        self.extract_segments_btn.setEnabled(True)
        
            
        if success:
            self.clip_progress_bar.setValue(100)
            self.progress_label.setText(f"Extracted {count} segments successfully.")
            self._message("information", "Segments Extraction Complete",
                f"Successfully extracted {count} segments to:\n{self.save_dir}"
            )
        else:
            self.clip_progress_bar.setValue(0)
            self.progress_label.setText("Segment extraction cancelled or failed.")
            if message != "Cancelled":
                self._message("critical", "Error", message)

    # -----------------------------
    # Motion Extraction Logic
    # -----------------------------
    
    def extractMotion(self):
        if not self.source_file or not self.save_dir:
            self._message("warning", "Missing Information", "Please select a source file and save directory first.")
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
                self._message("warning", "Missing Dependency", "YOLOv8 ONNX mode requires the 'onnxruntime' package.\n\nPlease run: pip install onnxruntime")
                self.motion_extract_button.setEnabled(True)
                self.motion_cancel_button.setEnabled(False)
                return
                
            model_path = get_resource_path("assets/models/yolov8n.onnx")
            if not os.path.exists(model_path):
                self._message("warning", "Missing Model", "Could not find 'yolov8n.onnx' in assets/models/")
                self.motion_extract_button.setEnabled(True)
                self.motion_cancel_button.setEnabled(False)
                return

        self.motion_worker = MotionExtractionWorker(
            self.source_file, self.save_dir, ext, mode,
            sensitivity, min_area, cooldown,
            self.unique_path, self._filename_renderer(),
            engine, yolo_conf
        )
        self.motion_worker.progress.connect(self._update_motion_progress)
        self._connect_worker_result(self.motion_worker, self._on_motion_finished)
        self._start_worker(self.motion_worker)
        
    def _update_motion_progress(self, pct, label):
        self.motion_progress_bar.setValue(pct)
        self.motion_progress_label.setText(label)
        
    def _on_motion_finished(self, success, count, msg):
        self.motion_extract_button.setEnabled(True)
        self.motion_cancel_button.setEnabled(False)
        
            
        if success:
            self.motion_progress_bar.setValue(100)
            self.motion_progress_label.setText("Complete!")
            self._message("information", "Success", f"Extracted {count} motion events.\n{msg}")
        else:
            self.motion_progress_bar.setValue(0)
            self.motion_progress_label.setText("Failed or cancelled.")
            if msg != "Cancelled":
                self._message("critical", "Error", msg)

    # -----------------------------
    # GIF / WebP export logic
    # -----------------------------

    def queueCurrentAnimationJob(self):
        if not getattr(self, "source_file", None):
            self._message("warning", "No Video", "Please load a video first.")
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
        
            
        self._message("information", "Queued", f"Added {name} to Batch Queue with current animation settings.")

    def exportAnimation(self):
        if not hasattr(self, "source_file"):
            self._message("warning", "No Source File", "Select a video first.")
            return

        start_ms = qtime_to_ms(self.gif_start_time.time())
        end_ms = qtime_to_ms(self.gif_end_time.time())

        if end_ms <= start_ms:
            self._message("warning", "Invalid Range", "End time must be after start time.")
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
        self._connect_worker_result(self.gif_worker, self._on_gif_export_finished)
        self._start_worker(self.gif_worker)

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
            self._message("critical", "Export Failed", message or "An unknown error occurred.")
            return

        self.gif_progress_bar.setValue(100)
        self.gif_progress_label.setText(f"Done — {message}")
        self._message("information", "Export Complete",
            f"Animation saved successfully.\n\n{message}\n\nSaved to:\n{output_path}"
        )

    # -----------------------------
    # Framerate Conversion Actions
    # -----------------------------

    def startFramerateConversion(self):
        if not hasattr(self, "source_file") or not self.source_file:
            self._message("warning", "No Source Video", "Please select a source video first.")
            return

        target_fps = self.conv_fps_spin.value()
        mode_text = self.conv_mode_combo.currentText()
        if "Resample" in mode_text:
            engine = "resample"
            mi_mode = "mci"
        elif "Blend" in mode_text:
            engine = "ffmpeg"
            mi_mode = "blend"
        elif "Conform" in mode_text:
            engine = "conform"
            mi_mode = "mci"
        else:
            engine = "resample"
            mi_mode = "mci"

        codec_text = self.conv_codec_combo.currentText()
        hwaccel_enabled = ("Hardware" in codec_text)
        if "H.265" in codec_text:
            video_codec = "libx265"
        else:
            video_codec = "libx264"

        quality_crf = self.conv_crf_spin.value()
        audio_mode = "mute" if "Mute" in self.conv_audio_combo.currentText() else "copy"

        base_name = os.path.splitext(os.path.basename(self.source_file))[0]
        default_out = f"{base_name}_{int(target_fps)}fps.mp4"

        if getattr(self, "in_batch_mode", False) and hasattr(self, "save_dir"):
            save_path = os.path.join(self.save_dir, default_out)
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(self.save_dir, f"{base_name}_{int(target_fps)}fps_{counter}.mp4")
                counter += 1
        else:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Converted Video",
                os.path.join(getattr(self, "save_dir", ""), default_out),
                "MP4 Video (*.mp4);;MKV Video (*.mkv);;All Files (*)"
            )
            if not save_path:
                return

        duration_ms = self.current_metadata.get("DurationMs", 0) if getattr(self, "current_metadata", None) else 0
        src_fps = 0.0
        if getattr(self, "current_metadata", None):
            src_fps = self.current_metadata.get("RawFPS", 0.0) or self.current_metadata.get("Fps", 0.0)

        self.conv_start_btn.setEnabled(False)
        self.conv_cancel_btn.setEnabled(True)
        self.conv_progress_bar.setValue(0)
        self.conv_progress_label.setText("Starting framerate conversion...")

        self.conv_worker = InterpolationWorker(
            source_file=self.source_file,
            output_path=save_path,
            target_fps=target_fps,
            duration_ms=duration_ms,
            source_fps=src_fps,
            engine=engine,
            mi_mode=mi_mode,
            mc_mode="aobmc",
            me_algo="epzs",
            scd_enabled=True,
            scd_threshold=10.0,
            video_codec=video_codec,
            quality_crf=quality_crf,
            hwaccel_enabled=hwaccel_enabled,
            audio_mode=audio_mode,
        )
        self.conv_worker.progress.connect(self._on_conv_progress)
        self._connect_worker_result(self.conv_worker, self._on_conv_finished)
        self._start_worker(self.conv_worker)

    def cancelFramerateConversion(self):
        self.cancelExtraction()


    def _on_conv_progress(self, percent, message):
        self.conv_progress_bar.setValue(percent)
        self.conv_progress_label.setText(message)

    def _on_conv_finished(self, success, cancelled, path, error):
        self.conv_start_btn.setEnabled(True)
        self.conv_cancel_btn.setEnabled(False)

        if cancelled:
            self.conv_progress_label.setText("Framerate conversion cancelled.")
            if not getattr(self, "in_batch_mode", False):
                self._message("information", "Cancelled", "Framerate conversion process was cancelled.")
        elif success:
            self.conv_progress_label.setText(f"Finished: {os.path.basename(path)}")
            if not getattr(self, "in_batch_mode", False):
                self._message("information", "Complete",
                    f"Video framerate converted successfully!\n\nSaved to:\n{path}"
                )
        else:
            self.conv_progress_label.setText("Error during framerate conversion.")
            if not getattr(self, "in_batch_mode", False):
                self._message("critical", "Conversion Failed",
                    f"An error occurred during framerate conversion:\n\n{error or 'Unknown error'}"
                )

    def queueCurrentFramerateConversionJob(self):
        if not getattr(self, "source_file", None):
            self._message("warning", "No Video", "Please load a video first.")
            return

        state = self.gather_app_state(is_custom_job=True)
        state["target_tab"] = 4
        state["fps_subtab"] = 0

        name = os.path.basename(self.source_file)
        target_fps = self.conv_fps_spin.value()
        item = QListWidgetItem(f"{name} [Convert to {target_fps:.0f} FPS]")
        item.setData(Qt.ItemDataRole.UserRole, {
            "path": self.source_file,
            "state": state,
            "mode": "Framerate Conversion"
        })
        self.batch_list.addItem(item)


        self._message("information", "Queued", f"Added {name} to Batch Queue with current framerate conversion settings.")

    # -----------------------------
    # Frame Interpolation Actions
    # -----------------------------

    def startInterpolation(self):
        if not hasattr(self, "source_file") or not self.source_file:
            self._message("warning", "No Source Video", "Please select a source video first.")
            return

        target_fps = self.interp_fps_spin.value()
        engine_text = self.interp_engine_combo.currentText()
        if "RIFE" in engine_text or "FILM" in engine_text:
            status_text, is_ready, model_path = get_model_status_info(getattr(self, "custom_rife_path", None))
            if not model_path:
                self._message("warning", "AI Model Missing",
                    "Interpolation model was not found.\n\n"
                    "Please place 'RIFE_fp32.onnx' in 'assets/models/' or click 'Select Custom Model'."
                )
                return
            if not is_ready:
                self._message("warning", "AI Model Environment Not Ready",
                    f"{status_text}\n\n"
                    "Tip: You can use 'FFmpeg Motion Interpolation (MCI)' to interpolate frames right now without any additional dependencies!"
                )
                return
            engine = "rife"
            mi_mode = "mci"
        else:
            engine = "ffmpeg"
            mi_mode = "mci"

        mc_mode = "aobmc" if "aobmc" in self.interp_mc_combo.currentText() else "obmc"
        me_algo = "epzs"
        if "hexbs" in self.interp_me_combo.currentText():
            me_algo = "hexbs"
        elif "esa" in self.interp_me_combo.currentText():
            me_algo = "esa"

        scd_enabled = self.interp_scd_check.isChecked()
        scd_threshold = self.interp_scd_thresh_spin.value()

        codec_text = self.interp_codec_combo.currentText()
        hwaccel_enabled = ("Hardware" in codec_text)
        if "H.265" in codec_text:
            video_codec = "libx265"
        else:
            video_codec = "libx264"

        quality_crf = self.interp_crf_spin.value()
        audio_mode = "mute" if "Mute" in self.interp_audio_combo.currentText() else "copy"

        base_name = os.path.splitext(os.path.basename(self.source_file))[0]
        default_out = f"{base_name}_{int(target_fps)}fps_interpolated.mp4"

        if getattr(self, "in_batch_mode", False) and hasattr(self, "save_dir"):
            save_path = os.path.join(self.save_dir, default_out)
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(self.save_dir, f"{base_name}_{int(target_fps)}fps_interpolated_{counter}.mp4")
                counter += 1
        else:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Interpolated Video",
                os.path.join(getattr(self, "save_dir", ""), default_out),
                "MP4 Video (*.mp4);;MKV Video (*.mkv);;All Files (*)"
            )
            if not save_path:
                return

        duration_ms = self.current_metadata.get("DurationMs", 0) if getattr(self, "current_metadata", None) else 0
        src_fps = 0.0
        if getattr(self, "current_metadata", None):
            src_fps = self.current_metadata.get("RawFPS", 0.0) or self.current_metadata.get("Fps", 0.0)

        self.interp_start_btn.setEnabled(False)
        self.interp_cancel_btn.setEnabled(True)
        self.interp_progress_bar.setValue(0)
        self.interp_progress_label.setText("Starting interpolation...")

        self.interp_worker = InterpolationWorker(
            source_file=self.source_file,
            output_path=save_path,
            target_fps=target_fps,
            duration_ms=duration_ms,
            source_fps=src_fps,
            engine=engine,
            mi_mode=mi_mode,
            mc_mode=mc_mode,
            me_algo=me_algo,
            scd_enabled=scd_enabled,
            scd_threshold=scd_threshold,
            video_codec=video_codec,
            quality_crf=quality_crf,
            hwaccel_enabled=hwaccel_enabled,
            audio_mode=audio_mode,
            rife_model_path=getattr(self, "custom_rife_path", None),
        )
        self.interp_worker.progress.connect(self._on_interpolation_progress)
        self._connect_worker_result(self.interp_worker, self._on_interpolation_finished)
        self._start_worker(self.interp_worker)

    def cancelInterpolation(self):
        self.cancelExtraction()


    def _on_interpolation_progress(self, percent, message):
        self.interp_progress_bar.setValue(percent)
        self.interp_progress_label.setText(message)

    def _on_interpolation_finished(self, success, cancelled, path, error):
        self.interp_start_btn.setEnabled(True)
        self.interp_cancel_btn.setEnabled(False)

        if cancelled:
            self.interp_progress_label.setText("Interpolation cancelled.")
            if not getattr(self, "in_batch_mode", False):
                self._message("information", "Cancelled", "Interpolation process was cancelled.")
        elif success:
            self.interp_progress_label.setText(f"Finished: {os.path.basename(path)}")
            if not getattr(self, "in_batch_mode", False):
                self._message("information", "Complete",
                    f"Video successfully interpolated!\n\nSaved to:\n{path}"
                )
        else:
            self.interp_progress_label.setText("Error during interpolation.")
            if not getattr(self, "in_batch_mode", False):
                self._message("critical", "Interpolation Failed",
                    f"An error occurred during interpolation:\n\n{error or 'Unknown error'}"
                )

    def queueCurrentInterpolationJob(self):
        if not getattr(self, "source_file", None):
            self._message("warning", "No Video", "Please load a video first.")
            return

        state = self.gather_app_state(is_custom_job=True)
        state["target_tab"] = 4
        state["fps_subtab"] = 1

        name = os.path.basename(self.source_file)
        target_fps = self.interp_fps_spin.value()
        item = QListWidgetItem(f"{name} [Interpolate to {target_fps:.0f} FPS]")
        item.setData(Qt.ItemDataRole.UserRole, {
            "path": self.source_file,
            "state": state,
            "mode": "Frame Interpolation"
        })
        self.batch_list.addItem(item)


        self._message("information", "Queued", f"Added {name} to Batch Queue with current interpolation settings.")

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
            self._message("warning", "No Source File",
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
            lambda msg: self._message("warning", "Scene Detection Error", msg)
        )
        self._connect_worker_result(self.scene_worker, self._on_scene_detection_finished)
        self._start_worker(self.scene_worker)

    def _on_scene_detection_finished(self, scenes, frame_count, fps):
        self.set_extracting_state(False)
        self.progress_bar.setValue(100)
        

        if not scenes:
            self._message("information", "Scene Detection",
                "No scene changes were detected with that threshold."
            )
            return

        dialog = SceneResultsDialog(scenes, frame_count, fps, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.action:
            if not self.save_dir:
                self._message("warning", "Missing Output", "Choose a save directory first.")
                return
                
            action = dialog.action
            ext = f".{self.format_combo.currentText().lower()}" if action == "keyframes" else ".mp4"
            
            self.set_extracting_state(True)
            self.progress_bar.setValue(0)
            self.progress_label.setText(f"Starting {action} extraction...")
            
            self.scene_action_worker = SceneActionWorker(
                action, scenes, self.source_file, self.save_dir, ext,
                self._filename_renderer(), self.unique_path, fps, frame_count
            )
            self.scene_action_worker.progress.connect(
                lambda pct, lbl: (self.progress_bar.setValue(pct), self.progress_label.setText(lbl))
            )
            self._connect_worker_result(self.scene_action_worker, self._on_scene_action_finished)
            self._start_worker(self.scene_action_worker)
            
    def _on_scene_action_finished(self, success, msg):
        self.set_extracting_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Complete.")
        
            
        if success:
            self._message("information", "Success", msg)
            self.open_output_button.setEnabled(True)
        else:
            self._message("critical", "Error", msg)

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
            ["Extract Frames", "Extract Clip", "Motion Highlights", "GIF/WebP", "Framerate Conversion", "Frame Interpolation"], 
            0, False
        )
        if not ok:
            return
            
        state = self.gather_app_state(is_custom_job=False)
        # Ensure target_tab matches the selected mode
        if mode == "Motion Highlights":
            state["target_tab"] = 1
        elif mode == "Extract Clip":
            state["target_tab"] = 2
        elif mode == "GIF/WebP":
            state["target_tab"] = 3
        elif mode == "Framerate Conversion":
            state["target_tab"] = 4
            state["fps_subtab"] = 0
        elif mode in ("Frame Interpolation", "Interpolate FPS"):
            state["target_tab"] = 4
            state["fps_subtab"] = 1
        else:
            state["target_tab"] = 0
            
        for path in files:
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

    def clearBatchVideos(self):
        self.batch_list.clear()

    def processBatch(self):
        if not self.batch_list.count() or not self.save_dir:
            self._message('warning', 'Batch Queue', 'Add jobs and choose an output directory first.')
            return
        self.cancel_requested = False
        self.batch_jobs = [copy.deepcopy(self.batch_list.item(i).data(Qt.ItemDataRole.UserRole))
                           for i in range(self.batch_list.count())]
        self._batch_original = (self.source_file, self.gather_app_state(is_custom_job=True))
        self.batch_results = []
        self.in_batch_mode = True
        self.process_next_batch_item()

    def process_next_batch_item(self):
        if self._closing:
            return
        if self.cancel_requested or not self.batch_jobs:
            self.in_batch_mode = False
            source, state = self._batch_original
            if source:
                self.load_video_path(source, async_probe=False)
            else:
                self.source_file = ""
                self.source_file_edit.clear()
                self.current_metadata = None
            self.apply_app_state(state)
            self.job_state = 'completed'
            self._refresh_job_controls()
            good = sum(o.status == JobStatus.SUCCESS for _, o in self.batch_results)
            failed = sum(o.status == JobStatus.FAILED for _, o in self.batch_results)
            details = '\n'.join(f'{name}: {o.error}' for name, o in self.batch_results if o.error)
            self._message('information', 'Batch Complete',
                          f'{good} succeeded; {failed} failed. Remaining: {len(self.batch_jobs)}.\n'
                          f'Outputs: {self.save_dir}\n{details}')
            return
        self._batch_job = self.batch_jobs.pop(0)
        job = self._batch_job
        self._batch_error = ''
        self._dispatching_batch = True
        try:
            self.load_video_path(job['path'], async_probe=False)
            if not self.current_metadata:
                raise ValueError('Video metadata unavailable')
            if job.get('state') is not None:
                self.apply_app_state(job['state'])
            mode = job['mode']
            if 'Motion' in mode:
                self.extractMotion()
            elif 'GIF' in mode or 'WebP' in mode:
                if job.get('state') is None:
                    self.gif_start_time.setTime(ms_to_qtime(0))
                    self.gif_end_time.setTime(ms_to_qtime(self.current_metadata['DurationMs']))
                self.exportAnimation()
            elif 'Framerate Conversion' in mode:
                self.startFramerateConversion()
            elif 'Interpolat' in mode:
                self.startInterpolation()
            elif 'Clip' in mode or mode.startswith('Convert'):
                self.extractClip()
            else:
                if job.get('state') is None:
                    self.extract_part_checkbox.setChecked(False)
                self.extractFrames()
        except Exception as exc:
            self._batch_error = str(exc)
        finally:
            self._dispatching_batch = False
        if self._active_worker is None:
            self.batch_results.append((job['path'], JobOutcome(JobStatus.FAILED, error=self._batch_error or 'Job could not start')))
            QTimer.singleShot(0, self.process_next_batch_item)

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
            self._message("warning", "Invalid Name", "You cannot overwrite built-in presets.")
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

    _extra_controls = ('filename_template_edit', 'export_manifest_checkbox', 'blur_threshold_spinbox',
        'motion_engine_combo', 'motion_cooldown_spin', 'motion_yolo_conf', 'motion_ext_combo',
        'gif_custom_width_spin', 'gif_dither_combo', 'gif_lossless_checkbox', 'gif_loop_combo',
        'clip_format_combo', 'clip_hw_accel_combo', 'clip_subtitle_action')

    @staticmethod
    def _control_value(widget):
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return widget.value()

    @staticmethod
    def _set_control_value(widget, value):
        if isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QLineEdit):
            widget.setText(value)
        else:
            widget.setValue(value)

    def gather_app_state(self, is_custom_job=False):
        state = self._gather_legacy_state(is_custom_job)
        state['schema_version'] = 2
        state['controls'] = {name: self._control_value(getattr(self, name)) for name in self._extra_controls}
        state['custom_rife_path'] = getattr(self, 'custom_rife_path', None)
        state['subtitles'] = {}
        for name in ('frame_subtitle_combo', 'clip_subtitle_combo', 'gif_subtitle_combo'):
            index = getattr(self, name).currentIndex() - 1
            track = self.subtitle_tracks[index] if 0 <= index < len(getattr(self, 'subtitle_tracks', [])) else None
            state['subtitles'][name] = copy.deepcopy(track)
        return copy.deepcopy(state)

    def apply_app_state(self, state):
        from ..utils.presets import BUILTIN_PRESETS
        state = copy.deepcopy(state)
        merged = copy.deepcopy(BUILTIN_PRESETS['Default'])
        merged.update(state)
        if merged.get('motion_mode') == 'MOG2':
            merged['motion_mode'] = 'Keyframes'
        if 'RIFE' in merged.get('interp_engine', '') or 'FILM' in merged.get('interp_engine', ''):
            merged['interp_engine'] = 'AI Neural Interpolation (RIFE ONNX)'
        for prefix in ('conv', 'interp'):
            key = prefix + '_fps_preset'
            if key not in state:
                combo = getattr(self, prefix + '_fps_preset_combo')
                rate = merged.get(prefix + '_fps', 24 if prefix == 'conv' else 60)
                merged[key] = next((combo.itemText(i) for i in range(combo.count())
                                    if combo.itemText(i).startswith(f'{rate:g} FPS')), 'Custom')
        controls = { 'filename_template_edit': 'frame_{timestamp}', 'export_manifest_checkbox': False,
            'blur_threshold_spinbox': 100.0, 'motion_engine_combo': 'MOG2 (Pixel-Based)',
            'motion_cooldown_spin': 2000, 'motion_yolo_conf': 0.4,
            'motion_ext_combo': '.mp4' if merged.get('motion_mode') == 'Clips' else '.jpg',
            'gif_custom_width_spin': 480, 'gif_dither_combo': 'sierra2_4a', 'gif_lossless_checkbox': False,
            'gif_loop_combo': 'Infinite Loop', 'clip_format_combo': 'MP4',
            'clip_hw_accel_combo': self.clip_hw_accel_combo.itemText(0),
            'clip_subtitle_action': self.clip_subtitle_action.itemText(0)}
        for old, new in (('clip_format', 'clip_format_combo'), ('clip_hw_accel', 'clip_hw_accel_combo'),
                         ('gif_dither', 'gif_dither_combo'), ('gif_lossless', 'gif_lossless_checkbox'),
                         ('gif_loop', 'gif_loop_combo')):
            if old in state:
                controls[new] = state[old]
        controls.update(state.get('controls', {}))
        blockers = [QSignalBlocker(w) for w in vars(self).values() if isinstance(w, QWidget)]
        try:
            self._apply_legacy_state(merged)
            # Mode changes rebuild the extension options; signals are blocked.
            self.motion_ext_combo.clear()
            self.motion_ext_combo.addItems(['.mp4', '.mkv', '.avi'] if self.motion_mode_combo.currentText() == 'Clips' else ['.jpg', '.png'])
            for name, value in controls.items():
                self._set_control_value(getattr(self, name), value)
            self.custom_rife_path = state.get('custom_rife_path')
            tracks = getattr(self, 'subtitle_tracks', [])
            added = False
            for track in state.get('subtitles', {}).values():
                if track and track['type'] == 'external' and os.path.isfile(track['path']) and not any(t.get('path') == track['path'] for t in tracks):
                    tracks.append(track)
                    added = True
            self.subtitle_tracks = tracks
            if added:
                self._populate_subtitle_combos()
            for name in ('frame_subtitle_combo', 'clip_subtitle_combo', 'gif_subtitle_combo'):
                track = state.get('subtitles', {}).get(name)
                combo = getattr(self, name)
                combo.setCurrentIndex(0)
                if not track:
                    continue
                key = 'path' if track['type'] == 'external' else 'sub_index'
                index = next((i for i, t in enumerate(self.subtitle_tracks) if t.get('type') == track['type'] and t.get(key) == track.get(key)), None)
                if index is None or (key == 'path' and not os.path.isfile(track['path'])):
                    if self.in_batch_mode:
                        raise ValueError('Selected subtitle resource is unavailable')
                else:
                    combo.setCurrentIndex(index + 1)
        finally:
            del blockers
            self._block_preset_signals = False
        self.toggleTimeFields()
        self.update_custom_fps_visibility(self.extraction_mode_combo.currentText())
        self._update_gif_format_options(self.gif_format_combo.currentText())
        self._update_gif_resolution(self.gif_resolution_combo.currentText())
        self._on_conv_mode_changed(self.conv_mode_combo.currentText())
        self._on_interp_engine_changed(self.interp_engine_combo.currentText())
        self._update_rife_status_display()
        self.mog2_widget.setVisible('MOG2' in self.motion_engine_combo.currentText())
        self.yolo_widget.setVisible('YOLO' in self.motion_engine_combo.currentText())

    def _gather_legacy_state(self, is_custom_job=False):
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
        
        if hasattr(self, "fps_subtab_widget"):
            state["fps_subtab"] = self.fps_subtab_widget.currentIndex()
        if hasattr(self, "conv_mode_combo"):
            state["conv_mode"] = self.conv_mode_combo.currentText()
            state["conv_fps_preset"] = self.conv_fps_preset_combo.currentText()
            state["conv_fps"] = self.conv_fps_spin.value()
            state["conv_codec"] = self.conv_codec_combo.currentText()
            state["conv_crf"] = self.conv_crf_spin.value()
            state["conv_audio"] = self.conv_audio_combo.currentText()
        if hasattr(self, "interp_fps_spin"):
            state["interp_fps_preset"] = self.interp_fps_preset_combo.currentText()
            state["interp_fps"] = self.interp_fps_spin.value()
            state["interp_engine"] = self.interp_engine_combo.currentText()
            state["interp_mc"] = self.interp_mc_combo.currentText()
            state["interp_me"] = self.interp_me_combo.currentText()
            state["interp_scd"] = self.interp_scd_check.isChecked()
            state["interp_scd_threshold"] = self.interp_scd_thresh_spin.value()
            state["interp_codec"] = self.interp_codec_combo.currentText()
            state["interp_crf"] = self.interp_crf_spin.value()
            state["interp_audio"] = self.interp_audio_combo.currentText()
        
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

    def _apply_legacy_state(self, state):
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
        
        if "fps_subtab" in state and hasattr(self, "fps_subtab_widget"): self.fps_subtab_widget.setCurrentIndex(state["fps_subtab"])
        if "conv_mode" in state and hasattr(self, "conv_mode_combo"): self.conv_mode_combo.setCurrentText(state["conv_mode"])
        if "conv_fps_preset" in state and hasattr(self, "conv_fps_preset_combo"): self.conv_fps_preset_combo.setCurrentText(state["conv_fps_preset"])
        if "conv_fps" in state and hasattr(self, "conv_fps_spin"): self.conv_fps_spin.setValue(state["conv_fps"])
        if "conv_codec" in state and hasattr(self, "conv_codec_combo"): self.conv_codec_combo.setCurrentText(state["conv_codec"])
        if "conv_crf" in state and hasattr(self, "conv_crf_spin"): self.conv_crf_spin.setValue(state["conv_crf"])
        if "conv_audio" in state and hasattr(self, "conv_audio_combo"): self.conv_audio_combo.setCurrentText(state["conv_audio"])

        if "interp_fps_preset" in state and hasattr(self, "interp_fps_preset_combo"): self.interp_fps_preset_combo.setCurrentText(state["interp_fps_preset"])
        if "interp_fps" in state and hasattr(self, "interp_fps_spin"): self.interp_fps_spin.setValue(state["interp_fps"])
        if "interp_engine" in state and hasattr(self, "interp_engine_combo"): self.interp_engine_combo.setCurrentText(state["interp_engine"])
        if "interp_mc" in state and hasattr(self, "interp_mc_combo"): self.interp_mc_combo.setCurrentText(state["interp_mc"])
        if "interp_me" in state and hasattr(self, "interp_me_combo"): self.interp_me_combo.setCurrentText(state["interp_me"])
        if "interp_scd" in state and hasattr(self, "interp_scd_check"): self.interp_scd_check.setChecked(state["interp_scd"])
        if "interp_scd_threshold" in state and hasattr(self, "interp_scd_thresh_spin"): self.interp_scd_thresh_spin.setValue(state["interp_scd_threshold"])
        if "interp_codec" in state and hasattr(self, "interp_codec_combo"): self.interp_codec_combo.setCurrentText(state["interp_codec"])
        if "interp_crf" in state and hasattr(self, "interp_crf_spin"): self.interp_crf_spin.setValue(state["interp_crf"])
        if "interp_audio" in state and hasattr(self, "interp_audio_combo"): self.interp_audio_combo.setCurrentText(state["interp_audio"])

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
            (self.conv_mode_combo, "currentTextChanged"),
            (self.conv_fps_preset_combo, "currentTextChanged"),
            (self.conv_fps_spin, "valueChanged"),
            (self.conv_codec_combo, "currentTextChanged"),
            (self.conv_crf_spin, "valueChanged"),
            (self.conv_audio_combo, "currentTextChanged"),
            (self.interp_fps_preset_combo, "currentTextChanged"),
            (self.interp_fps_spin, "valueChanged"),
            (self.interp_engine_combo, "currentTextChanged"),
            (self.interp_mc_combo, "currentTextChanged"),
            (self.interp_me_combo, "currentTextChanged"),
            (self.interp_scd_check, "toggled"),
            (self.interp_scd_thresh_spin, "valueChanged"),
            (self.interp_codec_combo, "currentTextChanged"),
            (self.interp_crf_spin, "valueChanged"),
            (self.interp_audio_combo, "currentTextChanged"),
        ]
        
        for name in self._extra_controls:
            widget = getattr(self, name)
            signal = ('currentTextChanged' if isinstance(widget, QComboBox) else
                      'toggled' if isinstance(widget, QCheckBox) else
                      'textChanged' if isinstance(widget, QLineEdit) else 'valueChanged')
            widgets.append((widget, signal))
        for widget, signal_name in widgets:
            signal = getattr(widget, signal_name, None)
            if signal:
                signal.connect(self.mark_custom)

    # -----------------------------
    # Extra utility UI
    # -----------------------------

    def addUtilityControls(self):
        pass



def _guard_job(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if self._closing or self._active_worker is not None or (self.in_batch_mode and not self._dispatching_batch):
            return
        if method.__name__ != 'processBatch' and not self.current_metadata:
            self._message('warning', 'Source Unavailable', 'Load a video and wait for metadata before processing.')
            return
        try:
            return method(self, *args, **kwargs)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.set_extracting_state(False)
            self._refresh_job_controls()
            self._message('critical', 'Could Not Start Job', str(exc))
    return guarded


for _method in ('extractFrames', 'extractClip', 'extractAllSegments', 'extractMotion',
                'exportAnimation', 'startFramerateConversion', 'startInterpolation', 'detectScenes', 'processBatch'):
    setattr(OmniExtractStudio, _method, _guard_job(getattr(OmniExtractStudio, _method)))
