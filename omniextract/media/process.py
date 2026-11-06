from ..utils.external_process import popen_external
"""Cancellable subprocess helpers; never leave a PIPE undrained."""
import logging
import os
import queue
import subprocess
import threading
import time


def close_stream(stream):
    """Close a cleanup stream without replacing the processing result."""
    if stream is not None:
        try:
            stream.close()
        except OSError:
            logging.getLogger(__name__).warning('Could not close cleanup stream', exc_info=True)


def remove_file(path):
    """Best-effort removal of an owned temporary or incomplete output file."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logging.getLogger(__name__).warning('Could not remove cleanup file %s', path, exc_info=True)


def stop_process(process):
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()


def run_cancellable(owner, cmd, timeout=120):
    owner.process = popen_external(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if owner.cancel_requested:
                stop_process(owner.process)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                stdout, stderr = owner.process.communicate(timeout=0.1)
                return subprocess.CompletedProcess(cmd, owner.process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        stop_process(owner.process)


def progress_lines(owner, process, stream=None):
    """Read without blocking the worker's cancellation checks."""
    lines = queue.Queue(maxsize=64)
    stopped = threading.Event()
    stream = stream or process.stdout
    def reader():
        try:
            for line in stream:
                while not stopped.is_set():
                    try:
                        lines.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if stopped.is_set():
                    break
        finally:
            stopped.set()
    thread = threading.Thread(target=reader, name='ffmpeg-progress')
    thread.start()
    try:
        while not stopped.is_set() or not lines.empty():
            if owner.cancel_requested:
                stop_process(process)
                break
            try:
                yield lines.get(timeout=0.1)
            except queue.Empty:
                pass
    finally:
        if owner.cancel_requested or not stopped.is_set():
            stop_process(process)
        stopped.set()
        thread.join()
