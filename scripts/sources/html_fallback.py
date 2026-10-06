"""HTML 备用采集：RSS 失效时的降级方案。

抓官方/IR 页与 changelog 页，做链接与标题白名单、日期兜底解析；公司监控
（fetch_company_news）与通用 HTML 抓取（fetch_html）同属这一路，放在一起。"""

import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

try:
    from constants import COMPANY_ALIASES, COMPANY_BLACKLIST
    from content.classify import (
        detect_event_types, is_blacklisted, _is_chinese_outbound_title,
        _is_low_signal_company_title, _title_mentions_aliases, _title_mentions_company,
    )
    from content.dedupe import _is_same_event
    from content.util import (
        _cn_now, _cn_today, _parse_date, _recent_article_date,
        _extract_date_from_url, _extract_title_publisher, _same_host_url,
    )
    from sources.collect import _rss_date_metadata, _select_rss_entry_link
    from sources.http import fetch_url
    from sources.meta import _is_official_cfg, _with_source_meta
    from event_dates import publication_metadata
except ImportError:
    from scripts.constants import COMPANY_ALIASES, COMPANY_BLACKLIST
    from scripts.content.classify import (
        detect_event_types, is_blacklisted, _is_chinese_outbound_title,
        _is_low_signal_company_title, _title_mentions_aliases, _title_mentions_company,
    )
    from scripts.content.dedupe import _is_same_event
    from scripts.content.util import (
        _cn_now, _cn_today, _parse_date, _recent_article_date,
        _extract_date_from_url, _extract_title_publisher, _same_host_url,
    )
    from scripts.sources.collect import _rss_date_metadata, _select_rss_entry_link
    from scripts.sources.http import fetch_url
    from scripts.sources.meta import _is_official_cfg, _with_source_meta
    from scripts.event_dates import publication_metadata


# HTML 降级采集时过滤报告/评论类 URL（这类链接无情报价值）
HTML_SKIP_URL_PATTERNS = [
    '/reports/',          # 报告类
    '/review/',           # 回顾类
    'funding-review',     # 融资回顾
    'women-founders',     # 女性创始人报告
    'greater-china',      # 大中华区报告
    'southeast-asia',    # 东南亚报告
    'private-equity',     # PE 基金报告
    'lp-view',           # LP 视角（评论，非新闻）
    'startup-watch',     # 创业观察（长列表，非新闻）
]

HTML_SKIP_TITLE_PATTERNS = [
    'review', 'roundup', 'weekly recap', 'monthly recap',
    '2025 ', '2024 ', '2023 ',  # 历史回顾类标题
    ' Q4 ', ' Q1 ', ' Q2 ', ' Q3 ',  # 季度报告
]

OFFICIAL_SOURCE_LINK_PATTERNS = [
    '/news', '/press', '/media', '/investor', '/ir', '/financial', '/results',
    '/release', '/announcements', '/disclosure', '/reports', '/stories',
]

OFFICIAL_SOURCE_TITLE_PATTERNS = [
    'announces', 'announcement', 'launches', 'launched', 'partners', 'partnership',
    'expands', 'expansion', 'acquires', 'acquisition', 'results', 'revenue',
    'earnings', 'financial', 'quarter', 'annual', 'report', 'shareholder',
    'investor', 'strategy', 'strategic', 'platform', 'payment', 'commerce',
]

CHANGELOG_SOURCE_TITLE_PATTERNS = [
    'api', 'sdk', 'developer', 'changelog', 'release', 'released',
    'update', 'updates', 'new', 'beta', 'ga', 'graphql', 'webhook',
    'checkout', 'payments', 'merchant', 'admin', 'app', 'apps',
    'support', 'supports', 'enabled', 'enables', 'default', 'custom',
    'order', 'discount', 'import', 'duty', 'b2b',
]

