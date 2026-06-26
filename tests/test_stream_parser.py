from __future__ import annotations

import unittest

from trueclaw.llm.stream_parser import ToolCallStreamAccumulator


class ToolCallStreamAccumulatorTest(unittest.TestCase):
    def test_merge_streaming_tool_call_fragments(self) -> None:
        acc = ToolCallStreamAccumulator()
        acc.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "function": {"name": "read_file", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        acc.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '{"path":'}}
                            ]
                        }
                    }
                ]
            }
        )
        acc.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '"hello.txt"}'}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        calls = acc.finalize()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "call_abc")
        self.assertEqual(calls[0].name, "read_file")
        self.assertEqual(calls[0].arguments.get("path"), "hello.txt")


if __name__ == "__main__":
    unittest.main()
