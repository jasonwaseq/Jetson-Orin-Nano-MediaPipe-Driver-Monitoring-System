import threading
import queue
import time

class LatestFrameReader:
    """Read camera frames on a background thread and keep only the newest frame."""

    def __init__(self, cap, queue_size=1):
        self.cap = cap
        self.queue = queue.Queue(maxsize=max(1, queue_size))
        self.stop_event = threading.Event()
        self.thread = None
        self.frames_read = 0
        self.frames_dropped = 0
        self.read_failed = False

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            success, frame = self.cap.read()
            if not success:
                self.read_failed = True
                self.stop_event.set()
                break
            self.frames_read += 1
            item = (int(time.time() * 1000), frame)
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    self.frames_dropped += 1
                except queue.Empty:
                    pass
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.frames_dropped += 1

    def read(self, timeout=1.0):
        """Return (timestamp_ms, frame) or (None, None) when no frame is available."""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None, None

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
