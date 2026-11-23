"""资讯源：都是免费、公开、不需要登录的接口或 RSS。

tier（来源可信度）：A 官方 / 公司公告；B 主流媒体和财经快讯；C 社交媒体、Telegram 频道、论坛（传闻多）。
每个源出错时自动退避（连续失败越多，下次等得越久，最长 30 分钟），不会拖垮其他源。

安全：只请求这里写死的地址，以及按关注列表拼出来的 Google 新闻 / 雅虎财经搜索地址；
用户能改的只有关注词（作为查询参数，会被 URL 编码）和 Telegram 频道名（只允许字母数字下划线）。
"""
import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote

import httpx

from . import config
from .text import clean

CHANNEL_RE = re.compile(r'^[A-Za-z0-9_]{4,40}$')


@dataclass
class Source:
    name: str
    label: str
    interval: int
    tier: str
    market: str                      # stock / crypto / macro / mixed
    build: Callable                  # (watch) -> list[(url, headers)]
    parse: Callable                  # (text, url) -> list[dict]
    enabled: bool = True
    next_run: float = 0.0
    fails: int = 0
    seen: bool = False               # 本次启动后是否已经成功抓过（第一次抓到的是积压，不推送）
    meta: dict = field(default_factory=dict)

    def backoff(self) -> None:
        self.fails += 1
        self.next_run = time.time() + min(1800, self.interval * (2 ** min(self.fails, 6)))

    def ok(self) -> None:
        self.fails = 0
        self.next_run = time.time() + self.interval


def item(source: str, uid, title: str, body: str = '', url: str = '', published: Optional[float] = None) -> dict:
    title = clean(title, 300)
    body = clean(body, 1500)
    if not title:
        title, body = body[:120], body
    return {'source': source, 'uid': str(uid)[:200], 'title': title, 'body': body if body != title else '',
            'url': url if url.startswith(('http://', 'https://')) else '', 'published': published or time.time()}


def safe_url(url: str) -> str:
    return url if isinstance(url, str) and url.startswith(('http://', 'https://')) else ''


# ---------- 解析器 ----------
def parse_wallstcn(text: str, url: str) -> list:
    data = json.loads(text)
    out = []
    for it in (data.get('data') or {}).get('items') or []:
        body = it.get('content_text') or clean(it.get('content') or '')
        title = it.get('title') or body
        out.append(item('wallstcn', it.get('id'), title, body, safe_url(it.get('uri') or ''),
                        float(it.get('display_time') or time.time())))
    return out


def parse_sina(text: str, url: str) -> list:
    data = json.loads(text)
    feed = (((data.get('result') or {}).get('data') or {}).get('feed') or {}).get('list') or []
    out = []
    for it in feed:
        ts = time.time()
        try:
            ts = datetime.strptime(it.get('create_time', ''), '%Y-%m-%d %H:%M:%S').timestamp()  # 北京时间，Mini PC 时区是 +0800
        except ValueError:
            pass
        out.append(item('sina', it.get('id'), it.get('rich_text') or '', '',
                        safe_url(it.get('docurl') or ''), ts))
    return out


def _rss_time(text: str) -> Optional[float]:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def parse_feed(name: str) -> Callable:
    """RSS 2.0 / Atom 通用解析。"""
    def parse(text: str, url: str) -> list:
        if '<!ENTITY' in text[:4000]:      # 拒绝带实体定义的 XML（防 XML 炸弹）
            raise ValueError('xml entity')
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
        out = []
        for node in root.iter():
            tag = node.tag.split('}')[-1]
            if tag not in ('item', 'entry'):
                continue
            get = {c.tag.split('}')[-1]: c for c in node}
            title = (get['title'].text or '') if 'title' in get else ''
            link = ''
            if 'link' in get:
                link = get['link'].text or get['link'].get('href') or ''
            body = ''
            for key in ('description', 'summary', 'content'):
                if key in get and (get[key].text or ''):
                    body = get[key].text
                    break
            when = None
            for key in ('pubDate', 'published', 'updated', 'date'):
                if key in get:
                    when = _rss_time(get[key].text or '')
                    if when:
                        break
            uid = (get['guid'].text if 'guid' in get else None) or (get['id'].text if 'id' in get else None) or link or title
            if name.startswith('gnews'):
                body = ''   # Google 新闻的描述只是标题链接，没用
            out.append(item(name, uid, title, body, safe_url(link.strip()), when))
        return out
    return parse


TG_MSG_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?data-post="([^"]+)".*?'
                       r'(?:<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>).*?'
                       r'<time datetime="([^"]+)"', re.S)


def parse_telegram(text: str, url: str) -> list:
    out = []
    for post, body, when in TG_MSG_RE.findall(text):
        body = re.sub(r'<br\s*/?>', ' ', body)
        ts = _rss_time(when)
        out.append(item('tg', post, clean(body, 300), '', f'https://t.me/{post}', ts))
    return out


