"""DeepSeek / Claude 共用的 HTTP 调用：超时、重试、限流（429）处理。

- 网络错误、超时、429、5xx（含 Anthropic 的 529 overloaded）会重试，最多 ATTEMPTS 次；
- 429 / 503 带 Retry-After 时按它等待（最多 MAX_WAIT 秒），否则按 RETRY_DELAYS 退避；
- 4xx（除 408/409/429）直接抛出，不重试：多半是 key 错、余额不足或请求本身有问题，重试只会浪费钱；
- 日志只记模型名、状态码、第几次尝试和等待秒数，不记 URL 参数、请求体或密钥。
"""
import asyncio
import logging
from typing import Optional

import httpx

log = logging.getLogger('ai_council')

ATTEMPTS = 3
RETRY_DELAYS = (2.0, 6.0)      # 第 1、2 次失败后的等待秒数（测试里可改成 0）
MAX_WAIT = 30.0
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class RateLimited(Exception):
    """重试之后仍被限流（429）。"""


def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get('retry-after')
    try:
        return min(MAX_WAIT, max(0.0, float(value))) if value is not None else None
    except ValueError:
        return None


async def post_json(client: httpx.AsyncClient, url: str, *, headers: dict, json: dict, timeout: float,
                    label: str = 'api') -> dict:
    """POST 并返回 JSON；可重试的错误自动重试，最后一次仍失败就抛出原来的异常。"""
    for attempt in range(1, ATTEMPTS + 1):
        delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)] if RETRY_DELAYS else 0.0
        try:
            response = await client.post(url, headers=headers, json=json, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            if attempt == ATTEMPTS:
                raise
            log.warning('api_retry model=%s attempt=%s error=%s wait=%.0fs', label, attempt, type(error).__name__, delay)
            await asyncio.sleep(delay)
            continue
        if response.status_code in RETRY_STATUS and attempt < ATTEMPTS:
            wait = _retry_after(response)
            wait = delay if wait is None else wait
            log.warning('api_retry model=%s attempt=%s status=%s wait=%.0fs', label, attempt, response.status_code, wait)
            await asyncio.sleep(wait)
            continue
        if response.status_code == 429:
            raise RateLimited()
        response.raise_for_status()
        return response.json()
    raise RuntimeError('unreachable')
