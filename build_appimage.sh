#!/bin/bash
set -e

echo "==> Building with PyInstaller..."
pyinstaller --noconfirm --onedir --windowed --name "OmniExtract" --add-data "OmniExtract.png:." extractor.py

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
./appimagetool --appimage-extract-and-run AppDir OmniExtract-x86_64.AppImage

echo "==> AppImage created successfully: OmniExtract-x86_64.AppImage"
