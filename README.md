# OmniExtract Studio

OmniExtract Studio is an all-in-one desktop application designed for high-precision video processing, frame extraction, intelligent motion tracking, clip editing, and animated GIF/WebP creation. Built on top of PyQt6, OpenCV, and FFmpeg, it offers unparalleled control and performance for creating datasets, ripping subtitles, and building lightweight video animations.

## Features

- **Advanced Frame Extraction**: Extract frames accurately. Features an intelligent blur/sharpness filter (using Laplacian variance) to automatically discard blurry frames during extraction.
- **Clip Cutting & Multi-Segment Export**: Non-destructively trim, chapterize, and extract multiple clips using hardware-accelerated `ffmpeg` stream copying, or re-encode them.
- **Motion-Triggered Extraction**: Automatically isolate and extract only the moments of movement in a video (e.g. security footage, wildlife cams) using OpenCV's MOG2 background subtractor.
- **Animated GIF & WebP Maker**: Create high-quality, lightweight animations. Features 2-pass FFmpeg palette generation, advanced dithering, and resolution scaling.
- **Subtitle Ripper & Burner**: Instantly extract embedded subtitle tracks (`.srt`, `.vtt`) or hard-burn them visually into clips and animations.
- **Real-Time Video Preview**: A smooth, native-framerate playback window with timeline thumbnail generation, sub-frame scrubbing, and anti-aliased floating subtitle overlays.

## Requirements

- Python 3.9+
- [FFmpeg & FFprobe](https://ffmpeg.org/download.html) (Must be installed and available on system PATH)

### Python Dependencies

```bash
pip install -r requirements.txt
```

## Running the Application

To run from source:

```bash
python3 tfe_upgraded.py
```

## Building Executables

### Windows (.exe)

OmniExtract Studio utilizes GitHub Actions to automatically cross-compile a standalone `.exe` using PyInstaller. You can download the latest `.exe` from the [Releases](#) page.

To build manually on Windows:
```cmd
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onedir --windowed --name "OmniExtract" tfe_upgraded.py
```

### Linux (.AppImage)

To package as an AppImage on Linux:
```bash
# Install PyInstaller
pip install pyinstaller

# Build the binary
pyinstaller --noconfirm --onedir --windowed --name "OmniExtract" tfe_upgraded.py

# Download appimagetool
wget -O appimagetool "https://github.com/AppImage/AppImageKit/releases/download/13/appimagetool-x86_64.AppImage"
chmod +x appimagetool

# Create AppDir structure and bundle
mkdir -p AppDir/usr/bin
cp -r dist/OmniExtract/* AppDir/usr/bin/
cp AppRun AppDir/
cp OmniExtract.desktop AppDir/
cp icon.png AppDir/

./appimagetool AppDir OmniExtract-x86_64.AppImage
```

## License

MIT License
