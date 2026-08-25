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

## Usage & Installation

You can run OmniExtract Studio via a pre-built executable for your OS, or by building and running from the source code.

### Option 1: Pre-built Executables (Recommended)

1. Navigate to the **[Releases](../../releases)** page on GitHub.
2. **For Windows**: Download the `.exe` file. Double-click to run (no installation required).
3. **For Linux**: Download the `.AppImage` file. Make it executable and run it:
   ```bash
   chmod +x OmniExtract-x86_64.AppImage
   ./OmniExtract-x86_64.AppImage
   ```

### Option 2: Run from Source

1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd "YOUR_REPO"
   ```
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python3 extractor.py
   ```

### Option 3: Build Executables Manually

To compile the application yourself:

**Windows (.exe)**
```cmd
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onedir --windowed --name "OmniExtract" extractor.py
```

**Linux (.AppImage)**
We have provided a helper script to automate AppImage generation.
```bash
# Install dependencies
pip install -r requirements.txt pyinstaller

# Build the PyInstaller binary
pyinstaller --noconfirm --onedir --windowed --name "OmniExtract" extractor.py

# Download appimagetool (Continuous Release)
wget -O appimagetool "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x appimagetool

# Run the build script
./build_appimage.sh
```

## License

MIT License
