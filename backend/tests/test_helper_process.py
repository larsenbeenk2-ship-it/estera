"""Exercise the actual source helper process without discovering a phone."""
import json
import subprocess
import sys

import pytest

from openlocation_backend.web_client import HelperClient


@pytest.mark.parametrize("shutdown", [True, False])
def test_real_source_helper_handshake_shutdown_and_eof(shutdown):
    messages = [{"id": "hello-test", "op": "hello", "params": {"version": 1}}]
    if shutdown:
        messages.append({"id": "shutdown-test", "op": "shutdown", "params": {}})
    payload = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    result = subprocess.run([sys.executable, "-m", "openlocation_backend"], input=payload,
        capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    frames = [json.loads(frame) for frame in result.stdout.splitlines()]
    hello = next(frame for frame in frames if frame.get("id") == "hello-test")
    assert hello["ok"] and hello["result"]["protocol_version"] == 1
    assert "host.check" in hello["result"]["operations"]
    if shutdown:
        reply = next(frame for frame in frames if frame.get("id") == "shutdown-test")
        assert reply["ok"] and reply["result"]["clear_sent"] is False


async def test_real_web_client_owns_and_reaps_private_helper():
    helper = HelperClient()
    try:
        await helper.start()
        process = helper.process
        status = await helper.call("status")
        assert status["session_id"] is None and status["transport_state"] == "disconnected"
        assert status["last_transport_write"] is None
    finally:
        await helper.close()
    assert process.returncode == 0
    assert not helper.pending
