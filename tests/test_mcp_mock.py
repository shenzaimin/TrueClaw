from __future__ import annotations

import unittest

from trueclaw.tools.mcp.mock_bridge import MockMcpBridge


class MockMcpBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_echo_and_ping(self) -> None:
        bridge = MockMcpBridge(name="demo")
        await bridge.connect()
        tools = await bridge.list_tools()
        self.assertGreaterEqual(len(tools), 2)
        echo = await bridge.call_tool("echo", {"text": "hello-mcp"})
        self.assertEqual(echo, "hello-mcp")
        pong = await bridge.call_tool("ping", {})
        self.assertEqual(pong, "pong")
        self.assertTrue(await bridge.healthcheck())
        await bridge.close()


if __name__ == "__main__":
    unittest.main()
