#!/bin/bash
set -e

echo "==> Building with PyInstaller..."
PYI_BIN="pyinstaller"
if command -v .build_env/bin/pyinstaller >/dev/null 2>&1; then
    PYI_BIN=".build_env/bin/pyinstaller"
fi

$PYI_BIN --noconfirm --onedir --windowed --name "OmniExtract" \
    --add-data "OmniExtract.png:." \
    --add-data "OmniExtract.ico:." \
    --add-data "assets:assets" \
    extractor.py

if [ ! -f "appimagetool" ]; then
    echo "==> Downloading appimagetool..."
    wget -q -O appimagetool "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool
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

echo "==> AppImage created successfully: OmniExtract-x86_64.AppImage"