OFFICIAL_SOURCE_SKIP_URL_PATTERNS = [
    'category=', '/about', '/products/', '/investor$', '/investors/$',
    '/quarterlyresults', '/annualreports', '/financial-information',
    '/corporategovernance', '/current-reports', '#results-center',
    '/sustainability/', '/financial-reports', '/presentations', '/reports/',
]

OFFICIAL_SOURCE_NAV_TITLES = {
    'investor relations', 'quarterly results', 'financial results',
    'financial information', 'current reports', 'main/about kaspi.kz',
    'kaspi.kz ecosystem', 'annual reports', 'corporate governance',
    'all stories business consumers & drivers people social impact & safety',
    'download grab media content', 'sustainability reports',
    'presentations and reports', 'sustainability',
}

OFFICIAL_SOURCE_NAV_PREFIXES = (
    'all stories business consumers',
    'latest stories business consumers',
)

def _is_changelog_cfg(cfg):
    return cfg.get('source_type') in {'changelog', 'developer_changelog'}

def _official_link_allowed(link, cfg):
    link_lower = (link or '').lower().rstrip('/')
    if any(pattern in link_lower for pattern in OFFICIAL_SOURCE_SKIP_URL_PATTERNS):
        return False
    if _is_changelog_cfg(cfg):
        return True
    patterns = [p.lower() for p in cfg.get('include_url_patterns', [])] or OFFICIAL_SOURCE_LINK_PATTERNS
    return any(pattern in link_lower for pattern in patterns)

def _official_title_allowed(title, cfg):
    clean_title = ' '.join((title or '').split())
    title_lower = clean_title.lower()
    if not clean_title or title_lower in OFFICIAL_SOURCE_NAV_TITLES:
        return False
    if any(title_lower.startswith(prefix) for prefix in OFFICIAL_SOURCE_NAV_PREFIXES):
        return False
    if len(clean_title) < 18 or len(clean_title) > 180:
        return False
    if _is_changelog_cfg(cfg):
        return any(pattern in title_lower for pattern in CHANGELOG_SOURCE_TITLE_PATTERNS)
    company = cfg.get('company_name') or cfg.get('source') or cfg.get('name', '')
    aliases = COMPANY_ALIASES.get(company, [company])
    has_alias = _title_mentions_aliases(clean_title, aliases)
    has_signal = any(pattern in title_lower for pattern in OFFICIAL_SOURCE_TITLE_PATTERNS)
    has_year = bool(re.search(r'\\b20\\d{2}\\b', title_lower))
    return has_alias or has_signal or has_year

def _select_official_articles(soup, cfg):
    selectors = [
        'article', '[class*=news]', '[class*=press]', '[class*=release]',
        '[class*=story]', '[class*=card]', '[class*=item]', 'a[href]'
    ]
    seen = set()
    articles = []
    base_host = urlparse(cfg['url']).netloc.lower().removeprefix('www.')
    for sel in selectors:
        for node in soup.select(sel):
            link_node = node if getattr(node, 'name', '') == 'a' else node.select_one('a[href]')
            href = (link_node.get('href') or '').strip() if link_node else ''
            if not href or href.startswith('#') or href.startswith('javascript'):
                continue
            absolute = urljoin(cfg['url'], href)
            parsed = urlparse(absolute)
            host = parsed.netloc.lower().removeprefix('www.')
            if not parsed.scheme.startswith('http') or not host or host != base_host:
                continue
            if not _official_link_allowed(absolute, cfg):
                continue
            # 优先取节点内嵌标题元素（卡片式整卡链接会带摘要文本超长），
            # 选择器与 fetch_html 循环标题提取保持一致，无标题元素时回退整卡文本
            title_el = node.select_one('h2,h3,h4,h5,.title,.entry-title,.post-title,.article-title')
            text_value = ' '.join((title_el or node).get_text(' ', strip=True).split())
            if not _official_title_allowed(text_value, cfg):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            articles.append(node)
    return articles

_EN_MONTHS = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

