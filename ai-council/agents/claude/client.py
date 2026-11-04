"""Claude（Anthropic 官方 API，按量计费）问答、看图和联网搜索。

手机 Termux 装不了官方 Python SDK 依赖的 Rust 组件（pydantic-core、jiter 没有安卓预编译包），
所以和 DeepSeek 一样用 httpx 直接调用 /v1/messages。
- anthropic-version 2026-10-21：不能带 temperature / top_p / top_k。
- fallbacks: "default"（beta server-side-fallback-2026-07-01）：被安全分类器拒绝时由官方换模型重试。
- 联网：官方网页搜索工具 web_search_20260209（在官方服务器上执行），每次提问最多搜 MAX_SEARCHES 次，
  每次搜索另收 $0.01。服务器端工具循环太长时会返回 pause_turn，原样接着请求即可。
- GitHub：agents/tools.py 的只读工具（客户端工具，在手机上执行）；stop_reason 为 tool_use 时执行并把结果接回。
- 思考默认自适应；聊天用 effort=low，省 token、回得快。
"""
import base64
from typing import Optional, Sequence

import httpx

from agents.base import Image, Reply
from agents.tools import Toolkit
from config import Config, REQUEST_TIMEOUT

API_VERSION = '2026-10-21'
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
SYSTEM = ('请清晰、简洁地回答用户，使用用户的语言，适合手机阅读。'
          '问题涉及最新信息（新闻、价格、天气、日期、版本、比分等）时，先用网页搜索核实再回答，不要只凭记忆。')
GITHUB_HINT = '你可以用 github_ 开头的工具读取主人的 GitHub 仓库（含私人仓库，只读）；问题涉及主人的项目时先查仓库再回答。'
MAX_TOKENS = 4000
MAX_SEARCHES = 3
MAX_CONTINUES = 8   # pause_turn / 工具调用最多接着请求几次


class ClaudeRefused(Exception):
    """Claude（及备用模型）拒绝回答。"""


class ClaudeAgent:
    def __init__(self, config: Config, client: httpx.AsyncClient, effort: str = 'low', search: bool = True,
                 toolkit: Optional[Toolkit] = None):
        self.config = config
        self.client = client
        self.effort = effort
        self.search = search
        self.toolkit = toolkit

    async def _post(self, messages: list) -> dict:
        body = {
            'model': self.config.model,
            'max_tokens': MAX_TOKENS,
            'system': SYSTEM + (GITHUB_HINT if self.toolkit and self.toolkit.names(web=False) else ''),
            'messages': messages,
            'output_config': {'effort': self.effort},
            'fallbacks': 'default',
        }
        tools = [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': MAX_SEARCHES}] if self.search else []
        if self.toolkit:
            tools += self.toolkit.anthropic_tools(web=False)   # 网页搜索用官方的，这里只加 GitHub 工具
        if tools:
            body['tools'] = tools
        response = await self.client.post(
            self.config.base_url,
            headers={'x-api-key': self.config.key, 'anthropic-version': API_VERSION,
                     'anthropic-beta': FALLBACK_BETA, 'content-type': 'application/json'},
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None) -> Reply:
        content: object = text
        if image:
            data = base64.b64encode(image.data).decode()
            content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': image.mime, 'data': data}},
                       {'type': 'text', 'text': text}]
        messages = [*history, {'role': 'user', 'content': content}]
        parts: list[str] = []
        totals = {'in': 0, 'out': 0, 'hit': 0, 'searches': 0}
        for _ in range(MAX_CONTINUES + 1):
            data = await self._post(messages)
            raw = data.get('usage') or {}
            hit = int(raw.get('cache_read_input_tokens') or 0)
            totals['in'] += int(raw.get('input_tokens') or 0) + hit + int(raw.get('cache_creation_input_tokens') or 0)
            totals['out'] += int(raw.get('output_tokens') or 0)
            totals['hit'] += hit
            totals['searches'] += int((raw.get('server_tool_use') or {}).get('web_search_requests') or 0)
            if data.get('stop_reason') == 'refusal':
                raise ClaudeRefused()
            blocks = data.get('content') or []
            stop = data.get('stop_reason')
            if stop == 'tool_use' and self.toolkit:
                # 执行 GitHub 工具，结果全部放在同一条 user 消息里接回去
                results = []
                for b in blocks:
                    if b.get('type') == 'tool_use':
                        text = await self.toolkit.call(b.get('name', ''), b.get('input') or {})
                        results.append({'type': 'tool_result', 'tool_use_id': b.get('id'), 'content': text,
                                        **({'is_error': True} if text.startswith('出错：') else {})})
                messages = [*messages, {'role': 'assistant', 'content': blocks}, {'role': 'user', 'content': results}]
                continue
            parts.append(''.join(b.get('text', '') for b in blocks if b.get('type') == 'text'))
            if stop != 'pause_turn':
                break
            # 服务器端搜索还没做完：把这段回答原样接上，再请求一次让它继续
            messages = [*messages, {'role': 'assistant', 'content': blocks}]
        answer = ''.join(parts).strip()
        if not answer:
            raise ValueError('模型返回空回复')
        if data.get('stop_reason') == 'max_tokens':
            answer += '\n[回答过长，已截断]'
        # 换成和 DeepSeek 相同的字段名，日志与 AI 中枢“今日花费”通用：输入合计含缓存读写。
        usage = {'prompt_tokens': totals['in'], 'completion_tokens': totals['out'],
                 'prompt_cache_hit_tokens': totals['hit'], 'web_searches': totals['searches']}
        return Reply(answer, usage)
