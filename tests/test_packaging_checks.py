import pytest
from scripts.check_packaging import validate_bundle, validate_opencv


def test_headless_opencv_accepted():
    validate_opencv('  GUI:                           NONE\n  Video I/O:\n    FFMPEG: YES')


@pytest.mark.parametrize('info', ['  GUI: QT5', '  GUI: QT6', '  GUI: GTK3', 'unknown'])
def test_gui_or_unknown_opencv_rejected(info):
    with pytest.raises(RuntimeError, match='opencv-python-headless'):
        validate_opencv(info)


@pytest.mark.parametrize('name', ['libQt5Core-9088.so.5.15.19','Qt5Core.dll','libQt5Gui.dylib'])
def test_qt5_library_rejected(tmp_path,name):
    directory=tmp_path/'opencv_python.libs';directory.mkdir()
    (directory/name).touch()
    with pytest.raises(RuntimeError,match='Qt 5'):
        validate_bundle(tmp_path)


def test_qt6_bundle_accepted(tmp_path):
    (tmp_path/'libQt6Core.so.6').touch()
    validate_bundle(tmp_path)
