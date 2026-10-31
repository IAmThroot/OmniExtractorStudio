"""Regression tests for 1.4 worker results, UI lifecycle and real media."""
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QSettings, QStandardPaths, QTime, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QListWidgetItem

from omniextract.workers.base import JobStatus
from omniextract.workers.clip_worker import ClipExtractionWorker, MultiSegmentWorker
from omniextract.workers.frame_worker import FrameExtractionWorker
from omniextract.workers.interpolation_worker import InterpolationWorker
from omniextract.workers.motion_worker import MotionExtractionWorker
from omniextract.ui.preview_dialog import image_output_path
from omniextract.ui.main_window import OmniExtractStudio
from omniextract.utils.filenames import render_filename, unique_path


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    import omniextract.ui.main_window as ui
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda *_: str(tmp_path))
    monkeypatch.setattr(ui, 'QSettings', lambda *_: QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    for kind in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, kind, lambda *args: None)
    w = OmniExtractStudio()
    yield w
    w.close()
    wait_until(app, lambda: not w._live_workers and not w._probes)
    app.processEvents()


def wait_until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), 'Operation did not finish before deadline'


class Capture:
    def __init__(self, frames=3, delay=0):
        self.frames, self.position, self.delay = frames, 0, delay
        self.released = False
    def isOpened(self): return True
    def get(self, prop):
        return {cv2.CAP_PROP_FPS: 10, cv2.CAP_PROP_FRAME_COUNT: self.frames,
                cv2.CAP_PROP_FRAME_WIDTH: 32, cv2.CAP_PROP_FRAME_HEIGHT: 32}.get(prop, 0)
    def set(self, prop, value): self.position = int(value)
    def read(self):
        if self.position >= self.frames: return False, None
        time.sleep(self.delay)
        frame = np.full((32, 32, 3), self.position % 256, dtype=np.uint8)
        self.position += 1
        return True, frame
    def grab(self): self.position += 1; return True
    def release(self): self.released = True


def frame_worker(tmp_path):
    return FrameExtractionWorker('source', str(tmp_path), '.png', 0, 3, 1,
        lambda n, t, e: f'{n}{e}', unique_path, 90, True, 'PNG')


def test_final_frame_write_failure(tmp_path, monkeypatch):
    cap = Capture()
    monkeypatch.setattr(cv2, 'VideoCapture', lambda *_: cap)
    def write(path, *_):
        if path.endswith('2.png'):
            time.sleep(0.03)
            return False
        return True
    monkeypatch.setattr(cv2, 'imwrite', write)
    worker = frame_worker(tmp_path)
    worker.run()
    assert worker.outcome.status == JobStatus.FAILED
    assert worker.outcome.count == 2
    rows = list(csv.DictReader((tmp_path / 'extraction_manifest.csv').open()))
    assert [row['filename'] for row in rows] == ['0.png', '1.png']
    assert cap.released


def test_failed_nonempty_segment(tmp_path):
    output = tmp_path / 'partial.mp4'
    cmd = [sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"partial"); sys.exit(1)', str(output)]
    worker = MultiSegmentWorker([dict(cmd=cmd, duration_ms=1, output_path=str(output))])
    worker.run()
    assert worker.outcome.status == JobStatus.FAILED
    assert worker.outcome.count == 0
    assert not output.exists()


def test_cancel_silent_subprocess(tmp_path, app):
    worker = ClipExtractionWorker([sys.executable, '-c', 'import time; time.sleep(30)'], 1, str(tmp_path/'out'))
    worker.start()
    time.sleep(0.05)
    worker.cancel()
    assert worker.wait(4000)
    assert worker.outcome.status == JobStatus.CANCELLED
    assert worker.process.poll() is not None


@pytest.mark.parametrize('path,filter,expected', [
    ('frame.jpg','JPEG Image (*.jpg *.jpeg)','frame.jpg'),
    ('frame.jpeg','JPEG Image (*.jpg *.jpeg)','frame.jpeg'),
    ('frame.tiff','TIFF Image (*.tif *.tiff)','frame.tiff'),
    ('frame.tif','TIFF Image (*.tif *.tiff)','frame.tif'),
    ('frame.png','JPEG Image (*.jpg *.jpeg)','frame.jpg'),
    ('frame','PNG Image (*.png)','frame.png')])
def test_preview_extension(path, filter, expected):
    assert image_output_path(path, filter) == expected


