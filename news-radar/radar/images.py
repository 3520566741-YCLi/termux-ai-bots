"""资讯配图：取来源给的图（RSS 媒体图、Telegram 图片），没有就读原文的 og:image，缓存到 data/radar/img/。

- 只给 3 分以上、6 小时内的消息和收藏的消息找图，省流量；Google 新闻（跳转页）和 Telegram 原文不读 og:image；
- 安全：只走 http/https，每一跳都检查目标不是内网/本机地址（防 SSRF），网页最多读 512KB，图片最多 4MB，
  只收 jpeg/png/webp/gif；
- 装了 Pillow 就缩到宽 900 像素、转成 JPEG，并记下宽高（网页预留比例不跳动）；没装就原样存；
- 没收藏的图第二天由每日清理删掉（store.daily_trim），收藏的永远留着。
"""
import asyncio
import hashlib
import io
import ipaddress
import logging
import re
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import config, store

log = logging.getLogger('radar')
MAX_HTML = 512 * 1024
MAX_IMG = 4 * 1024 * 1024
MAX_W = 900
TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
NAME_RE = re.compile(r'^[0-9a-f]{40}\.(jpg|png|webp|gif)$')
OG_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]*>', re.I)
CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)
NO_OG = ('gnews_en', 'gnews_zh', 'tg')
MAX_PIXELS = 30_000_000


def img_dir() -> Path:
    d = config.DATA / 'img'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if ip.version == 6 and ip.ipv4_mapped:      # ::ffff:10.0.0.1 这种写法也按 IPv4 查
            ip = ip.ipv4_mapped
        # 只放行公网地址：is_global 已排除内网、本机、链路本地、保留段、Tailscale 的 100.64/10 等；NAT64 另外挡
        if not ip.is_global or ip.is_multicast or (ip.version == 6 and ip in ipaddress.ip_network('64:ff9b::/96')):
            return False
    return True


async def _get(client: httpx.AsyncClient, url: str, limit: int, accept: str) -> tuple:
    """自己跟跳转（最多 3 次），每一跳都查地址；返回 (最终地址, content-type, 内容)。"""
    for _ in range(4):
        u = urlparse(url)
        if u.scheme not in ('http', 'https') or not u.hostname:
            raise ValueError('bad url')
        if not await asyncio.to_thread(_public, u.hostname):
            raise ValueError('private address')
        async with client.stream('GET', url, timeout=12, follow_redirects=False,
                                 headers={'User-Agent': config.USER_AGENT, 'Accept': accept}) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get('location'):
                url = urljoin(url, r.headers['location'])
                continue
            r.raise_for_status()
            if int(r.headers.get('content-length') or 0) > limit:
                raise ValueError('too large')
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    if accept.startswith('text'):
                        break   # 网页只要开头的 <head>
                    raise ValueError('too large')
                chunks.append(chunk)
            return url, r.headers.get('content-type', '').split(';')[0].strip().lower(), b''.join(chunks)
    raise ValueError('too many redirects')


def og_image(html: str, base: str) -> str:
    for tag in OG_RE.findall(html[:MAX_HTML]):
        m = CONTENT_RE.search(tag)
        if m:
            url = urljoin(base, m.group(1).replace('&amp;', '&').strip())
            if url.startswith(('http://', 'https://')):
                return url
    return ''