def _format_date_parts(year, month, day):
    try:
        dt = datetime(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None
    return dt.strftime('%Y-%m-%d')

def _extract_date_from_text(text):
    """Extract common official/IR date formats from titles or list text."""
    clean = ' '.join((text or '').split())
    if not clean:
        return None
    m = re.search(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', clean)
    if m:
        return _format_date_parts(m.group(1), m.group(2), m.group(3))
    m = re.search(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', clean)
    if m:
        return _format_date_parts(m.group(1), m.group(2), m.group(3))
    month_names = '|'.join(sorted(_EN_MONTHS, key=len, reverse=True))
    m = re.search(
        rf'\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b',
        clean,
        flags=re.I,
    )
    if m:
        return _format_date_parts(m.group(3), _EN_MONTHS[m.group(1).lower()], m.group(2))
    m = re.search(
        rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})\.?\s+(20\d{{2}})\b',
        clean,
        flags=re.I,
    )
    if m:
        return _format_date_parts(m.group(3), _EN_MONTHS[m.group(2).lower()], m.group(1))
    return None

def _extract_recent_month_day_date(text):
    clean = ' '.join((text or '').split())
    if not clean:
        return None
    month_names = '|'.join(sorted(_EN_MONTHS, key=len, reverse=True))
    m = re.search(
        rf'\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b',
        clean,
        flags=re.I,
    )
    if not m:
        return None
    now = _cn_now().date()
    month = _EN_MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    for year in (now.year, now.year - 1):
        candidate = _format_date_parts(year, month, day)
        parsed = _parse_date(candidate)
        if parsed and parsed <= now:
            return candidate
    return None

def _extract_official_article_date_meta(title, link, node_text='', observed_at=None):
    node_full_date = _extract_date_from_text(node_text)
    candidates = [
        (_extract_date_from_url(link), 'url_path', 'high'),
        (_extract_date_from_text(title), 'title_text', 'medium'),
        (node_full_date, 'body_text', 'low'),
        (_extract_recent_month_day_date(node_text) if not node_full_date else None, 'body_month_day', 'low'),
    ]
    rejected = None
    for candidate, source, confidence in candidates:
        if not candidate:
            continue
        metadata = publication_metadata(candidate, source, confidence, observed_at=observed_at)
        if metadata['published_at']:
            return metadata
        if metadata.get('scheduled_at') and rejected is None:
            rejected = metadata
    return rejected or publication_metadata('', 'observed_at', 'observed', observed_at=observed_at)

def _extract_official_article_date(title, link, node_text=''):
    return _extract_official_article_date_meta(title, link, node_text)['published_at'] or None

def _select_changelog_items(soup, cfg):
    """Extract dated changelog links directly; card selectors are often noisy."""
    results = []
    seen = set()
    max_items = cfg.get('max', 4)
    max_scan = max(cfg.get('max_scan', 40), 100)

    for link_el in soup.select('a[href]')[:max_scan]:
        if len(results) >= max_items:
            break
        link = _same_host_url(cfg['url'], link_el.get('href'))
        if not link or link in seen:
            continue
        if any(p in link.lower() for p in HTML_SKIP_URL_PATTERNS):
            continue
        if any(p in link.lower() for p in OFFICIAL_SOURCE_SKIP_URL_PATTERNS):
            continue

        title = link_el.get_text(' ', strip=True).lstrip('•·-–— ').strip()
        if not _official_title_allowed(title, cfg) or is_blacklisted(title, official=True):
            continue

        node_text = ''
        date_meta = publication_metadata('', 'observed_at', 'observed')
        article_date = None
        for parent in [link_el] + list(link_el.parents)[:6]:
            node_text = ' '.join(parent.get_text(' ', strip=True).split())
            date_meta = _extract_official_article_date_meta(title, link, node_text)
            article_date = date_meta['published_at'] or None
            if article_date:
                break
        # changelog 列表页同官方源：提取不到日期视为最新条目保留，能提取到且旧才滤
        if article_date and not _recent_article_date(article_date, days=2):
            continue

        types = detect_event_types(title)
        if types == ['other']:
            types = ['strategy']

        seen.add(link)
        results.append(_with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', '')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **date_meta,
        }, cfg))

    return results

