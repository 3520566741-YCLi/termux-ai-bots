"""给 Codex 用的本机只读工具通道：令牌留在机器人进程里，Codex 只拿到一个临时端口和随机口令。

每次 Codex 回答问题时开启，答完关闭；只监听 127.0.0.1，每行一个 JSON 请求：
  {"secret": "...", "op": "list"}                       → {"tools": [...]}
  {"secret": "...", "op": "call", "name": "...", "args": {...}} → {"text": "..."}
只提供 GitHub 只读工具（Codex 自己有网页搜索）。
"""
import asyncio
import json
import secrets

from agents.tools import Toolkit


class ToolGateway:
    def __init__(self, toolkit: Toolkit):
        self.toolkit = toolkit
        self.secret = secrets.token_hex(16)
        self.port = 0
        self.server = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 30)
            request = json.loads(line or b'{}')
            if not isinstance(request, dict) or not secrets.compare_digest(str(request.get('secret', '')), self.secret):
                reply = {'error': 'unauthorized'}
            elif request.get('op') == 'list':
                reply = {'tools': self.toolkit.mcp_tools(web=False)}
            elif request.get('op') == 'call' and request.get('name') in self.toolkit.names(web=False):
                args = request.get('args') if isinstance(request.get('args'), dict) else {}
                reply = {'text': await self.toolkit.call(request['name'], args)}
            else:
                reply = {'error': 'unknown request'}
            writer.write((json.dumps(reply, ensure_ascii=False) + '\n').encode())
            await writer.drain()
        except (asyncio.TimeoutError, ValueError, ConnectionError):
            pass
        finally:
            writer.close()
