"""Exercise the clicked(bool) signal that direct method calls do not cover."""
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton
from omniextract.ui.main_window import _guard_job


@pytest.mark.parametrize('name', ['extractFrames','extractClip','extractAllSegments','extractMotion',
    'exportAnimation','startFramerateConversion','startInterpolation','detectScenes','processBatch'])
@pytest.mark.parametrize('checkable', [False,True])
def test_clicked_checked_argument_is_not_forwarded(name,checkable,monkeypatch):
    app=QApplication.instance() or QApplication([])
    received=[]
    exceptions=[]
    monkeypatch.setattr(sys,'excepthook',lambda *args:exceptions.append(args))
    def action(self):
        received.append(self)
    action.__name__=name
    class Receiver:
        _closing=False
        _active_worker=None
        in_batch_mode=False
        _dispatching_batch=False
        current_metadata={'RawFPS':30}
    receiver=Receiver()
    button=QPushButton()
    button.setCheckable(checkable)
    button.clicked.connect(_guard_job(action).__get__(receiver))
    button.click()
    app.processEvents()
    assert not exceptions
    assert received==[receiver]
