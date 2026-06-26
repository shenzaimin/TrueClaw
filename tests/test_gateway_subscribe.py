from __future__ import annotations

import unittest

from trueclaw.gateway.connection_manager import ConnectionManager


class GatewaySubscribeTest(unittest.TestCase):
    def test_empty_patterns_receive_all(self) -> None:
        self.assertTrue(ConnectionManager._event_matches("gateway.started", set()))
        self.assertTrue(ConnectionManager._event_matches("channel.outbound", set()))

    def test_channel_pattern(self) -> None:
        patterns = {"channel.*"}
        self.assertTrue(ConnectionManager._event_matches("channel.outbound", patterns))
        self.assertFalse(ConnectionManager._event_matches("gateway.client_connected", patterns))

    def test_gateway_wildcard(self) -> None:
        patterns = {"gateway.*"}
        self.assertTrue(ConnectionManager._event_matches("gateway.stopped", patterns))
        self.assertFalse(ConnectionManager._event_matches("channel.outbound", patterns))


if __name__ == "__main__":
    unittest.main()
