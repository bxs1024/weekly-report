"""RSS 采集：条目解析、链接择优、抓取入口。

原 fetch_news.py 的「工具函数」与「采集」两段实际是一体——前者只服务 RSS
条目解析，后者是 RSS 抓取入口，合并成一个模块。"""

import re
from datetime import datetime
from urllib.parse import urlparse

import feedparser
from bs4 import BeautifulSoup

try:
    from content.classify import detect_event_types, is_blacklisted, _is_vertical_source
    from content.util import _extract_date_from_url, _recent_article_date
    from sources.http import fetch_url
    from sources.meta import _is_official_cfg, _with_source_meta
    from event_dates import publication_metadata
    from scope_gate import apply_scope_contract
except ImportError:
    from scripts.content.classify import detect_event_types, is_blacklisted, _is_vertical_source
    from scripts.content.util import _extract_date_from_url, _recent_article_date
    from scripts.sources.http import fetch_url
    from scripts.sources.meta import _is_official_cfg, _with_source_meta
    from scripts.event_dates import publication_metadata
    from scripts.scope_gate import apply_scope_contract


def _parse_rss_date(item):
    """从 RSS/Atom 条目提取文章发布日期，返回 ISO 格式字符串，失败返回 None"""
    # 已废弃：feedparser 自动标准化日期，保留接口兼容
    return None

def _rss_date_metadata(entry, link, observed_at=None):
    candidates = []
    if entry.get('published_parsed'):
        candidates.append((datetime(*entry['published_parsed'][:3]).strftime('%Y-%m-%d'), 'rss_published', 'high'))
    if entry.get('updated_parsed'):
        candidates.append((datetime(*entry['updated_parsed'][:3]).strftime('%Y-%m-%d'), 'rss_updated', 'medium'))
    candidates.append((_extract_date_from_url(link), 'url_path', 'high'))
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

RSS_URL_STOPWORDS = {
    'www', 'com', 'org', 'net', 'html', 'htm', 'amp', 'article', 'news',
    'post', 'posts', 'the', 'and', 'for', 'with', 'from', 'into', 'after',
}

def _rss_candidate_links(entry):
    candidates = []

    def add(value):
        value = (value or '').strip()
        if value.startswith(('http://', 'https://')) and value not in candidates:
            candidates.append(value)

    link_value = entry.get('link', '')
    if isinstance(link_value, dict):
        add(link_value.get('href'))
    else:
        add(link_value)
    for link in entry.get('links', []) or []:
        if isinstance(link, dict):
            add(link.get('href'))
    add(entry.get('id'))
    add(entry.get('guid'))
    return candidates

def _rss_url_match_score(title, url):
    title_tokens = {
        token for token in re.findall(r'[a-z0-9]{3,}', (title or '').lower())
        if token not in RSS_URL_STOPWORDS
    }
    path_tokens = {
        token for token in re.findall(r'[a-z0-9]{3,}', urlparse(url).path.lower())
        if token not in RSS_URL_STOPWORDS
    }
    return len(title_tokens & path_tokens)

def _select_rss_entry_link(entry, title):
    candidates = _rss_candidate_links(entry)
    if not candidates:
        return '', {}
    preferred = candidates[0]
    scored = [(url, _rss_url_match_score(title, url)) for url in candidates]
    best_url, best_score = max(scored, key=lambda item: item[1])
    preferred_score = dict(scored)[preferred]
    if best_url != preferred and best_score >= 2 and best_score > preferred_score:
        return best_url, {
            'source_url_original': preferred,
            'source_url_repaired': True,
            'source_url_repair_reason': 'rss_guid_title_match',
        }
    return preferred, {}

def _parse_rss_text(cfg, text):
    """解析 RSS/Atom 文本，返回事件列表。feedparser 自动处理编码/日期标准化。"""
    if not text: return []
    text = text.strip()
    if not any(text.startswith(x) or x in text[:300] for x in ['<?xml', '<rss', '<feed']):
        return []

    try:
        parsed = feedparser.parse(text)
    except Exception:
        return []

    results = []
    qualified_results = []
    candidate_results = []
    max_items = cfg.get('max', 8)
    max_scan = cfg.get('max_scan', max_items)
    scanned = 0
    scope_managed = _is_vertical_source(cfg) or cfg.get('source_role') == 'deep_trend'
    scope_stats = {
        'feed_entries': len(parsed.entries),
        'recent_items': 0,
        'qualified': 0,
        'candidate': 0,
        'filtered': 0,
        'filter_reasons': {},
    }

    for entry in parsed.entries:
        if not scope_managed and len(results) >= max_items: break
        if scanned >= max_scan: break
        scanned += 1

        # 标题
        title = (entry.get('title') or '').strip()
        if len(title) < 15 or is_blacklisted(title, official=_is_official_cfg(cfg) or bool(cfg.get('is_company'))):
            continue

        # 链接：优先主链接；若 guid/id 与标题明显更匹配则自动修复。
        link, link_repair = _select_rss_entry_link(entry, title)
        if not link:
            continue

        # 日期：feedparser 标准化时间，URL 日期兜底
        date_meta = _rss_date_metadata(entry, link)
        article_date = date_meta['published_at'] or None
        if article_date and not _recent_article_date(article_date, days=2):
            continue
        scope_stats['recent_items'] += 1

        # 图片：从 RSS media:content 或 media:thumbnail 提取
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

        types = detect_event_types(title)
        summary_html = entry.get('summary') or entry.get('description') or ''
        source_excerpt = BeautifulSoup(summary_html, 'html.parser').get_text(' ', strip=True)[:600]
        item = _with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', 'Google News')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'image_url': image_url,
            'source_excerpt': source_excerpt,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **link_repair,
            **date_meta,
        }, cfg)
        apply_scope_contract(item)
        if scope_managed:
            status = item.get('scope_status')
            if status == 'filtered':
                scope_stats['filtered'] += 1
                reason = item.get('scope_reason') or 'scope_filtered'
                reasons = scope_stats['filter_reasons']
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            if status == 'candidate':
                candidate_results.append(item)
                scope_stats['candidate'] += 1
                reason = item.get('scope_reason') or 'scope_candidate'
                reasons = scope_stats['filter_reasons']
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            if types[0] == 'other':
                item['event_types'] = ['strategy']
            qualified_results.append(item)
            scope_stats['qualified'] += 1
            continue
        if cfg.get('signal_only') and types[0] == 'other':
            continue
        results.append(item)
    if scope_managed:
        results = qualified_results[:max_items] + candidate_results[:max_items]
    cfg['_scope_stats'] = scope_stats
    return results

def _qualified_signal_count(items):
    return sum(
        1 for item in items
        if item.get('scope_status') == 'qualified'
        and (item.get('event_types') or ['other'])[0] != 'other'
    )

def fetch_rss(cfg):
    """顺序抓取（兼容旧接口，保留给 fetch_html 等调用方使用）"""
    text = fetch_url(cfg['url'])
    return _parse_rss_text(cfg, text)
