#!/bin/bash
set -e
# Packaging and automated checks must not open desktop-integration dialogs.
export APPIMAGELAUNCHER_DISABLE=1

echo "==> Building with PyInstaller..."
BUILD_PYTHON="python"
if command -v .build_env/bin/pyinstaller >/dev/null 2>&1; then
    BUILD_PYTHON=".build_env/bin/python"
fi

"$BUILD_PYTHON" scripts/check_packaging.py

"$BUILD_PYTHON" -m PyInstaller --clean --noconfirm --onedir --windowed --name "OmniExtract" \
    --add-data "OmniExtract.png:." \
    --add-data "OmniExtract.ico:." \
    --add-data "assets:assets" \
    extractor.py

"$BUILD_PYTHON" scripts/check_packaging.py dist/OmniExtract

echo "==> Checking packaged application startup..."
SMOKE_DIR=$(mktemp -d)
trap 'rm -rf "$SMOKE_DIR"' EXIT
ffmpeg -v error -f lavfi -i testsrc2=size=64x64:rate=30:duration=1 -c:v mpeg4 "$SMOKE_DIR/video.mp4"
QT_QPA_PLATFORM=offscreen timeout 60 dist/OmniExtract/OmniExtract --smoke-test-video "$SMOKE_DIR/video.mp4"

if [ ! -s "appimagetool" ]; then
    echo "==> Downloading appimagetool..."
    curl --fail --location --connect-timeout 20 --max-time 120 --retry 2 \
        --output appimagetool.download \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool.download
    mv appimagetool.download appimagetool
fi

echo "==> Creating AppDir..."
rm -rf AppDir
mkdir -p AppDir/usr/bin
cp -r dist/OmniExtract/* AppDir/usr/bin/
cp AppRun AppDir/
cp OmniExtract.desktop AppDir/
cp OmniExtract.png AppDir/

echo "==> Packaging AppImage..."
ARCH=x86_64 ./appimagetool --appimage-extract-and-run AppDir OmniExtract-x86_64.AppImage

SMOKE_IMAGE="$PWD/OmniExtract-x86_64.AppImage"
(cd "$SMOKE_DIR" && "$SMOKE_IMAGE" --appimage-extract > /dev/null)
"$BUILD_PYTHON" scripts/check_packaging.py "$SMOKE_DIR/squashfs-root/usr/bin"
APPDIR="$SMOKE_DIR/squashfs-root" QT_QPA_PLATFORM=offscreen timeout 60 "$SMOKE_DIR/squashfs-root/AppRun" --smoke-test-video "$SMOKE_DIR/video.mp4"

if command -v xvfb-run >/dev/null 2>&1; then
    if ! APPDIR="$SMOKE_DIR/squashfs-root" QT_QPA_PLATFORM=xcb QT_DEBUG_PLUGINS=1 xvfb-run -a timeout 60 "$SMOKE_DIR/squashfs-root/AppRun" --smoke-test-video "$SMOKE_DIR/video.mp4" >"$SMOKE_DIR/xcb.log" 2>&1; then
        cat "$SMOKE_DIR/xcb.log"
        exit 1
    fi
fi

echo "==> AppImage created successfully: OmniExtract-x86_64.AppImage"
