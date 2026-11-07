import os
import sys

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton

from . import __version__
from .ui.main_window import OmniExtractStudio
from .ui.metadata_dialog import AboutDialog
from .utils.resources import get_resource_path


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            app_id = f"throot.omniextractstudio.app.{__version__}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        # Suppress xkbcommon Compose file errors for unmapped locales like en_IN
        os.environ["LC_CTYPE"] = "en_US.UTF-8"

    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        # KDE's native file dialogs delegate to host KIO workers, which cannot
        # reliably run against the libraries inside a self-contained image.
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app = QApplication(sys.argv)
    app.setApplicationName("Throot Omni Extract Studio")
    app.setOrganizationName("Throot")
    app.setApplicationDisplayName("OmniExtract Studio")

    # On Windows, taskbars and titlebars prefer .ico multi-resolution format
    icon_candidates = [
        "OmniExtract.ico",
        "OmniExtract.png",
        "resources/icon.ico",
        "resources/icon.png",
    ]
    app_icon = None
    for candidate in icon_candidates:
        cand_path = get_resource_path(candidate)
        if os.path.exists(cand_path):
            app_icon = QIcon(cand_path)
            break

    if app_icon and not app_icon.isNull():
        app.setWindowIcon(app_icon)

    ex = OmniExtractStudio()

    # Toolbar with Scene Detection and About
    toolbar = QHBoxLayout()

    scenes = QPushButton("Scene Detection")
    scenes.clicked.connect(ex.detectScenes)
    ex.scene_detect_button = scenes
    ex._refresh_job_controls()

    about_btn = QPushButton("About")
    about_btn.clicked.connect(lambda: AboutDialog(ex).exec())

    toolbar.addStretch()
    toolbar.addWidget(scenes)
    toolbar.addWidget(about_btn)

    ex.layout().insertLayout(0, toolbar)

    ex.show()
    if "--smoke-test-video" in sys.argv:
        import tempfile
        import cv2
        from .utils.external_process import run_external
        errors = []
        ex._message = lambda kind, title, text: errors.append(text)
        index = sys.argv.index('--smoke-test-video')
        video = sys.argv[index + 1]
        ex.load_video_path(video, async_probe=False)
        if not ex.current_metadata:
            raise RuntimeError('Packaged video loading failed: ' + '; '.join(errors))
        capture = cv2.VideoCapture(video)
        try:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError('Packaged OpenCV could not decode the video')
        finally:
            capture.release()
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'frame.png')
            run_external(['ffmpeg', '-v', 'error', '-y', '-i', video, '-frames:v', '1', output],
                         check=True, capture_output=True, timeout=30)
            if not os.path.isfile(output):
                raise RuntimeError('Packaged FFmpeg frame export failed')
            # Exercise the real asynchronous conversion UI, including completion
            # and worker disposal; a window-only smoke test misses this path.
            from PyQt6.QtCore import QEventLoop
            from PyQt6.QtWidgets import QFileDialog
            from .workers.base import JobStatus
            converted = os.path.join(directory, 'converted24.mp4')
            original_dialog = QFileDialog.getSaveFileName
            QFileDialog.getSaveFileName = lambda *args, **kwargs: (converted, 'MP4 Video (*.mp4)')
            try:
                ex.conv_fps_preset_combo.setCurrentText('Custom')
                ex.conv_fps_spin.setValue(24)
                ex.conv_start_btn.click()
            finally:
                QFileDialog.getSaveFileName = original_dialog
            worker = ex._active_worker
            if worker is None:
                raise RuntimeError('Packaged conversion did not start: ' + '; '.join(errors))
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if ex._active_worker is None else None)
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda: loop.exit(1))
            poll.start(10)
            deadline.start(60000)
            timed_out = loop.exec()
            poll.stop()
            deadline.stop()
            if timed_out or worker.outcome.status != JobStatus.SUCCESS:
                raise RuntimeError('Packaged conversion failed: ' + '; '.join(errors))
            if not os.path.isfile(converted) or os.path.getsize(converted) == 0:
                raise RuntimeError('Packaged conversion produced no output')
            from .media.metadata import probe_video_metadata
            converted_metadata = probe_video_metadata(converted, strict=True)
            if abs(converted_metadata.get('RawFPS', 0) - 24) > 0.01:
                raise RuntimeError('Packaged conversion did not produce 24 FPS video')

    if "--smoke-test" in sys.argv or "--smoke-test-video" in sys.argv:
        QTimer.singleShot(0, ex.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
