"""网页、日报、讨论文案共用的 DeepSeek 调用（同步，给网页线程和 asyncio.to_thread 用）。

- 关掉“思考”，省钱也快；所有调用都记进 usage 表（按用途分开记账）；
- 新闻正文只当数据，模型输出只按约定格式解析。
"""
import json
from datetime import datetime, timedelta, timezone

import httpx

from . import store

BJ = timezone(timedelta(hours=8))


class LLMError(Exception):
    pass


def is_peak(now: datetime = None) -> bool:
    """DeepSeek 高峰（全价）= 北京时间工作日 9–12 点、14–18 点；其余半价。"""
    n = (now or datetime.now(BJ)).astimezone(BJ)
    if n.weekday() >= 5:
        return False
    h = n.hour + n.minute / 60
    return 9 <= h < 12 or 14 <= h < 18


def chat(env: dict, messages: list, kind: str, *, max_tokens: int = 800, temperature: float = 0.3,
         json_mode: bool = False, tools: list = None, timeout: float = 90, client: httpx.Client = None) -> dict:
    """返回 DeepSeek 的 message（dict）。失败抛 LLMError。kind 是记账用途：digest / chat / draft。"""
    if not env.get('DEEPSEEK_API_KEY'):
        raise LLMError('没有配置 DeepSeek Key')
    body = {'model': env.get('DEEPSEEK_MODEL') or 'deepseek-chat', 'messages': messages, 'stream': False,
            'max_tokens': max_tokens, 'temperature': temperature}
    if 'reasoner' not in body['model']:
        body['thinking'] = {'type': 'disabled'}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    if tools:
        body['tools'] = tools
    own = client is None
    client = client or httpx.Client()
    try:
        r = client.post((env.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/') + '/chat/completions',
                        json=body, timeout=timeout, headers={'Authorization': 'Bearer ' + env['DEEPSEEK_API_KEY']})
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        raise LLMError(f'DeepSeek 返回 {e.response.status_code}') from None
    except (httpx.HTTPError, ValueError) as e:
        raise LLMError(f'DeepSeek 连接失败：{type(e).__name__}') from None
    finally:
        if own:
            client.close()
    usage = data.get('usage') or {}
    with store.opened() as db:
        store.bump_usage(db, kind, int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0))
    try:
        return data['choices'][0]['message']
    except (KeyError, IndexError, TypeError):
        raise LLMError('DeepSeek 返回格式不对') from None


def chat_json(env: dict, system: str, user: str, kind: str, **kw) -> dict:
    msg = chat(env, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], kind,
               json_mode=True, **kw)
    try:
        out = json.loads(msg.get('content') or '{}')
    except ValueError:
        raise LLMError('DeepSeek 没按格式输出') from None
    if not isinstance(out, dict):
        raise LLMError('DeepSeek 没按格式输出')
    return out
