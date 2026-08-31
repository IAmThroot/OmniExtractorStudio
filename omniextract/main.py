import os
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton

from .ui.main_window import OmniExtractStudio
from .ui.metadata_dialog import AboutDialog
from .utils.resources import get_resource_path


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            app_id = "throot.omniextractstudio.app.1.1.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        # Suppress xkbcommon Compose file errors for unmapped locales like en_IN
        os.environ["LC_CTYPE"] = "en_US.UTF-8"

    app = QApplication(sys.argv)
    app.setApplicationName("Throot Omni Extract Studio")
    app.setOrganizationName("Throot")

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

    about_btn = QPushButton("About")
    about_btn.clicked.connect(lambda: AboutDialog(ex).exec())

    toolbar.addStretch()
    toolbar.addWidget(scenes)
    toolbar.addWidget(about_btn)

    ex.layout().insertLayout(0, toolbar)

    ex.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
