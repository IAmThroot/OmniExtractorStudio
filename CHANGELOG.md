# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-10-16

### Added & Optimized
- **Asynchronous Video Metadata & Subtitle Probing**: Integrated background `MetadataProbeWorker` to offload ffprobe calls from the main UI thread, eliminating GUI freezes when opening large or network-stored videos.
- **Hardware Acceleration Encoder Profiles**: Added `get_hwaccel_encode_profile()` with unified rate-control flags across NVIDIA NVENC (`-cq`), Intel QSV (`-global_quality`), VAAPI (`-qp`), and CPU (`-crf`). Wired hardware encoding into Clip extraction, Multi-Segment cuts, Scene splitting, and Motion clip extraction.
- **Accurate Scene Splitting**: Replaced keyframe-snapped copy cuts (`-ss` before `-i`) with re-encoded hardware-accelerated cuts, ensuring frame-accurate cuts strictly on detected scene boundaries.
- **YOLO Temporal Stride Optimization**: Sampled YOLO presence detection at ~5 FPS intervals instead of full-frame inference on 30/60 FPS streams, yielding a 6x to 12x speedup in AI motion detection.
- **Subprocess Hang Protection & Bounded Timeouts**: Added strict timeouts (`timeout=15`–`120s`) and fallback process termination across `communicate()` and `wait()` calls in `AnimationExportWorker`, `ClipExtractionWorker`, `MultiSegmentWorker`, `InterpolationWorker`, and `VideoPreviewDialog`.
- **Direct Thumbnail Decoding & OOM Prevention**: Refactored `ContactSheetDialog` to decode images directly at target thumbnail resolution via `QImageReader.setScaledSize()`, cutting memory usage by over 95% on 4K/8K media and paginating display view to prevent UI stalls.
- **$O(\log N)$ Binary Subtitle Search**: Optimized subtitle overlay synchronization during video preview playback using binary search (`bisect_right`) with bounding cue duration windows.
- **Multi-Block Video Cache Hashing**: Replaced 128KB head/tail sampling in `analysis_cache` with distributed multi-block sampling (0%, 25%, 50%, 75%, 100%) to prevent collision on edited video files.
- **Corrected PNG Quality Inversion**: Fixed PNG compression scale mapping so that 0–100 quality maps properly to libpng's 0–9 compression range (`IMWRITE_PNG_COMPRESSION`).
- **Downscaled Blur Detection & Streaming Manifest**: Frame extraction now downscales Laplacian blur detection images to $\le 720\text{p}$ in float32 (saving ~66MB per 4K frame) and streams CSV manifests directly to disk with periodic flushing.

