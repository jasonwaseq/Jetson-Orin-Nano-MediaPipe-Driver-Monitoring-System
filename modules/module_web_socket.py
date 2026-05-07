
import asyncio
import json
import os
import queue
import threading

class WebSocketBroadcaster:
    """Broadcast JSON messages to all connected websocket clients."""
    def __init__(self, host="0.0.0.0", port=8765, queue_size=None):
        self.host = host
        self.port = port
        self.clients = set()
        if queue_size is None:
            queue_size = int(os.getenv("MP_WS_QUEUE_SIZE", "128"))
        self.queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self.dropped_messages = 0
        self.thread = None
        self.loop = None
        self._server = None
        self._running = threading.Event()
        self._enabled = False
        self._ws_mod = None
        self._startup_error = None

    def start(self):
        """Start websocket server on a background thread."""
        try:
            import websockets  # pylint: disable=import-outside-toplevel
            self._ws_mod = websockets
        except ImportError:
            print("WebSocket disabled: install dependency with 'pip install websockets'")
            return False

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._running.wait(timeout=3.0)
        if self._startup_error is not None:
            print(f"WebSocket startup failed: {self._startup_error}")
        return self._enabled

    def send(self, payload):
        """Queue a payload for broadcast."""
        if not self._enabled:
            return
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.dropped_messages += 1
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(payload)
            except queue.Full:
                self.dropped_messages += 1

    def stop(self):
        """Stop websocket server and worker loop."""
        if not self._enabled:
            return
        self._enabled = False
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(None)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=3.0)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
            self.loop.create_task(self._pump())
            self._enabled = True
        except Exception as exc:
            self._startup_error = exc
            self._enabled = False
            self._running.set()
            self.loop.close()
            return
        self._running.set()
        try:
            self.loop.run_forever()
        finally:
            self._enabled = False
            if self._server is not None:
                self._server.close()
                self.loop.run_until_complete(self._server.wait_closed())
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    async def _serve(self):
        async def handler(websocket):
            self.clients.add(websocket)
            try:
                async for _ in websocket:
                    pass
            except Exception:
                pass
            finally:
                self.clients.discard(websocket)

        self._server = await self._ws_mod.serve(handler, self.host, self.port)
        print(f"WebSocket server listening on ws://{self.host}:{self.port}")

    async def _pump(self):
        while True:
            item = await asyncio.to_thread(self.queue.get)
            if item is None:
                break
            if not self.clients:
                continue
            message = json.dumps(item)
            stale = []
            # Iterate over a snapshot; handlers may add/remove clients concurrently.
            for client in tuple(self.clients):
                try:
                    await client.send(message)
                except Exception:
                    stale.append(client)
            for client in stale:
                self.clients.discard(client)