def fetch_company_news(cfg):
    """
    从 Google News RSS 抓取特定公司的新闻
    只取当天/昨天的 + 有信号的事件 + 每公司最多3条
    """
    import urllib.parse
    query = urllib.parse.quote(cfg['query'] + ' when:2d')
    url = f'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
    body = fetch_url(url)
    cfg['_last_fetch_status'] = 'success' if body else 'failed'
    if not body: return []

    if not any(body.strip().startswith(x) or x in body[:300] for x in ['<?xml', '<rss', '<feed']):
        return []

    try:
        parsed = feedparser.parse(body)
    except Exception:
        return []

    today = _cn_today()
    yesterday = (_cn_now() - timedelta(days=1)).strftime('%Y-%m-%d')
    allowed_dates = {today, yesterday}

    results = []
    seen_company_events = []
    publisher_counts = {}
    max_items = cfg.get('max', 3)
    max_other = cfg.get('max_other', 1)
    other_count = 0

    for entry in parsed.entries:
        if len(results) >= max_items: break  # 每公司最多N条

        title = (entry.get('title') or '').strip()
        if len(title) < 15: continue
        publisher = _extract_title_publisher(title)

        if not _title_mentions_company(title, cfg):
            continue

        # 基础噪音过滤
        title_lower = f"{title} {publisher}".lower()
        if any(kw in title_lower for kw in COMPANY_BLACKLIST): continue
        if _is_low_signal_company_title(title):
            continue
        if cfg.get('region') == '中资' and not _is_chinese_outbound_title(title):
            continue
        if publisher and publisher_counts.get(publisher.lower(), 0) >= 1:
            continue

        link, link_repair = _select_rss_entry_link(entry, title)
        if not link: continue

        # 日期过滤：RSS日期优先，URL日期兜底
        date_meta = _rss_date_metadata(entry, link)
        article_date = date_meta['published_at'] or None
        if article_date and article_date not in allowed_dates:
            continue

        types = detect_event_types(title)
        if types[0] == 'other':
            if other_count >= max_other:
                continue
            other_count += 1

        # 图片：从 RSS media:content 或 media:thumbnail
        image_url = ''
        mc = entry.get('media_content', [])
        if mc:
            for m in mc:
                if m.get('url'):
                    image_url = m['url']
                    break
        if not image_url:
            mt = entry.get('media_thumbnail', [])
            if mt and mt[0].get('url'):
                image_url = mt[0]['url']

        item = _with_source_meta({
            'title': title,
            'url': link,
            'source': 'Google News',
            'source_detail': publisher,
            'publisher': publisher,
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': True,
            'company_name': cfg['name'],
            'origin_source_id': cfg.get('id') or cfg['name'],
            'observation_entity_id': cfg.get('entity_id') or cfg.get('id') or cfg['name'],
            'discovery_source': 'google_news',
            'publisher_source': publisher,
            'image_url': image_url,
            **link_repair,
            **date_meta,
        }, cfg)
        if any(_is_same_event(item, existing) for existing in seen_company_events):
            continue
        seen_company_events.append(item)
        if publisher:
            publisher_key = publisher.lower()
            publisher_counts[publisher_key] = publisher_counts.get(publisher_key, 0) + 1
        results.append(item)
    return results

