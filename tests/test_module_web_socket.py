import asyncio
import json

from modules.module_web_socket import WebSocketBroadcaster


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    async def send(self, message):
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append(message)


def test_send_ignores_payloads_when_broadcaster_is_disabled():
    broadcaster = WebSocketBroadcaster()

    broadcaster.send({"type": "alert"})

    assert broadcaster.queue.empty()


def test_pump_broadcasts_json_and_removes_stale_clients():
    async def run():
        broadcaster = WebSocketBroadcaster()
        healthy = FakeClient()
        stale = FakeClient(fail=True)
        broadcaster.clients = {healthy, stale}
        broadcaster.queue.put({"type": "alert", "level": 2})
        broadcaster.queue.put(None)

        await broadcaster._pump()

        assert healthy.messages == [json.dumps({"type": "alert", "level": 2})]
        assert broadcaster.clients == {healthy}

    asyncio.run(run())
