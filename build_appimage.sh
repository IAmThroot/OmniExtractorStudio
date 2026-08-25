#!/bin/bash
rm -rf AppDir
mkdir -p AppDir/usr/bin
cp -r dist/OmniExtract/* AppDir/usr/bin/
cp AppRun AppDir/
cp OmniExtract.desktop AppDir/
cp OmniExtract.png AppDir/
./appimagetool AppDir OmniExtract-x86_64.AppImage
