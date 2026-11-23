"""文本工具：清洗 HTML、指纹去重、关注词匹配、规则打分（DeepSeek 不可用时的兜底）。"""
import hashlib
import html
import re
from typing import Iterable

TAG_RE = re.compile(r'<[^>]+>')
SPACE_RE = re.compile(r'\s+')
NORM_RE = re.compile(r'[^0-9a-z一-鿿]+')
PREFIX_RE = re.compile(r'^(【[^】]{0,40}】|\[[^\]]{0,40}\]|财联社\d+月\d+日电[，,]?|格隆汇\d+月\d+日[丨|]?)')

# 可能影响股价 / 币价的关键词（规则打分用）
URGENT_WORDS = [
    '辞职', '离职', '回归', '重返', '接任', '去世', '被捕', '调查', '起诉', '制裁', '禁令', '暂停交易', '停牌', '退市',
    '收购', '并购', '合并', '拆分', '破产', '违约', '财报', '业绩预告', '下调', '上调', '指引', '回购', '增持', '减持',
    '黑客', '被盗', '攻击', '漏洞', '上线', '下架', '上币', 'ETF', '批准', '否决', '降息', '加息', '紧急', '突发', '重磅',
    'resign', 'steps down', 'returns', 'return as', 'appointed', 'ceo', 'acquire', 'acquisition', 'merger', 'bankrupt',
    'investigation', 'lawsuit', 'sec ', 'halt', 'delist', 'guidance', 'earnings', 'hack', 'exploit', 'listing',
    'approve', 'breaking', 'urgent', 'sanction', 'ban',
]
RUMOR_WORDS = ['据传', '传闻', '消息人士', '知情人士', '据悉', '网传', '爆料', '小道', 'rumor', 'rumour', 'reportedly',
               'sources say', 'people familiar', 'according to sources', 'unconfirmed', 'speculation']


def clean(text: str, limit: int = 1200) -> str:
    text = html.unescape(TAG_RE.sub(' ', text or ''))
    return SPACE_RE.sub(' ', text).strip()[:limit]


def norm(text: str) -> str:
    return NORM_RE.sub('', PREFIX_RE.sub('', (text or '').strip()).lower())


def fingerprint(title: str, body: str = '') -> str:
    base = norm(title) or norm(body)
    return hashlib.sha1(base[:60].encode()).hexdigest()[:16]


def bigrams(text: str) -> set:
    t = norm(text)[:120]
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a: str, b: str, threshold: float = 0.55) -> bool:
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= threshold


def _term_re(term: str) -> re.Pattern:
    term = term.strip()
    if re.fullmatch(r'[A-Za-z0-9.\- ]+', term):
        # 英文 / 代码按单词边界匹配，避免 "ETH" 匹配到 "method"；纯大写短代码区分大小写
        flags = 0 if (term.isupper() and len(term) <= 5) else re.I
        return re.compile(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', flags)
    return re.compile(re.escape(term), re.I)


def match_watch(text: str, watch: Iterable[dict]) -> list:
    """返回命中的关注项名字。"""
    hits = []
    for w in watch:
        terms = list(w.get('keywords') or []) + list(w.get('symbols') or []) + [w['name']]
        for term in terms:
            if term and len(term.strip()) >= 2 and _term_re(term).search(text):
                hits.append(w['name'])
                break
    return hits


def has_any(text: str, words: Iterable[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def rule_score(text: str, matched: list, tier: str) -> int:
    urgent = has_any(text, URGENT_WORDS)
    if matched and urgent:
        return 4
    if matched:
        return 3
    if urgent and tier in ('A', 'B'):
        return 2
    return 1
