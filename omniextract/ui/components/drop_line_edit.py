from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QLineEdit


class DropLineEdit(QLineEdit):
    """Line edit that accepts a dropped video path."""

    def __init__(self, callback=None, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                if self.callback:
                    self.callback(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()