### Fixed
- **Python < 3.14 Import Failure**: `get_hwaccel_encode_profile()` referenced `Optional` without importing it. On Python 3.9–3.13 (including CI's 3.10) annotations are evaluated eagerly, so `import omniextract.media.hwaccel` raised `NameError` and the whole app failed to start. Added the missing import and a regression test.
- **Cached Hardware Probes**: `probe_capabilities()` / `probe_decode_capabilities()` / `probe_encode_capabilities()` are now `lru_cache`d and expose `invalidate_capability_cache()`, so the two blocking `ffmpeg` probes no longer run per export (or per scene clip). Scene splitting resolves the encode profile once instead of once per clip.
- **Clip Encoder Resolution Unified**: Both clip exporters and the subtitle-burn re-encode path now share `_resolve_clip_video_args()`, removing the duplicated hwaccel mapping and the double capability probe.
- **Robust Subtitle Parser**: Replaced the single backtracking regex with a linear block scan that tolerates single-digit hours, 1–3 digit milliseconds, WebVTT cue settings/`NOTE` blocks, inline `{}`/`<>` tags, and HTML entities.
- **Graceful Cache/IO Failures**: `analysis_cache` now handles corrupt JSON and I/O errors without raising, removes partial temp files, and `invalidate()` is idempotent; `probe_video_metadata()` no longer crashes on `duration: null` or a missing file during `getsize`, and chapter parsing accepts missing timestamps.
- **Motion Worker Cleanup**: Guaranteed `VideoCapture`/YOLO session release on every early-return path, guarded against zero/NaN FPS, and surfaced YOLO model-load failures as errors instead of crashing the thread.
- **Bounded Frame Extraction Memory**: In-flight uncompressed frames are capped (≤ 4) independently of core count to avoid multi-hundred-MB spikes on 4K/8K extraction.
- **Debounced Preview Scrubbing**: Slider drags now throttle media seeks to ~10/s while keeping the timeline/time label live, instead of seeking on every pixel move.

## [1.2.0] - 2026-10-16

### Added
- **FPS & Interpolation Tab with Dedicated Sub-Tabs**: Separated "Framerate Conversion" and "Frame Interpolation" into distinct sub-tab workflows for clear separation of concerns.
- **Framerate Conversion Sub-Tab**:
  - Standard Resample (`fps` filter) for instant drop/duplicate conversion keeping speed and duration unchanged.
  - Frame Blending (`blend`) for smooth cadence reduction.
  - Playback Speed Conform (`setpts`) to change playback speed and interpretation (e.g. 60 FPS footage played back at 24 FPS for 0.4x slow motion) without dropping or duplicating frames.
- **Frame Interpolation Sub-Tab**:
  - **Motion-Compensated Interpolation (MCI)**: Zero-dependency FFmpeg `minterpolate` filter supporting Adaptive Overlapped Block Motion Compensation (`aobmc`), bidirectional motion estimation, search algorithms (`epzs`, `hexbs`, `esa`), and scene cut guard (`scd=fdiff`).
  - **Ratio-Aware AI Neural Interpolation (RIFE ONNX)**: Model manager auto-detecting `RIFE_fp32.onnx` (downloadable from Hugging Face `FuryTMP/RIFE_fp32`) or custom ONNX models in `assets/models/`, running via the lightweight ONNX Runtime with zero PyTorch dependencies.
  - **Recursive Multi-Multiplier Cadence**: Dynamically derives target-to-source multiplier (1x, 2x, 4x, 8x) and generates true recursive intermediate frames with exact pipe framerate and resampling filter.
- **Source Video Properties Display**: Fixed source FPS detection to reliably probe stream and container frame rates across MP4, MKV, and WebM containers.
- **Target FPS Presets**: Quick-select rates (24, 25, 29.97, 30, 50, 59.94, 60, 120 FPS, 0.5x, 2x, 4x) and custom floating-point spinboxes.
- **Hardware-Accelerated Encoding**: Integrated NVENC, QSV, and VAAPI encoder selection via `hwaccel.py`.
- **Preset Profile & Batch Queue Integration**: Save and load both conversion and interpolation configurations within Presets, with distinct Batch Queue job creation for Framerate Conversion and Frame Interpolation.

### Fixed & Optimized
- **Batch Queue Orchestration**: Fixed worker attribute references (`gif_worker`, `motion_worker`), signal parameter signatures, and cleanup lists (`multi_segment_worker`, `scene_worker`), eliminating `AttributeError` exceptions and allowing batch queues to advance automatically without dropping error handlers.
- **Strict AI Validation**: Eliminated silent `cv2.addWeighted` fallbacks on `.pkl` and `.safetensors` checkpoints; now explicitly warns users when a raw weights file lacks an exported ONNX model graph.
- **Scene and Clip Extraction Robustness**: Guarded against `ZeroDivisionError` when scene cut lists are empty; added strict subprocess return code and output file validation to prevent false-success reporting on failed FFmpeg calls.
- **Subprocess & Deadlock Prevention**: Switched animated export worker to file-backed error streams, preventing 64KB OS pipe buffer deadlocks during long exports; added proper `cancel()` routines across all background workers.
- **Vectorized Audio Waveform Processing**: Optimized audio analysis with NumPy `frombuffer` and array vectorization; progressive extraction now decodes the audio stream once rather than decoding 3 sequential times.
- **Metadata & Safe State Initialization**: Added safe fallback parsing for non-numeric FPS metadata strings (`"Unknown"`) preventing clip time/frame synchronization crashes; properly initialized `source_file` and `save_dir` attributes with drag-and-drop directory support.
- **Cache & Preset Atomic Writes**: Replaced in-place writes in `analysis_cache` and `preset_manager` with atomic temporary file replacements (`.tmp` + `os.replace`), protecting user configs from corruption during unexpected shutdowns.
- **Linux cuDNN 9+ Dynamic Loading**: Preloaded modular cuDNN 9 shared libraries (`libcudnn_ops`, `libcudnn_cnn`, `libcudnn_adv`, `libcudnn`) with `RTLD_GLOBAL` on Linux to restore CUDA acceleration on cutting-edge distributions.
- **Headless CI Testing**: Configured `QT_QPA_PLATFORM=offscreen` and headless Qt core fixtures, ensuring reliable headless test execution in GitHub Actions CI across Linux and Windows runners.

## [1.1.0] - 2026-09-01

### Added
- **YOLOv8 AI-Based Motion Detection**: Added an alternative AI engine using an ultra-lightweight ONNX Runtime backend (`yolov8n.onnx`) bundled in `assets/models/`.
- **Target Filtering & Fast Inference**: High-speed AI detection bypassing NMS overhead to track people, vehicles, and animals directly against model output tensors.
- **Engine Switching UI**: Motion Extraction tab now features a toggle between pixel-based MOG2 and YOLOv8 with contextual settings (Sensitivity/Area vs. Confidence Threshold).
- Comprehensive test suite covering utilities and internal modules (tests for timestamps, metadata, subtitles, filename generation, and FFmpeg command utilities).
- Dynamic application version display in the window titlebar and About dialog.
- Re-architected project structure into a proper Python package (`omniextract`) for improved maintainability.
- Multi-resolution Windows application icon (`OmniExtract.ico`) with early `AppUserModelID` registration for taskbar grouping.
- Added a font size adjustment control in the video preview dialog.
- Added SHA256 checksum generation for release binaries to ensure download integrity.
- Full-App JSON Preset Profiles with built-in defaults and custom user saving.
- Smart Batch Queue support for generating Bulk Custom Jobs on the fly.
- Independent Custom Job Queueing per video (snapshotting individual settings for Frames, Clips, and Animations).
- Added a dedicated "Save Directory" configuration section to the GIF/WebP tab.
- Detailed README overhaul featuring a quick-start guide, prerequisite installations, and improved documentation layout.

### Changed
- **Unified Video Source & Output Panel**: Replaced redundant file and directory selectors across individual tabs with a persistent, global I/O panel.
- **Frame Extraction Throughput**: Throttled UI progress updates and scaled I/O thread pool to CPU core count for massive speed improvements.
- **Motion Clip Generation**: Switched motion clip cutting to fast H.264 re-encoding to guarantee millisecond-accurate cuts even on sparse-keyframe videos.
- Refactored monolithic code into modular modules (`ui/`, `workers/`, `media/`, `utils/`).
- Streamlined `extractor.py` to function as a lightweight entrypoint shim.
- Redesigned the Batch Queue "Add Videos" button to prompt for Job Types and create true Custom Jobs.
- Performed a comprehensive codebase audit to eliminate dead code and formatting issues.

### Fixed
- **Motion Area Scaling**: Fixed a resolution-scaling calculation bug where `min_area` was compared against downscaled 320x180 thumbnails rather than the native video resolution.
- **MOG2 Initial Stabilization**: Added a 5-frame stabilization delay to prevent false-positive motion triggers on the first frame of a video.
- **Motion Cooldown Bounds**: Removed the hardcoded 500ms minimum threshold on cooldown duration to allow micro-clip extraction down to 0ms.
- **Linux Locale Warnings**: Handled `xkbcommon` compose locale errors gracefully on Linux platforms.
- Fixed Windows taskbar icon displaying default Python icon.
- Fixed a bug where the Batch Queue ignored Custom Job settings and defaulted to extracting entire files.
- Fixed a bug causing GIF batch exports to halt the queue by prompting for a save location.
- Fixed crashes and infinite hangs caused by corrupted or invalid MP4/MKV files by implementing strict subprocess management and bounds checking.
- Eliminated lingering zombie `ffmpeg` processes upon closing the video preview dialog.

## [1.0.0] - Initial Release

### Added
- Intelligent video frame extraction with blur detection.
- Subtitle extraction and hard-burning.
- Motion detection clip extraction.
- GIF and WebP animated export with palette generation.
- Real-time video preview with thumbnails.
- Automated GitHub Actions builds for Windows and Linux.
