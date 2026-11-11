from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai import APIStatusError, APITimeoutError

from app.deepseek import DeepSeekClient, DeepSeekError


def completion(content="{}", finish_reason="stop"):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )
    return SimpleNamespace(choices=[choice])


def status_error(status):
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return APIStatusError("raw error", response=response, body=None)


def build_messages(paths):
    return [{"role": "user", "content": ",".join(paths)}]


class DeepSeekClientTests(unittest.TestCase):
    def setUp(self):
        sdk = self.enterContext(patch("app.deepseek.OpenAI"))
        self.sleep = self.enterContext(patch("app.deepseek.sleep"))
        self.client = self.enterContext(DeepSeekClient("test-key"))
        self.create = sdk.return_value.chat.completions.create

    def test_retry_timeout_and_server_error(self):
        expected = completion()
        self.create.side_effect = [APITimeoutError(request=None), status_error(503), expected]
        self.assertIs(self.client.complete(build_messages(("a.js",))), expected)
        self.assertEqual(self.create.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])

    def test_non_retryable_errors(self):
        for status, hint in [(401, "鉴权失败"), (402, "充值"), (429, "限流")]:
            with self.subTest(status=status):
                self.create.reset_mock()
                self.create.side_effect = status_error(status)
                with self.assertRaisesRegex(DeepSeekError, f"HTTP {status}.*{hint}"):
                    self.client.complete(build_messages(("a.js",)))
                self.create.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_limit(self):
        self.create.side_effect = APITimeoutError(request=None)
        with self.assertRaisesRegex(DeepSeekError, "超时"):
            self.client.complete(build_messages(("a.js",)))
        self.assertEqual(self.create.call_count, 3)

    def test_truncated_batch_is_split_and_rebuilt_in_order(self):
        self.create.side_effect = [
            completion("partial", "length"),
            completion("first"),
            completion("second"),
            completion("third"),
        ]
        paths = ["a.js", "b.js", "c.js", "d.js"]
        self.assertEqual(
            list(self.client.generate_batches(paths, build_messages)),
            [(("a.js",), "first"), (("b.js", "c.js"), "second"), (("d.js",), "third")],
        )
        requests = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual(
            [request["messages"][0]["content"] for request in requests],
            ["a.js,b.js,c.js", "a.js", "b.js,c.js", "d.js"],
        )
        self.assertTrue(all(request["max_tokens"] == 8192 for request in requests))

    def test_truncated_single_file_fails_without_returning_partial_text(self):
        self.create.return_value = completion("partial", "length")
        with self.assertRaisesRegex(DeepSeekError, "a.js.*仍被截断"):
            next(self.client.generate_batches(["a.js"], build_messages))
        self.create.assert_called_once()

    def test_abnormal_finish_is_not_treated_as_success(self):
        self.create.return_value = completion("partial", "content_filter")
        with self.assertRaisesRegex(DeepSeekError, "content_filter"):
            next(self.client.generate_batches(["a.js"], build_messages))
        self.create.assert_called_once()