# ---------- 源列表 ----------
def fixed(url: str, headers: Optional[dict] = None) -> Callable:
    return lambda watch: [(url, headers or {})]


def gnews_urls(lang: str) -> Callable:
    def build(watch: list) -> list:
        urls = []
        for w in watch:
            terms = (w['symbols'][:1] + w['keywords'])
            if lang == 'en':
                terms = [t for t in terms if t.isascii()]
            else:
                terms = [t for t in terms if not t.isascii()] or terms
            if not terms:
                continue
            q = ' OR '.join(f'"{t}"' if ' ' in t else t for t in terms[:4]) + ' when:1d'
            if lang == 'en':
                urls.append((f'https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en', {}))
            else:
                urls.append((f'https://news.google.com/rss/search?q={quote(q)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans', {}))
        return urls
    return build


def yahoo_urls(watch: list) -> list:
    syms = [s for w in watch if w['kind'] == 'stock' for s in w['symbols'] if re.fullmatch(r'[A-Z0-9.\-]{1,10}', s)]
    return [(f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={",".join(syms[i:i + 10])}&region=US&lang=en-US', {})
            for i in range(0, len(syms), 10)]


def telegram_urls(channels: list) -> Callable:
    return lambda watch: [(f'https://t.me/s/{c}', {}) for c in channels if CHANNEL_RE.match(c)]


# 2026-11 在 Mini PC 上实测有更新的公开频道（C 级，传闻多）
DEFAULT_TG = ['WalterBloomberg', 'WatcherGuru', 'BWEnews', 'wublockchainenglish', 'binance_announcements', 'unfolded']


def build_sources(env: dict) -> list:
    tg = [c.strip() for c in (env.get('RADAR_TG_CHANNELS') or ','.join(DEFAULT_TG)).split(',') if c.strip()]
    srcs = [
        Source('wallstcn', '华尔街见闻快讯', 20, 'B', 'mixed',
               fixed('https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&limit=30'),
               parse_wallstcn),
        Source('sina', '新浪财经 7x24', 30, 'B', 'mixed',
               fixed('https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=30'), parse_sina),
        Source('gnews_en', 'Google 新闻（英文）', 180, 'B', 'mixed', gnews_urls('en'), parse_feed('gnews_en')),
        Source('gnews_zh', 'Google 新闻（中文）', 300, 'B', 'mixed', gnews_urls('zh'), parse_feed('gnews_zh')),
        Source('yahoo', '雅虎财经个股新闻', 600, 'B', 'stock', yahoo_urls, parse_feed('yahoo')),
        Source('nvidia', '英伟达官方新闻稿', 300, 'A', 'stock',
               fixed('https://nvidianews.nvidia.com/releases.xml'), parse_feed('nvidia')),
        Source('cointelegraph', 'Cointelegraph', 120, 'B', 'crypto',
               fixed('https://cointelegraph.com/rss'), parse_feed('cointelegraph')),
        Source('theblock', 'The Block', 120, 'B', 'crypto',
               fixed('https://www.theblock.co/rss.xml'), parse_feed('theblock')),
        Source('coindesk', 'CoinDesk', 120, 'B', 'crypto',
               fixed('https://www.coindesk.com/arc/outboundfeeds/rss/'), parse_feed('coindesk')),
        Source('tg', 'Telegram 公开频道', 60, 'C', 'mixed', telegram_urls(tg), parse_telegram),
    ]
    disabled = {s.strip() for s in (env.get('RADAR_DISABLED_SOURCES') or '').split(',') if s.strip()}
    if not env.get('RADAR_DISABLED_SOURCES'):
        disabled = {'yahoo'}   # 雅虎从 Mini PC 一直 429，默认关闭
    for s in srcs:
        s.enabled = s.name not in disabled
    return srcs


async def fetch_text(client: httpx.AsyncClient, url: str, headers: dict) -> str:
    h = {'User-Agent': config.USER_AGENT, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', **headers}
    async with client.stream('GET', url, headers=h, timeout=config.FETCH_TIMEOUT, follow_redirects=True) as r:
        r.raise_for_status()
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > config.MAX_RESPONSE_BYTES:
                raise ValueError('response too large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        return raw.decode(r.encoding or 'utf-8', errors='replace')


async def run_source(client: httpx.AsyncClient, src: Source, watch: list) -> list:
    """抓一个源的所有地址，返回资讯列表（失败的地址抛出最后一个错误）。"""
    out, error = [], None
    targets = src.build(watch)
    for url, headers in targets:
        try:
            text = await fetch_text(client, url, headers)
            for it in src.parse(text, url):
                it['source'] = src.name
                it['tier'] = src.tier
                it['market'] = src.market
                out.append(it)
        except (httpx.HTTPError, ValueError, ET.ParseError, KeyError, TypeError, AttributeError) as e:
            error = e
        if len(targets) > 1:
            await asyncio.sleep(0.5)   # 对同一网站别打太快
    if error and not out:
        raise error
    return out
