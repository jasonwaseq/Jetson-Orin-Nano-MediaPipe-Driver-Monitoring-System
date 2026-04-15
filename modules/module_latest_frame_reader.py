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
        consecutive_failed_reads = 0
        while not self.stop_event.is_set():
            success, frame = self.cap.read()
            if not success:
                consecutive_failed_reads += 1
                time.sleep(0.01)
                # On Mac/Linux, camera might need a few frames to warm up
                if consecutive_failed_reads > 30:
                    self.read_failed = True
                    self.stop_event.set()
                    break
                continue
            
            consecutive_failed_reads = 0
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

class SynchronousFrameReader:
    """Read frames synchronously from a video file. Does not drop frames."""
    def __init__(self, cap):
        self.cap = cap
        self.frames_read = 0
        self.frames_dropped = 0
        self.stop_event = threading.Event()
        self.fps = cap.get(3) or 30 # cv2.CAP_PROP_FPS is 5

    def start(self):
        # No background thread needed for synchronous reading
        pass

    def read(self, timeout=1.0):
        if self.stop_event.is_set():
            return None, None
            
        import cv2
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0: fps = 30
            
        success, frame = self.cap.read()
        if not success:
            self.stop_event.set()
            return None, None
            
        self.frames_read += 1
        timestamp_ms = int((self.frames_read * 1000) / fps)
        return timestamp_ms, frame

    def stop(self):
        self.stop_event.set()
