import queue

import modules.module_latest_frame_reader as module_latest_frame_reader
from modules.module_latest_frame_reader import LatestFrameReader, SynchronousFrameReader


class SequenceCapture:
    def __init__(self, reads, fps=25):
        self.reads = list(reads)
        self.fps = fps

    def read(self):
        if self.reads:
            return self.reads.pop(0)
        return False, None

    def get(self, prop):
        return self.fps


def test_latest_frame_reader_keeps_newest_frame_and_counts_drops(monkeypatch):
    monkeypatch.setattr(module_latest_frame_reader.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module_latest_frame_reader.time, "time", lambda: 1.234)
    cap = SequenceCapture([(True, "old"), (True, "new")])
    reader = LatestFrameReader(cap, queue_size=1)

    reader._run()

    assert reader.read_failed is True
    assert reader.frames_read == 2
    assert reader.frames_dropped == 1
    assert reader.queue.get_nowait() == (1234, "new")
    assert reader.stop_event.is_set()


def test_latest_frame_reader_read_times_out_when_empty():
    reader = LatestFrameReader(SequenceCapture([]), queue_size=1)

    assert reader.read(timeout=0.001) == (None, None)


def test_synchronous_frame_reader_returns_video_timestamps_and_stops_at_end(monkeypatch):
    fake_cv2 = type("FakeCv2", (), {"CAP_PROP_FPS": 5})
    monkeypatch.setitem(__import__("sys").modules, "cv2", fake_cv2)
    cap = SequenceCapture([(True, "a"), (True, "b"), (False, None)], fps=20)
    reader = SynchronousFrameReader(cap)

    assert reader.read() == (50, "a")
    assert reader.read() == (100, "b")
    assert reader.read() == (None, None)
    assert reader.stop_event.is_set()
    assert reader.frames_read == 2
    assert reader.frames_dropped == 0


def test_synchronous_frame_reader_respects_stop_event():
    reader = SynchronousFrameReader(SequenceCapture([(True, "unused")]))
    reader.stop()

    assert reader.read() == (None, None)
