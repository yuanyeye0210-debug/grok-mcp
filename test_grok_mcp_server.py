"""Tests for grok_mcp_server.py. Run: python -m unittest test_grok_mcp_server -v

The HTTP calls to the xAI API are mocked everywhere; no key and no network
are needed to run these tests. A live call is only possible with a real
XAI_API_KEY and is deliberately not covered here.
"""

import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grok_mcp_server as server

SAMPLE_RESPONSE = {
    "id": "resp_123",
    "model": "grok-4.7",
    "output": [
        {"type": "reasoning", "summary": []},
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "The answer is 42."},
                {"type": "output_text", "text": " (Really.)"},
            ],
        },
    ],
    "usage": {"input_tokens": 10, "output_tokens": 8},
}


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ExtractTextTests(unittest.TestCase):
    def test_joins_output_text_parts_and_skips_reasoning(self):
        self.assertEqual(server.extract_text(SAMPLE_RESPONSE),
                         "The answer is 42. (Really.)")

    def test_falls_back_to_top_level_output_text(self):
        self.assertEqual(server.extract_text({"output_text": "hello"}), "hello")

    def test_empty_payload_gives_empty_string(self):
        self.assertEqual(server.extract_text({}), "")
        self.assertEqual(server.extract_text({"output": []}), "")


class AskGrokTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"XAI_API_KEY": "xai-test-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_success_posts_correct_request_and_returns_text(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(SAMPLE_RESPONSE)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            answer, error = server.ask_grok("What is the answer?")

        self.assertEqual(error, "")
        self.assertEqual(answer, "The answer is 42. (Really.)")
        self.assertEqual(captured["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer xai-test-key")
        self.assertEqual(captured["body"]["model"], "grok-4.7")
        self.assertEqual(captured["body"]["store"], False)
        self.assertEqual(captured["body"]["input"][1],
                         {"role": "user", "content": "What is the answer?"})

    def test_model_override_is_sent(self):
        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["model"], "grok-4.3")
            return FakeResponse(SAMPLE_RESPONSE)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            answer, error = server.ask_grok("hi", model="grok-4.3")
        self.assertEqual(error, "")
        self.assertTrue(answer.startswith("The answer"))

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            answer, error = server.ask_grok("hi")
        self.assertEqual(answer, "")
        self.assertIn("XAI_API_KEY", error)
        self.assertIn("console.x.ai", error)

    def test_empty_prompt_is_rejected(self):
        answer, error = server.ask_grok("   ")
        self.assertEqual(answer, "")
        self.assertIn("prompt", error)

    def test_http_error_is_reported_with_status(self):
        import urllib.error

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            answer, error = server.ask_grok("hi")
        self.assertEqual(answer, "")
        self.assertIn("401", error)

    def test_empty_answer_is_an_error(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"output": []})):
            answer, error = server.ask_grok("hi")
        self.assertEqual(answer, "")
        self.assertIn("no text", error)


class McpTests(unittest.TestCase):
    def test_initialize_returns_server_info(self):
        resp = server.handle_message({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "grok-mcp")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list_names_ask_grok(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = resp["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["ask_grok"])
        self.assertIn("prompt", tools[0]["inputSchema"]["required"])

    def test_tools_call_returns_answer(self):
        with mock.patch.object(server, "ask_grok",
                               return_value=("mocked answer", "")):
            resp = server.handle_message({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "ask_grok",
                           "arguments": {"prompt": "hello"}},
            })
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual(resp["result"]["content"][0]["text"], "mocked answer")

    def test_tools_call_unknown_tool_is_error(self):
        resp = server.handle_message({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertTrue(resp["result"]["isError"])

    def test_notification_gets_no_reply(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(resp)

    def test_unknown_method_is_an_error(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list", "params": {}})
        self.assertEqual(resp["error"]["code"], -32601)


class SubprocessSmokeTests(unittest.TestCase):
    def test_server_answers_initialize_and_tools_list_over_stdio(self):
        proc = subprocess.Popen(
            [sys.executable,
             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "grok_mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        try:
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "smoke", "version": "1"}},
            }) + "\n")
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                "params": {}}) + "\n")
            proc.stdin.flush()
            first = json.loads(proc.stdout.readline())
            second = json.loads(proc.stdout.readline())
            self.assertEqual(first["result"]["serverInfo"]["name"], "grok-mcp")
            self.assertEqual(second["result"]["tools"][0]["name"], "ask_grok")
        finally:
            proc.kill()
            proc.wait()
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()

    def test_tools_call_without_key_is_a_clean_error(self):
        env = dict(os.environ)
        env.pop("XAI_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable,
             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "grok_mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
        try:
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "ask_grok",
                           "arguments": {"prompt": "hello"}},
            }) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            self.assertTrue(resp["result"]["isError"])
            self.assertIn("XAI_API_KEY", resp["result"]["content"][0]["text"])
        finally:
            proc.kill()
            proc.wait()
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
