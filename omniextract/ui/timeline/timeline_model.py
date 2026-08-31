"""
Shared data model for the analysis timeline.
"""

from PyQt6.QtCore import QObject, pyqtSignal

class TimelineModel(QObject):
    """Data model representing timeline state."""
    
    playhead_changed = pyqtSignal(int)
    in_out_changed = pyqtSignal(int, int)
    zoom_changed = pyqtSignal(float)
    viewport_changed = pyqtSignal(int, int)
    scene_threshold_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._playhead_ms = 0
        self._in_point_ms = 0
        self._out_point_ms = 0
        self._zoom_level = 1.0
        self._viewport_start_ms = 0
        self._viewport_end_ms = 0
        self._scene_threshold = 0.4

    @property
    def duration_ms(self) -> int:
        return self._duration_ms
        
    @duration_ms.setter
    def duration_ms(self, value: int):
        self._duration_ms = max(0, value)
        if self._out_point_ms > self._duration_ms:
            self._out_point_ms = self._duration_ms
            self.in_out_changed.emit(self._in_point_ms, self._out_point_ms)

    @property
    def playhead_ms(self) -> int:
        return self._playhead_ms

    @property
    def in_point_ms(self) -> int:
        return self._in_point_ms

    @property
    def out_point_ms(self) -> int:
        return self._out_point_ms

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    @property
    def viewport_start_ms(self) -> int:
        return self._viewport_start_ms

    @property
    def viewport_end_ms(self) -> int:
        return self._viewport_end_ms

    @property
    def scene_threshold(self) -> float:
        return self._scene_threshold

    def set_playhead(self, ms: int):
        """Set playhead position clamped to [0, duration_ms]."""
        clamped = max(0, min(ms, self._duration_ms))
        if self._playhead_ms != clamped:
            self._playhead_ms = clamped
            self.playhead_changed.emit(self._playhead_ms)

    def set_in_point(self, ms: int):
        """Set in point, ensuring it is <= out point."""
        clamped = max(0, min(ms, self._duration_ms))
        if clamped > self._out_point_ms:
            clamped = self._out_point_ms
        if self._in_point_ms != clamped:
            self._in_point_ms = clamped
            self.in_out_changed.emit(self._in_point_ms, self._out_point_ms)

    def set_out_point(self, ms: int):
        """Set out point, ensuring it is >= in point."""
        clamped = max(0, min(ms, self._duration_ms))
        if clamped < self._in_point_ms:
            clamped = self._in_point_ms
        if self._out_point_ms != clamped:
            self._out_point_ms = clamped
            self.in_out_changed.emit(self._in_point_ms, self._out_point_ms)

    def set_zoom(self, level: float):
        """Set zoom level clamped to [1.0, 100.0]."""
        clamped = max(1.0, min(level, 100.0))
        if self._zoom_level != clamped:
            self._zoom_level = clamped
            self.zoom_changed.emit(self._zoom_level)

    def set_viewport(self, start_ms: int, end_ms: int):
        """Set viewport start and end."""
        start = max(0, min(start_ms, self._duration_ms))
        end = max(start, min(end_ms, self._duration_ms))
        
        if self._viewport_start_ms != start or self._viewport_end_ms != end:
            self._viewport_start_ms = start
            self._viewport_end_ms = end
            self.viewport_changed.emit(self._viewport_start_ms, self._viewport_end_ms)

    def set_scene_threshold(self, threshold: float):
        """Set scene threshold clamped to [0.0, 1.0]."""
        clamped = max(0.0, min(threshold, 1.0))
        if self._scene_threshold != clamped:
            self._scene_threshold = clamped
            self.scene_threshold_changed.emit(self._scene_threshold)

    def snap_to_scene(self, scene_start_ms: int, scene_end_ms: int):
        """Snap in/out points and playhead to a scene."""
        start = max(0, min(scene_start_ms, self._duration_ms))
        end = max(start, min(scene_end_ms, self._duration_ms))
        
        changed = False
        if self._in_point_ms != start or self._out_point_ms != end:
            self._in_point_ms = start
            self._out_point_ms = end
            changed = True
            
        if changed:
            self.in_out_changed.emit(self._in_point_ms, self._out_point_ms)
            
        self.set_playhead(start)

    def ms_to_viewport_fraction(self, ms: int) -> float:
        """Convert a timestamp to a 0.0..1.0 position within current viewport."""
        viewport_duration = self._viewport_end_ms - self._viewport_start_ms
        if viewport_duration <= 0:
            return 0.0
            
        fraction = (ms - self._viewport_start_ms) / viewport_duration
        return max(0.0, min(fraction, 1.0))