def test_custom_fps_and_settings(window):
    window.extraction_mode_combo.setCurrentText('Custom FPS')
    window.custom_fps_spin.setValue(7)
    assert window.get_extraction_step(30) == pytest.approx(30/7)
    window.custom_fps_spin.setValue(100)
    assert window.get_extraction_step(30) == 1
    window.motion_mode_combo.setCurrentText('Clips')
    window.motion_engine_combo.setCurrentText('YOLOv8 (AI-Based)')
    window.motion_ext_combo.setCurrentText('.mkv')
    window.motion_cooldown_spin.setValue(3300)
    window.motion_yolo_conf.setValue(.71)
    window.filename_template_edit.setText('test_{frame}')
    window.custom_rife_path = '/tmp/custom.onnx'
    state = window.gather_app_state(True)
    window.apply_app_state({'motion_mode': 'MOG2'})
    assert window.motion_mode_combo.currentText() == 'Keyframes'
    window.apply_app_state(state)
    assert window.gather_app_state(True) == state
    state['controls']['motion_cooldown_spin'] = 10
    assert window.motion_cooldown_spin.value() == 3300


def test_builtin_presets(window):
    from omniextract.utils.presets import BUILTIN_PRESETS
    for name, state in BUILTIN_PRESETS.items():
        window.apply_app_state(state)
        assert window.motion_mode_combo.currentText() in ('Keyframes', 'Clips')
        assert window.motion_mode_combo.findText(state['motion_mode']) >= 0
    window.apply_app_state(BUILTIN_PRESETS['Security Activity Highlights'])
    assert window.tab_widget.currentWidget() is window.motion_tab


def test_double_start_and_shutdown(window, app, tmp_path):
    worker = ClipExtractionWorker([sys.executable, '-c', 'import time; time.sleep(30)'], 1, str(tmp_path/'out'))
    window._start_worker(worker)
    window.processBatch()  # rejected even if called directly
    window.extractMotion()
    assert window._active_worker is worker
    window.close()
    wait_until(app, lambda: not window._live_workers)
    assert worker.outcome.status == JobStatus.CANCELLED
    assert worker.process.poll() is not None


def test_batch_validation_failure_continues_and_restores(window, app, tmp_path, monkeypatch):
    original = tmp_path/'original.mp4'
    original.touch()
    metadata = {'RawFPS':10, 'RawFrames':10, 'DurationMs':1000}
    def load(path, async_probe=False):
        window.source_file = path
        window.current_metadata = metadata.copy() if 'bad' not in path else None
    monkeypatch.setattr(window, 'load_video_path', load)
    load(str(original))
    window.save_dir = str(tmp_path)
    window.filename_template_edit.setText('original_{frame}')
    before = window.gather_app_state(True)
    def start():
        output = tmp_path/'success'
        cmd = [sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"ok")', str(output)]
        window._start_worker(ClipExtractionWorker(cmd, 1, str(output)))
    monkeypatch.setattr(window, 'extractFrames', start)
    for path in ['bad.mp4', 'good.mp4']:
        item = QListWidgetItem(path)
        item.setData(Qt.ItemDataRole.UserRole, dict(path=path, state=None, mode='Extract Frames'))
        window.batch_list.addItem(item)
    window.processBatch()
    wait_until(app, lambda: not window.in_batch_mode)
    assert [o.status for _, o in window.batch_results] == [JobStatus.FAILED, JobStatus.SUCCESS]
    assert window.source_file == str(original)
    assert window.gather_app_state(True) == before


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg unavailable')
    path = tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=32x32:rate=10:duration=1',
        '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac',str(path)],check=True)
    return path


@pytest.mark.parametrize('fps', [5,20])
def test_real_conform_audio(media, tmp_path, fps):
    output = tmp_path/f'conform_{fps}.mp4'
    worker = InterpolationWorker(str(media),str(output),fps,1000,10,engine='conform')
    worker.run()
    assert worker.outcome.status == JobStatus.SUCCESS, worker.outcome.error
    data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
    streams = {s['codec_type']:s for s in data['streams']}
    expected = 10 / fps
    assert float(streams['video']['duration']) == pytest.approx(expected,abs=.12)
    assert float(streams['audio']['duration']) == pytest.approx(expected,abs=.15)
    assert int(streams['video']['nb_frames']) == 10


def test_atempo_chain_extreme_ratio():
    worker = InterpolationWorker('in','out',1,1000,120,engine='conform')
    factors = [float(part.split('=')[1]) for part in worker._audio_tempo_filter().split(',')]
    assert all(.5 <= factor <= 2 for factor in factors)
    assert np.prod(factors) == pytest.approx(1/120)


