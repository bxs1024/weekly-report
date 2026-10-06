"""通用工具：日期、文本规范化、URL 判断。

无业务逻辑、无项目内依赖（只依赖标准库与 constants 词表），因此位于依赖链
最底层，sources/content/editorial 各层都可引用。
"""

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urljoin, urlparse

try:
    from constants import TITLE_STOPWORDS
except ImportError:
    from scripts.constants import TITLE_STOPWORDS

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))


def _cn_now():
    return datetime.now(SHANGHAI_TZ)

def _cn_today():
    return _cn_now().strftime('%Y-%m-%d')

def _parse_date(value):
    try:
        return datetime.strptime((value or '')[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None

def _recent_article_date(article_date, days=2):
    parsed = _parse_date(article_date)
    if not parsed:
        return True
    cutoff = (_cn_now() - timedelta(days=days)).date()
    return parsed >= cutoff

def _strip_title_source(title):
    """去掉标题末尾的媒体名尾缀，避免同事件因来源不同被拆成多条。"""
    title = (title or '').strip()
    for sep in [' - ', ' | ', ' — ', ' – ', ' —']:
        if sep in title:
            left, right = title.rsplit(sep, 1)
            if right and len(right) <= 40:
                return left.strip()
    return title

def _extract_title_publisher(title):
    """提取 Google News 标题尾部媒体名，保留真实来源用于控噪和展示。"""
    title = (title or '').strip()
    for sep in [' - ', ' | ', ' — ', ' – ', ' —']:
        if sep in title:
            left, right = title.rsplit(sep, 1)
            right = right.strip()
            if left.strip() and 1 < len(right) <= 40:
                return right
    return ''

def _normalize_text(text):
    text = _strip_title_source(text).lower()
    text = text.replace('&', ' and ')
    text = re.sub(r'[\u2018\u2019\u201c\u201d]', ' ', text)
    text = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def _title_tokens(title):
    tokens = []
    for token in _normalize_text(title).split():
        if token in TITLE_STOPWORDS:
            continue
        if len(token) <= 2 and token not in {'q1', 'q2', 'q3', 'q4', 'ai', 'ipo'}:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens

@lru_cache(maxsize=16384)
def _parse_iso_date(value):
    """缓存 'YYYY-MM-DD' 解析结果。

    性能：_dates_adjacent 被展示层去重以 O(n²) 调用，每次要跑两次
    datetime.strptime（CPython 下约 20µs/次），实测成为 build_display_context
    在修掉 _entity_key_info 之后的下一处瓶颈。日期字符串取值域很小，缓存
    命中率接近 100%。
    """
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None

def _same_host_url(base_url, href):
    absolute = urljoin(base_url, href or '')
    base_host = urlparse(base_url).netloc.lower().replace('www.', '')
    link_host = urlparse(absolute).netloc.lower().replace('www.', '')
    if not absolute.startswith('http') or base_host != link_host:
        return ''
    return absolute

def _is_http_url(url):
    return isinstance(url, str) and url.startswith(('http://', 'https://'))


def _extract_date_from_url(url):
    """从 URL 提取日期兜底，如 /2026/04/15/"""
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)', url or '')
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', url or '')
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None
