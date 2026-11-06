import os
import shutil
import subprocess
import sys

import pytest

from omniextract.utils.external_process import external_environment, run_external


@pytest.mark.parametrize('original,expected', [(None,None),('',None),('/opt/system-libs','/opt/system-libs')])
def test_frozen_restores_system_library_path(monkeypatch,original,expected):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'platform','linux')
    env={'LD_LIBRARY_PATH':'/bundle/_internal','PATH':'/usr/bin'}
    if original is not None: env['LD_LIBRARY_PATH_ORIG']=original
    before=env.copy()
    child=external_environment(env)
    assert child.get('LD_LIBRARY_PATH')==expected
    assert env==before
    assert child['PATH']==env['PATH']


def test_appimage_original_also_excludes_bundle(monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'platform','linux')
    monkeypatch.setattr(sys,'_MEIPASS','/bundle/usr/bin/_internal',raising=False)
    env={'APPDIR':'/bundle','LD_LIBRARY_PATH':'/bundle/usr/bin/_internal',
         'LD_LIBRARY_PATH_ORIG':'/bundle/usr/lib:/opt/libs:/bundle-other/lib'}
    assert external_environment(env)['LD_LIBRARY_PATH']=='/opt/libs:/bundle-other/lib'


def test_source_environment_preserved(monkeypatch):
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    env={'LD_LIBRARY_PATH':'/user/libs','LD_LIBRARY_PATH_ORIG':'/original','PATH':'/bin'}
    assert external_environment(env)==env


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux dynamic loader regression')
def test_real_ffprobe_with_conflicting_bundled_library(tmp_path,monkeypatch):
    ffprobe=shutil.which('ffprobe')
    if not ffprobe: pytest.skip('FFprobe unavailable')
    (tmp_path/'libz.so.1').write_text('incompatible bundled library')
    env=dict(os.environ,LD_LIBRARY_PATH=str(tmp_path))
    env.pop('LD_LIBRARY_PATH_ORIG',None)
    broken=subprocess.run([ffprobe,'-version'],env=env,capture_output=True)
    if broken.returncode==0: pytest.skip('FFprobe does not dynamically link libz')
    assert b'libz.so.1' in broken.stderr
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    working=run_external([ffprobe,'-version'],env=env,capture_output=True)
    assert working.returncode==0,working.stderr
    assert env['LD_LIBRARY_PATH']==str(tmp_path)


def test_metadata_strict_error_survives(monkeypatch,tmp_path):
    from omniextract.media import metadata
    video=tmp_path/'video.mp4';video.touch()
    monkeypatch.setattr(metadata.shutil,'which',lambda _: 'ffprobe')
    monkeypatch.setattr(metadata,'run_external',lambda *a,**k:subprocess.CompletedProcess(a,127,'','libexample.so: incompatible library'))
    assert metadata.probe_video_metadata(str(video))=={}
    with pytest.raises(RuntimeError,match='incompatible library'):
        metadata.probe_video_metadata(str(video),strict=True)


def test_cancellable_launch_uses_clean_child_environment(monkeypatch):
    from types import SimpleNamespace
    from omniextract.media.process import run_cancellable
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'platform','linux')
    monkeypatch.setenv('LD_LIBRARY_PATH','/bundled-libraries')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG',raising=False)
    owner=SimpleNamespace(cancel_requested=False)
    result=run_cancellable(owner,[sys.executable,'-c',"import os;print(os.environ.get('LD_LIBRARY_PATH','clean'))"])
    assert result.returncode==0
    assert result.stdout.strip()=='clean'
    assert os.environ['LD_LIBRARY_PATH']=='/bundled-libraries'