@pytest.mark.parametrize('broken', [False,True,'cancel'])
def test_rife_backpressure_and_encoder_failure(tmp_path, monkeypatch, broken):
    import omniextract.workers.interpolation_worker as module
    source = tmp_path/'source.mp4'; source.touch()
    output = tmp_path/'output.mp4'
    cap = Capture(frames=20)
    monkeypatch.setattr(cv2,'VideoCapture',lambda *_:cap)
    monkeypatch.setattr(module,'get_model_status_info',lambda *_:('ready',True,'model'))
    seen = []
    class Model:
        def __init__(self,*_): pass
        def interpolate_pair(self,a,b):
            time.sleep(.03)
            seen.append((int(a[0,0,0]),int(b[0,0,0])))
            return a
    monkeypatch.setattr(module,'RIFEInterpolator',Model)
    real_popen = subprocess.Popen
    def encoder(cmd, **kwargs):
        script = 'import sys; sys.exit(1)' if broken is True else 'import sys,pathlib; data=sys.stdin.buffer.read(); pathlib.Path(sys.argv[1]).write_bytes(data)'
        return real_popen([sys.executable,'-c',script,str(output)],**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',encoder)
    worker = InterpolationWorker(str(source),str(output),20,2000,10,engine='rife',audio_mode='mute')
    worker.start()
    if broken == 'cancel':
        time.sleep(.1)
        worker.cancel()
    assert worker.wait(5000), 'RIFE pipeline hung'
    assert cap.released
    assert not any(t.name.startswith('rife-') for t in threading.enumerate())
    assert worker.outcome.status == (JobStatus.CANCELLED if broken == 'cancel' else JobStatus.FAILED if broken else JobStatus.SUCCESS)
    if broken is False:
        assert seen == [(i,i+1) for i in range(19)]
        assert output.stat().st_size == 40 * 32 * 32 * 3
    else:
        assert not output.exists()


def test_metadata_generation_and_failure(window, monkeypatch, tmp_path):
    from PyQt6.QtCore import QObject, pyqtSignal
    import omniextract.ui.main_window as module
    class Probe(QObject):
        metadata_ready = pyqtSignal(str,dict,list,str)
        error = pyqtSignal(str,str)
        finished = pyqtSignal()
        def __init__(self,path,*_):
            super().__init__(); self.path=path; self.cancelled=False
        def start(self): pass
        def cancel(self): self.cancelled=True
        def isRunning(self): return False
    monkeypatch.setattr(module,'MetadataProbeWorker',Probe)
    path = tmp_path/'same.mp4'; path.touch()
    window.current_metadata = {'RawFPS':60}
    window.load_video_path(str(path))
    first = window._probe_worker
    assert window.current_metadata is None
    assert not window.extract_button.isEnabled()
    window.load_video_path(str(path))
    second = window._probe_worker
    assert first.cancelled
    first.metadata_ready.emit(str(path),{'RawFPS':10},[],'')
    assert window.current_metadata is None
    second.metadata_ready.emit(str(path),{'RawFPS':20,'RawFrames':20,'DurationMs':1000},[],'')
    assert window.current_metadata['RawFPS'] == 20
    first.error.emit(str(path),'old error')
    assert window.current_metadata['RawFPS'] == 20
    second.error.emit(str(path),'current failure')
    assert window.current_metadata is None
    assert not window.preview_button.isEnabled()
    first.finished.emit(); second.finished.emit()


def test_motion_write_failure(tmp_path, monkeypatch):
    import omniextract.ai.detector as detector
    cap = Capture(frames=2)
    monkeypatch.setattr(cv2,'VideoCapture',lambda *_:cap)
    monkeypatch.setattr(cv2,'imwrite',lambda *_:False)
    class Detector:
        def __init__(self,*_): self.closed=False
        def detect_presence(self,*_): return True
        def close(self): self.closed=True
    monkeypatch.setattr(detector,'YOLOPresenceDetector',Detector)
    worker = MotionExtractionWorker('source',str(tmp_path),'.png','Keyframes',20,1,0,
        unique_path,lambda n,t,e:f'{n}{e}',engine='YOLO')
    worker.run()
    assert worker.outcome.status == JobStatus.FAILED
    assert worker.outcome.count == 0
    assert cap.released


def test_subprocess_timeout_is_reaped():
    from omniextract.media.process import run_cancellable
    owner = SimpleNamespace(cancel_requested=False)
    with pytest.raises(subprocess.TimeoutExpired):
        run_cancellable(owner,[sys.executable,'-c','import time;time.sleep(30)'],timeout=.1)
    assert owner.process.poll() is not None


def test_real_silent_conform_and_segment(media, tmp_path):
    silent = tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(media),'-an','-c:v','copy',str(silent)],check=True)
    output = tmp_path/'converted.mp4'
    worker = InterpolationWorker(str(silent),str(output),20,1000,10,engine='conform')
    worker.run()
    assert worker.outcome.status == JobStatus.SUCCESS, worker.outcome.error
    clip = tmp_path/'clip.mp4'
    cmd = ['ffmpeg','-v','error','-y','-i',str(output),'-t','0.2','-c:v','libx264','-progress','pipe:1',str(clip)]
    segments = MultiSegmentWorker([dict(cmd=cmd,duration_ms=200,output_path=str(clip))])
    segments.run()
    assert segments.outcome.status == JobStatus.SUCCESS, segments.outcome.error
    assert segments.outcome.count == 1


