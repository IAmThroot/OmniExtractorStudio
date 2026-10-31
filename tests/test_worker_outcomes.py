"""Cleanup failures must not change results; subclasses inherit signal layouts."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from omniextract.media.process import close_stream
from omniextract.workers.base import JobStatus
from omniextract.workers.clip_worker import ClipExtractionWorker, MultiSegmentWorker
from omniextract.workers.frame_worker import FrameExtractionWorker
from omniextract.workers.interpolation_worker import InterpolationWorker
from omniextract.workers.animation_worker import AnimatedExportWorker
from omniextract.workers.motion_worker import MotionExtractionWorker
from omniextract.workers.scene_worker import SceneDetectionWorker, SceneActionWorker


@pytest.mark.parametrize('success,cancelled', [(True, False), (False, False), (False, True)])
def test_segment_cleanup_denied_preserves_outcome(tmp_path, monkeypatch, caplog, success, cancelled):
    import omniextract.workers.clip_worker as module
    temp = tmp_path / 'locked.srt'
    temp.write_text('locked')
    def deny(path):
        raise PermissionError('file is locked')
    monkeypatch.setattr('omniextract.media.process.os.remove', deny)
    def extract(owner, *args):
        owner.cancel_requested = cancelled
        return success, '' if success else 'original encoder error'
    monkeypatch.setattr(module, 'extract', extract)
    worker = MultiSegmentWorker([dict(cmd=[],duration_ms=1,output_path='clip.mp4',temp_srt=str(temp))])
    outcomes = []
    worker.outcome_ready.connect(outcomes.append, Qt.ConnectionType.DirectConnection)
    worker.run()
    assert len(outcomes) == 1
    assert worker.outcome.status == (JobStatus.CANCELLED if cancelled else JobStatus.SUCCESS if success else JobStatus.FAILED)
    assert worker.outcome.count == int(success)
    assert worker.outcome.error == ('' if success else 'original encoder error')
    assert temp.exists()
    assert 'Could not remove cleanup file' in caplog.text


def test_partial_output_cleanup_preserves_encoder_error(tmp_path, monkeypatch):
    import omniextract.workers.clip_worker as module
    output = tmp_path / 'partial.mp4'
    output.write_bytes(b'partial')
    class Process:
        stdout = iter(['encoder failure\n'])
        returncode = 1
        stdin = stderr = None
        def poll(self): return 1
        def wait(self, **kwargs): return 1
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr(module, 'progress_lines', lambda *args: iter(['encoder failure\n']))
    monkeypatch.setattr(module, 'close_stream', lambda *_: None)
    def deny(path): raise PermissionError('locked partial file')
    monkeypatch.setattr('omniextract.media.process.os.remove', deny)
    success, error = module.extract(SimpleNamespace(cancel_requested=False), [], 1, str(output), lambda *_: None)
    assert not success
    assert error == 'encoder failure'


def test_stream_cleanup_denied_is_nonfatal(caplog):
    class Stream:
        def close(self): raise PermissionError('locked stream')
    close_stream(Stream())
    assert 'Could not close cleanup stream' in caplog.text


@pytest.mark.parametrize('parent,init_args,result,status,count,paths', [
    (FrameExtractionWorker, ('source','out','.png',0,0,1,None,None,90,False,'PNG'), (0,0.1,False), JobStatus.SUCCESS,0,[]),
    (FrameExtractionWorker, ('source','out','.png',0,0,1,None,None,90,False,'PNG'), (2,0.1,True), JobStatus.CANCELLED,2,[]),
    (SceneDetectionWorker, ('source',.3), ([10,20],100,30.), JobStatus.SUCCESS,2,[]),
    (SceneDetectionWorker, ('source',.3), ([],100,30.), JobStatus.SUCCESS,0,[]),
    (ClipExtractionWorker, ([],1,'clip.mp4'), (True,False,'clip.mp4',''), JobStatus.SUCCESS,1,['clip.mp4']),
    (ClipExtractionWorker, ([],1,'clip.mp4'), (False,False,'clip.mp4','encoder error'), JobStatus.FAILED,0,[]),
    (InterpolationWorker, ('source','out',60), (False,True,'out','cancelled'), JobStatus.CANCELLED,0,[]),
    (AnimatedExportWorker, ('source','out',0,1000,'GIF',32,10,'none',80,0,False), (True,False,'out','saved'), JobStatus.SUCCESS,1,['out']),
    (MotionExtractionWorker, ('source','out','.png','Keyframes',20,1,0,None,None), (True,3,'saved'), JobStatus.SUCCESS,3,[]),
    (MultiSegmentWorker, ([],), (False,2,['one','two'],'encoder error'), JobStatus.FAILED,2,['one','two']),
    (SceneActionWorker, ('keyframes',[],'source','out','.png',None,None,30,100), (True,'saved'), JobStatus.SUCCESS,0,[]),
])
def test_subclass_inherits_result_dispatch(parent, init_args, result, status, count, paths):
    class Derived(parent):
        def _run(self):
            self.finished.emit(*result)
    worker = Derived(*init_args)
    outcomes = []
    worker.outcome_ready.connect(outcomes.append, Qt.ConnectionType.DirectConnection)
    worker.run()
    assert len(outcomes) == 1
    assert worker.outcome.status == status
    assert worker.outcome.count == count
    assert worker.outcome.paths == paths
    if status == JobStatus.FAILED:
        assert worker.outcome.error == result[-1]


def test_stream_cleanup_does_not_suppress_completion(caplog):
    class Stream:
        def close(self): raise PermissionError('locked stream')
    class Derived(ClipExtractionWorker):
        def _run(self):
            self.process = SimpleNamespace(poll=lambda:0, stdin=Stream(), stdout=None, stderr=None)
            self.finished.emit(True,False,'clip.mp4','')
    worker = Derived([],1,'clip.mp4')
    outcomes = []
    worker.outcome_ready.connect(outcomes.append, Qt.ConnectionType.DirectConnection)
    worker.run()
    assert len(outcomes) == 1
    assert worker.outcome.status == JobStatus.SUCCESS
    assert worker.outcome.paths == ['clip.mp4']
    assert 'Could not close cleanup stream' in caplog.text