def shrink(data: bytes, ctype: str) -> tuple:
    """(数据, 扩展名, 宽, 高)。有 Pillow 就缩小转 JPEG（GIF 动图原样）。"""
    try:
        from PIL import Image
    except ImportError:
        return data, TYPES[ctype], 0, 0
    import warnings
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            # 只认这四种格式（不让 EPS/PSD 等走别的解码器），先看尺寸再解码，防“小文件解出超大图”吃光内存
            im = Image.open(io.BytesIO(data), formats=['JPEG', 'PNG', 'WEBP', 'GIF'])
            if im.size[0] * im.size[1] > MAX_PIXELS:
                raise ValueError('too many pixels')
            im.draft('RGB', (MAX_W * 2, MAX_W * 2))   # JPEG 直接按小尺寸解码，省内存
            im.load()
    except ValueError:
        raise
    except Exception:
        raise ValueError('bad image')
    w, h = im.size
    if w < 120 or h < 80:
        raise ValueError('too small')   # 图标、追踪像素
    if ctype == 'image/gif' and getattr(im, 'is_animated', False):
        return data, '.gif', w, h
    if w > MAX_W:
        im = im.resize((MAX_W, max(1, round(h * MAX_W / w))))
    if im.mode not in ('RGB', 'L'):
        bg = Image.new('RGB', im.size, (255, 255, 255))
        im = im.convert('RGBA')
        bg.paste(im, mask=im.split()[-1])
        im = bg
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=80, optimize=True, progressive=True)
    return out.getvalue(), '.jpg', im.size[0], im.size[1]


async def fetch_for(client: httpx.AsyncClient, it: dict) -> tuple:
    """返回 (文件名, 宽, 高)；找不到图抛 ValueError。"""
    src = it.get('image_url') or ''
    if not src and it.get('url') and it['source'] not in NO_OG:
        final, ctype, body = await _get(client, it['url'], MAX_HTML, 'text/html,application/xhtml+xml')
        if 'html' not in ctype:
            raise ValueError('not html')
        src = og_image(body.decode('utf-8', errors='replace'), final)
    if not src:
        raise ValueError('no image')
    _final, ctype, data = await _get(client, src, MAX_IMG, 'image/webp,image/jpeg,image/png,image/gif')
    if ctype not in TYPES:
        raise ValueError('not image')
    data, ext, w, h = await asyncio.to_thread(shrink, data, ctype)
    name = hashlib.sha1(src.encode()).hexdigest() + ext
    path = img_dir() / name
    if not path.exists():
        tmp = path.with_suffix('.part')
        tmp.write_bytes(data)
        tmp.replace(path)
    return name, w, h


async def process(client: httpx.AsyncClient, items: list) -> int:
    sem = asyncio.Semaphore(3)
    got = 0

    async def one(it):
        nonlocal got
        async with sem:
            try:
                name, w, h = await fetch_for(client, it)
                state = (name, w, h, 1)
                got += 1
            except Exception:   # 任何错都记成“没图”，不会每 15 秒重试同一条
                state = ('', 0, 0, 2)
        with store.opened() as db:
            db.execute('UPDATE items SET image=?, image_w=?, image_h=?, img_state=? WHERE id=?', (*state, it['id']))
    await asyncio.gather(*(one(it) for it in items))
    return got


def pending(limit: int = 8) -> list:
    now = time.time()
    with store.opened() as db:
        return [dict(r) for r in db.execute(
            'SELECT id, source, url, image_url FROM items WHERE img_state=0 AND dup_of IS NULL AND trimmed=0 AND '
            '((score >= 3 AND fetched > ?) OR saved IS NOT NULL) ORDER BY saved IS NULL, score DESC, id DESC LIMIT ?',
            (now - 6 * 3600, limit))]


def sweep() -> int:
    """删掉库里已经没人用的图片文件（没收藏的消息被每日清理后）。只动 data/radar/img/ 里本程序生成的文件。"""
    with store.opened() as db:
        used = {r['image'] for r in db.execute("SELECT image FROM items WHERE image != ''")}
    n = 0
    for p in img_dir().iterdir():
        stale = time.time() - p.stat().st_mtime > 3600
        if stale and re.fullmatch(r'[0-9a-f]{40}\.part', p.name):   # 写到一半中断留下的临时文件
            p.unlink(missing_ok=True)
            continue
        if NAME_RE.match(p.name) and p.name not in used and stale:
            p.unlink(missing_ok=True)
            n += 1
    return n
