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


@pytest.mark.parametrize('output,code', [
    ('libxcb-icccm.so.4 => not found',0),
    ("libQt6Core.so.6: version `Qt_6.12' not found",0),
    ('loader failed',1),
])
def test_missing_xcb_dependencies_report_loader_details(tmp_path,monkeypatch,output,code):
    import subprocess
    import scripts.check_packaging as checks
    plugin=tmp_path/'_internal'/'PyQt6'/'Qt6'/'plugins'/'platforms'/'libqxcb.so'
    plugin.parent.mkdir(parents=True);plugin.touch()
    monkeypatch.setattr(checks.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,code,output,''))
    with pytest.raises(RuntimeError,match='missing or incompatible') as error:
        checks.validate_xcb_dependencies(tmp_path)
    assert output in str(error.value)


def test_xcb_dependency_check_uses_bundle_library_paths(tmp_path,monkeypatch):
    import subprocess
    import os
    import scripts.check_packaging as checks
    plugin=tmp_path/'_internal'/'PyQt6'/'Qt6'/'plugins'/'platforms'/'libqxcb.so'
    plugin.parent.mkdir(parents=True);plugin.touch()
    def run(command,**kwargs):
        assert command==['ldd',str(plugin)]
        paths=kwargs['env']['LD_LIBRARY_PATH'].split(os.pathsep)
        assert str(tmp_path/'_internal') in paths
        assert str(plugin.parents[2]/'lib') in paths
        return subprocess.CompletedProcess(command,0,'libxcb.so.1 => /usr/lib/libxcb.so.1','')
    monkeypatch.setattr(checks.subprocess,'run',run)
    checks.validate_xcb_dependencies(tmp_path)


def test_missing_xcb_plugin_is_rejected(tmp_path):
    from scripts.check_packaging import validate_xcb_dependencies
    with pytest.raises(RuntimeError,match='missing the Qt XCB'):
        validate_xcb_dependencies(tmp_path)
