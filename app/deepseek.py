"""DeepSeek API integration."""

from collections.abc import Callable, Iterable, Iterator, Sequence
from itertools import batched
from time import sleep

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-pro"
GENERATION_MAX_TOKENS = 8192
STATUS_HINTS = {
    400: "请求参数无效，请检查模型及请求配置。",
    401: "鉴权失败，请检查 DeepSeek API Key。",
    402: "账户余额或可用额度不足，请在 DeepSeek 开放平台检查余额并充值。",
    403: "访问被拒绝，请检查账号权限。",
    422: "请求参数无法处理，请检查 API 参数。",
    429: "请求被限流，请降低请求频率并稍后再试。",
    500: "DeepSeek 服务暂时出错，请稍后重试。",
    503: "DeepSeek 服务暂时过载，请稍后重试。",
}
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


class DeepSeekError(RuntimeError):
    """A request or response error suitable for display in the UI."""


class DeepSeekClient:
    def __init__(self, api_key: str, *, timeout: float = 60) -> None:
        self._client = OpenAI(
            api_key=api_key, base_url=BASE_URL, timeout=timeout, max_retries=0
        )

    def __enter__(self) -> "DeepSeekClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def complete(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        max_tokens: int = GENERATION_MAX_TOKENS,
        retries: int = 2,
        json_output: bool = False,
    ) -> ChatCompletion:
        if retries < 0 or max_tokens < 1:
            raise ValueError("retries 必须非负，max_tokens 必须为正数。")
        request_messages = list(messages)
        response_options = {"response_format": {"type": "json_object"}} if json_output else {}
        for attempt in range(retries + 1):
            try:
                return self._client.chat.completions.create(
                    model=MODEL,
                    messages=request_messages,
                    stream=False,
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                    **response_options,
                )
            except APITimeoutError:
                if attempt == retries:
                    raise DeepSeekError("请求超时，请检查网络后重试。") from None
            except APIConnectionError:
                raise DeepSeekError("无法连接 DeepSeek，请检查网络、代理或证书配置。") from None
            except APIStatusError as error:
                status = error.status_code
                if status not in RETRYABLE_STATUS_CODES or attempt == retries:
                    hint = STATUS_HINTS.get(status, "DeepSeek 请求失败，请检查请求配置或服务状态。")
                    raise DeepSeekError(f"HTTP {status} · {hint}") from None
            sleep(2 ** attempt)
        raise AssertionError("请求循环未返回结果")

    def generate_batches(
        self,
        file_paths: Sequence[str],
        build_messages: Callable[[tuple[str, ...]], Iterable[ChatCompletionMessageParam]],
        *,
        batch_size: int = 3,
        max_tokens: int = GENERATION_MAX_TOKENS,
        validate: Callable[[tuple[str, ...], str], None] | None = None,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        """Yield complete text for each batch, in order, for caller-side validation."""
        for paths in batched(file_paths, batch_size):
            yield from self._generate_batch(paths, build_messages, max_tokens, validate)

    def _generate_batch(
        self,
        paths: tuple[str, ...],
        build_messages: Callable[[tuple[str, ...]], Iterable[ChatCompletionMessageParam]],
        max_tokens: int,
        validate: Callable[[tuple[str, ...], str], None] | None,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        batch_label = "、".join(paths)
        feedback = None
        for _ in range(3 if validate else 1):
            messages = list(build_messages(paths))
            if feedback:
                messages.append({"role": "user", "content": f"上一批回复无效，请修正并完整重发。\n{feedback}"})
            response = self.complete(messages, max_tokens=max_tokens, json_output=validate is not None)
            if not response.choices:
                feedback = "候选结果为空。"
                continue
            choice = response.choices[0]
            if choice.finish_reason == "length":
                if len(paths) > 1:
                    middle = len(paths) // 2
                    yield from self._generate_batch(paths[:middle], build_messages, max_tokens, validate)
                    yield from self._generate_batch(paths[middle:], build_messages, max_tokens, validate)
                    return
                feedback = "单文件批次的输出仍被截断，请缩短回复。"
                continue
            if choice.finish_reason != "stop":
                raise DeepSeekError(f"批次 {batch_label} 未正常完成（{choice.finish_reason}）。")
            content = choice.message.content
            if not content or not content.strip():
                feedback = "回复内容为空。"
                continue
            if validate:
                try:
                    validate(paths, content)
                except ValueError as error:
                    feedback = str(error)
                    continue
            yield paths, content
            return
        raise DeepSeekError(f"批次 {batch_label} 生成失败：{feedback}")
