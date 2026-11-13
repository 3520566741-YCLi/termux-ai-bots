import base64
import re
from typing import Optional, Sequence

import httpx

from agents.base import Image, Reply
from agents.http import post_json
from agents.tools import MAX_CALLS_PER_ROUND, SKIPPED, Toolkit, dumps_args
from config import Config, REQUEST_TIMEOUT

USAGE_KEYS = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
SYSTEM = '请清晰、简洁地回答用户，使用用户的语言。'
TOOL_HINT = ('你可以用工具读取主人的 GitHub 仓库（含私人仓库，只读）'
             '，以及用 web_search 上网查最新信息。问题涉及主人的项目或最新信息时先用工具查，再回答。')
NOTION_HINT = '你还可以用 notion_ 开头的工具查看主人的 Notion 页面。'
NOTION_WRITE_HINT = ('主人这次要求保存：可以在“AI 笔记”页下新建页面或追加内容；保存前把内容整理清楚，'
                     '保存后把页面链接告诉主人。')
MAX_TOOL_ROUNDS = 8
# DeepSeek 偶尔把工具调用写成正文里的标记（<｜DSML｜function_calls> / invoke name=…），不能直接发给用户。
MARKUP_RE = re.compile(r'DSML|<\s*[｜|]|invoke\s+name=|function_calls|tool_calls?>', re.I)
NO_MORE_TOOLS = ('（系统提示：查询次数已用完，不要再调用任何工具，也不要输出工具调用标记。'
                 '请直接根据上面已经查到的信息用中文回答；查不到的部分说明还缺什么。）')


def strip_markup(text: str) -> str:
    """去掉含工具调用标记的行，只留正常文字。"""
    return '\n'.join(line for line in text.splitlines() if not MARKUP_RE.search(line)).strip()


class DeepSeekAgent:
    def __init__(self, config: Config, client: httpx.AsyncClient, toolkit: Optional[Toolkit] = None):
        self.config = config
        self.client = client
        self.toolkit = toolkit

    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None,
                      allow_tools: bool = False, notion: str = '') -> Reply:
        """allow_tools：是否给工具（只有主人才给）；notion：''/'read'/'write'，在 allow_tools 之下再分级。"""
        content = text
        if image:
            # 官方图像理解格式：user 消息的 content 为块数组，图片用 base64 data URL。
            url = f'data:{image.mime};base64,' + base64.b64encode(image.data).decode()
            content = [{'type': 'text', 'text': text}, {'type': 'image_url', 'image_url': {'url': url}}]
        tools = self.toolkit.openai_tools(notion=notion) if self.toolkit and allow_tools else []
        allowed = {t['function']['name'] for t in tools}
        writes = bool(allowed & {'notion_create_page', 'notion_append'})
        has_notion = any(n.startswith('notion_') for n in allowed)
        hint = (TOOL_HINT if tools else '') + (NOTION_HINT if has_notion else '') + (NOTION_WRITE_HINT if writes else '')
        messages = [{'role': 'system', 'content': SYSTEM + hint},
                    *history, {'role': 'user', 'content': content}]
        usage: dict = {}
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            # 整理成笔记保存到 Notion 时内容较长，给到 2000
            body = {'model': self.config.model, 'messages': messages, 'thinking': {'type': 'disabled'},
                    'max_tokens': 2000 if has_notion else 700, 'stream': False}
            if tools and round_no < MAX_TOOL_ROUNDS:   # 最后一轮不再给工具，逼它给出答案
                body['tools'] = tools
            data = await post_json(self.client, self.config.base_url + '/chat/completions',
                                   headers={'Authorization': 'Bearer ' + self.config.key},
                                   json=body, timeout=REQUEST_TIMEOUT, label='deepseek')
            raw = data.get('usage') or {}
            for k in USAGE_KEYS:
                if isinstance(raw.get(k), int):
                    usage[k] = usage.get(k, 0) + raw[k]
            message = data['choices'][0]['message']
            calls = message.get('tool_calls') or []
            if not calls:
                break
            # 执行模型要求的工具，把结果按 OpenAI 格式接回去再问一次
            messages.append({'role': 'assistant', 'content': message.get('content') or '', 'tool_calls': calls})
            for i, call in enumerate(calls):
                if i >= MAX_CALLS_PER_ROUND or not self.toolkit:
                    # 超出上限的不执行，但每个 tool_call_id 都要有对应的 tool 消息，否则 API 不接受这段历史
                    messages.append({'role': 'tool', 'tool_call_id': call.get('id', ''), 'content': SKIPPED})
                    continue
                fn = call.get('function') or {}
                result = await self.toolkit.call(fn.get('name', ''), dumps_args(fn.get('arguments')), allowed)
                messages.append({'role': 'tool', 'tool_call_id': call.get('id', ''), 'content': result})
                usage['tool_calls'] = usage.get('tool_calls', 0) + 1
        answer = message.get('content') if isinstance(message.get('content'), str) else ''
        if calls or MARKUP_RE.search(answer):
            # 还想继续查（或把工具调用写进了正文）：不给工具再问一次，要它根据已有结果直接回答
            messages.append({'role': 'user', 'content': NO_MORE_TOOLS})
            data = await post_json(self.client, self.config.base_url + '/chat/completions',
                                   headers={'Authorization': 'Bearer ' + self.config.key},
                                   json={'model': self.config.model, 'messages': messages,
                                         'thinking': {'type': 'disabled'}, 'max_tokens': 2000,
                                         'stream': False}, timeout=REQUEST_TIMEOUT, label='deepseek')
            raw = data.get('usage') or {}
            for k in USAGE_KEYS:
                if isinstance(raw.get(k), int):
                    usage[k] = usage.get(k, 0) + raw[k]
            answer = data['choices'][0]['message'].get('content') or ''
        answer = strip_markup(answer)
        if not answer:
            raise ValueError('模型返回空回复')
        return Reply(answer, usage)
