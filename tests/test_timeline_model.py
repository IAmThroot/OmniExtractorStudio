import pytest
from PyQt6.QtWidgets import QApplication
from omniextract.ui.timeline.timeline_model import TimelineModel

@pytest.fixture(scope='session')
def qapp():
    app = QApplication.instance() or QApplication([])
    return app

def test_timeline_model_instantiation(qapp):
    model = TimelineModel()
    assert model.duration_ms == 0
    assert model.playhead_ms == 0
    assert model.in_point_ms == 0
    assert model.out_point_ms == 0
    assert model.zoom_level == 1.0

def test_set_playhead(qapp):
    model = TimelineModel()
    model.duration_ms = 1000
    
    model.set_playhead(500)
    assert model.playhead_ms == 500
    
    model.set_playhead(1500)
    assert model.playhead_ms == 1000  # Clamped to duration
    
    model.set_playhead(-100)
    assert model.playhead_ms == 0  # Clamped to 0

def test_set_in_out_points(qapp):
    model = TimelineModel()
    model.duration_ms = 1000
    model.set_out_point(1000)
    
    model.set_in_point(500)
    assert model.in_point_ms == 500
    
    # Try setting out before in
    model.set_out_point(400)
    assert model.out_point_ms == 500  # Clamped to in point
    
    # Try setting in after out
    model.set_out_point(800)
    model.set_in_point(900)
    assert model.in_point_ms == 800  # Clamped to out point

def test_set_zoom(qapp):
    model = TimelineModel()
    model.set_zoom(5.0)
    assert model.zoom_level == 5.0
    
    model.set_zoom(150.0)
    assert model.zoom_level == 100.0  # Clamped
    
    model.set_zoom(-1.0)
    assert model.zoom_level == 1.0  # Clamped

def test_ms_to_viewport_fraction(qapp):
    model = TimelineModel()
    model.duration_ms = 10000
    model.set_viewport(1000, 5000)
    
    assert model.ms_to_viewport_fraction(1000) == 0.0
    assert model.ms_to_viewport_fraction(3000) == 0.5
    assert model.ms_to_viewport_fraction(5000) == 1.0
    
    # Clamping
    assert model.ms_to_viewport_fraction(0) == 0.0
    assert model.ms_to_viewport_fraction(6000) == 1.0

def test_set_scene_threshold(qapp):
    model = TimelineModel()
    model.set_scene_threshold(0.8)
    assert model.scene_threshold == 0.8
    
    model.set_scene_threshold(1.5)
    assert model.scene_threshold == 1.0  # Clamped
    
    model.set_scene_threshold(-0.5)
    assert model.scene_threshold == 0.0  # Clamped