def test_subtitle_identity_and_missing_resource(window, tmp_path):
    subtitle = tmp_path/'external.srt'; subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\nhello\n')
    window.subtitle_tracks = [dict(type='external',path=str(subtitle),label='external')]
    window._populate_subtitle_combos()
    window.frame_subtitle_combo.setCurrentIndex(1)
    state = window.gather_app_state(True)
    window.subtitle_tracks.insert(0,dict(type='embedded',sub_index=0,label='embedded'))
    window._populate_subtitle_combos()
    window.apply_app_state(state)
    assert window.frame_subtitle_combo.currentIndex() == 2
    subtitle.unlink()
    window.in_batch_mode = True
    with pytest.raises(ValueError,match='subtitle'):
        window.apply_app_state(state)
    window.in_batch_mode = False


def test_batch_cancellation_stops_remaining(window, app, tmp_path, monkeypatch):
    def load(path,async_probe=False):
        window.source_file=path
        window.current_metadata={'RawFPS':10,'RawFrames':10,'DurationMs':1000}
    monkeypatch.setattr(window,'load_video_path',load)
    window.save_dir=str(tmp_path)
    def start():
        window._start_worker(ClipExtractionWorker([sys.executable,'-c','import time;time.sleep(30)'],1,str(tmp_path/'out')))
    monkeypatch.setattr(window,'extractFrames',start)
    for name in ['first','second']:
        item=QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole,dict(path=name,state=None,mode='Extract Frames'))
        window.batch_list.addItem(item)
    window.processBatch()
    window.cancelExtraction()
    wait_until(app,lambda:not window.in_batch_mode)
    assert len(window.batch_results)==1
    assert window.batch_results[0][1].status==JobStatus.CANCELLED
    assert len(window.batch_jobs)==1


def test_real_ui_frame_job(window, app, media, tmp_path):
    window.load_video_path(str(media),async_probe=False)
    window.save_dir=str(tmp_path)
    window.filename_template_edit.setText('ui_{frame}')
    window.export_manifest_checkbox.setChecked(True)
    window.extractFrames()
    worker=window._active_worker
    assert worker is not None
    results=[]
    worker.outcome_ready.connect(results.append)
    wait_until(app,lambda:window._active_worker is None)
    assert len(results)==1
    assert results[0].status==JobStatus.SUCCESS
    assert results[0].count==10
    assert len(list(tmp_path.glob('ui_*.png')))==10
    assert window.job_state=='completed'
    assert window.extract_button.isEnabled()


def test_preview_subtitle_replacement(app,tmp_path,monkeypatch):
    from PyQt6.QtWidgets import QComboBox
    import omniextract.ui.preview_dialog as module
    old=tmp_path/'old.srt'; old.write_text('old')
    new=tmp_path/'new.srt'; new.write_text('1\n00:00:00,000 --> 00:00:01,000\nnew\n')
    combo=QComboBox(); combo.addItems(['Off','Embedded','<Browse External Subtitle...>'])
    state=SimpleNamespace(_temp_extracted_srt=str(old),_external_track_index=None,
        _external_track_path=None,video_path=str(tmp_path/'video.mp4'),sub_combo=combo,
        _set_subtitles_data=lambda data:None)
    monkeypatch.setattr(module.QFileDialog,'getOpenFileName',lambda *args:(str(new),''))
    module.VideoPreviewDialog._on_subtitle_track_changed(state,2)
    assert not old.exists()
    assert combo.count()==4
    assert combo.itemText(3)=='<Browse External Subtitle...>'
    assert state._external_track_path==str(new)


def test_source_startup_check(tmp_path):
    result = subprocess.run([sys.executable,'extractor.py','--smoke-test'],
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',XDG_CONFIG_HOME=str(tmp_path)),
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stderr
