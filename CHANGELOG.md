# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
