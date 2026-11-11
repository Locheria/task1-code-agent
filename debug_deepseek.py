"""Send one short request to the official DeepSeek API."""

from datetime import datetime
import getpass
import os
import sys
from time import perf_counter
import warnings

from app.deepseek import DeepSeekClient, DeepSeekError, MODEL


def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        if not sys.stdin.isatty():
            print("请在终端运行脚本以输入 API Key，或设置 DEEPSEEK_API_KEY。", file=sys.stderr)
            return 1
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                api_key = getpass.getpass("DeepSeek API Key: ").strip()
        except (EOFError, getpass.GetPassWarning):
            print("当前终端无法隐藏输入，请在 PowerShell 中运行脚本。", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\n已取消。", file=sys.stderr)
            return 130
    if not api_key:
        print("API Key 为空。", file=sys.stderr)
        return 1

    started = perf_counter()
    try:
        with DeepSeekClient(api_key) as client:
            response = client.complete(
                [{"role": "user", "content": "请只回复 OK"}],
                max_tokens=64,
                retries=0,
            )
    except DeepSeekError as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130

    print(f"验证时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"请求模型：{MODEL}")
    print(f"返回模型：{response.model}")
    print(f"请求耗时：{perf_counter() - started:.2f} 秒")
    if not response.choices:
        print("API 已响应，返回的候选结果为空。", file=sys.stderr)
        return 1

    choice = response.choices[0]
    content = (choice.message.content or "").strip()
    print(f"结束原因：{choice.finish_reason}")
    print(f"回复：{content or '（空）'}")
    if choice.finish_reason != "stop" or content != "OK":
        print("API 已响应，结果与预期的完整 OK 回复不符。", file=sys.stderr)
        return 1

    print("DeepSeek 请求验证通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