def fetch_html(cfg):
    """从 HTML 页面提取文章列表（降级方案，针对各站点结构定制）"""
    body = fetch_url(cfg['url'])
    cfg['_last_fetch_status'] = 'success' if body else 'failed'
    if not body: return []

    soup = BeautifulSoup(body, 'html.parser')
    results = []

    # 根据来源选择器定制
    source = cfg['source']
    if _is_changelog_cfg(cfg):
        return _select_changelog_items(soup, cfg)
    if _is_official_cfg(cfg):
        articles = _select_official_articles(soup, cfg)
    elif source == 'DealStreetAsia':
        # DealStreetAsia: JS SPA，文章在特定 div 结构中
        # 尝试多种文章容器选择器
        selectors = [
            'article', '.post-card', '.deal-card', '.startup-card',
            '[class*=card]', '[class*=item]', '[class*=post]',
            '.listing article', '.archive article',
        ]
        articles = []
        for sel in selectors:
            found = soup.select(sel)
            if found:
                articles = found
                break
        # 也尝试从链接模式找文章：/2026/ 或包含 deal/startup/invest
        if not articles:
            all_links = soup.select('a[href]')
            art_links = []
            for a in all_links:
                href = a.get('href', '')
                if '/202' in href and any(x in href for x in ['/deals/', '/startups/', '/funding/', '/invest/']):
                    parent = a.find_parent()
                    if parent:
                        art_links.append(parent)
            if art_links:
                articles = art_links
    elif source == 'e27':
        # e27: 文章在特定列表结构中
        selectors = [
            'article', '.post', '.listing-item', '.article-item',
            '[class*=article]', '[class*=post]',
        ]
        articles = []
        for sel in selectors:
            found = soup.select(sel)
            if found:
                articles = found
                break
        # 也从链接中提取：e27.co/20xx/ 模式
        if not articles:
            all_links = soup.select('a[href]')
            art_links = []
            for a in all_links:
                href = a.get('href', '')
                if '/20' in href and ('startup' in href or 'funding' in href or 'investment' in href or 'series' in href):
                    parent = a.find_parent()
                    if parent:
                        art_links.append(parent)
            if art_links:
                articles = art_links
    else:
        # 通用回退
        articles = soup.select('article') or soup.select('.post') or soup.select('.article')
        if not articles:
            articles = soup.select('a[href]')

    max_items = cfg.get('max', 8)
    max_scan = cfg.get('max_scan', 15)
    for art in articles[:max_scan]:
        if len(results) >= max_items:
            break
        # 提取标题和链接
        title_el = art.select_one('h2,h3,h4,h5,.title,.entry-title,.post-title,.article-title') or art
        title = title_el.get_text(' ', strip=True).lstrip('•·-–— ').strip()
        if len(title) < 15 or is_blacklisted(title, official=_is_official_cfg(cfg) or bool(cfg.get('is_company'))): continue

        link_el = art.select_one('a') or (title_el if isinstance(title_el, object) else None)
        link = ''
        if link_el:
            link = (link_el.get('href') or '').strip()
        if not link or link.startswith('#') or link.startswith('javascript'): continue

        # 过滤非文章链接
        if any(x in link for x in ['/category/', '/tag/', '/author/', '/page/',
                                    'subscribe', 'newsletter', 'contact', '/cdn-cgi/']): continue
        # 过滤报告/评论类 URL
        if any(p in link.lower() for p in HTML_SKIP_URL_PATTERNS): continue
        # 过滤报告类标题
        title_lower = title.lower()
        if any(p.lower() in title_lower for p in HTML_SKIP_TITLE_PATTERNS): continue
        # 只保留绝对 URL 或同源链接
        if not link.startswith('http'):
            if link.startswith('/'):
                base = cfg['url'].split('/')[2]  # 提取域名
                link = 'https://' + base + link

        date_meta = publication_metadata('', 'observed_at', 'observed')
        article_date = None
        if _is_official_cfg(cfg):
            node_text = ' '.join(art.get_text(' ', strip=True).split())
            date_meta = _extract_official_article_date_meta(title, link, node_text)
            article_date = date_meta['published_at'] or None
            # 官方源列表页第一屏即最新：能提取到日期才按窗口过滤，提取不到视为新稿保留
            if article_date and not _recent_article_date(article_date, days=2):
                continue

        types = detect_event_types(title)
        results.append(_with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', 'Google News')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **date_meta,
        }, cfg))

    return results
