"""Codex 用的 MCP 小服务（stdio，JSON-RPC 2.0，每行一条消息）。

由 codex exec 按 -c mcp_servers.repo.* 启动；只把请求转给机器人进程开的本机只读通道（agents/tool_gateway.py），
自己不碰任何密钥。端口和口令从环境变量 AI_TOOLS_PORT / AI_TOOLS_SECRET 读取。只用标准库。
"""
import json
import os
import socket
import sys

PORT = int(os.environ.get('AI_TOOLS_PORT', '0'))
SECRET = os.environ.get('AI_TOOLS_SECRET', '')


def gateway(request: dict) -> dict:
    request = dict(request, secret=SECRET)
    with socket.create_connection(('127.0.0.1', PORT), timeout=60) as conn:
        conn.sendall((json.dumps(request, ensure_ascii=False) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b'{}')


def handle(msg: dict):
    method = msg.get('method')
    if method == 'initialize':
        return {'protocolVersion': (msg.get('params') or {}).get('protocolVersion', '2025-06-18'),
                'capabilities': {'tools': {}}, 'serverInfo': {'name': 'owner-github', 'version': '1.0'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': gateway({'op': 'list'}).get('tools', [])}
    if method == 'tools/call':
        params = msg.get('params') or {}
        reply = gateway({'op': 'call', 'name': params.get('name'), 'args': params.get('arguments') or {}})
        text = reply.get('text') or ('出错：' + str(reply.get('error', '没有结果')))
        return {'content': [{'type': 'text', 'text': text}], 'isError': text.startswith('出错：')}
    raise LookupError(method)


def main() -> None:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if 'id' not in msg:      # 通知（如 notifications/initialized）不用回
            continue
        try:
            out = {'jsonrpc': '2.0', 'id': msg['id'], 'result': handle(msg)}
        except LookupError:
            out = {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'method not found'}}
        except (OSError, ValueError) as error:
            out = {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32000, 'message': type(error).__name__}}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
