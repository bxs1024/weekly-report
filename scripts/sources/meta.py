"""信源元数据：来源标注、注册源配置转换、漏斗计数。

把「一条原始条目/一个信源配置」补全成带来源层级、角色、公司主体的结构，
并处理 source_registry 的动态源。

依赖方向：本模块 → content.classify（用别名/区域/判型），反向不依赖。
"""

import json
import sys

try:
    from constants import HTML_SOURCES, RSS_SOURCES
    from content.classify import (
        _get_company_aliases, _title_mentions_aliases,
        infer_event_region, infer_signal_taxonomy,
    )
    from repo_paths import data_path
except ImportError:
    from scripts.constants import HTML_SOURCES, RSS_SOURCES
    from scripts.content.classify import (
        _get_company_aliases, _title_mentions_aliases,
        infer_event_region, infer_signal_taxonomy,
    )
    from scripts.repo_paths import data_path


def _source_meta(cfg):
    """保留信源分层，供后续日报/周报/月报按业务口径组织。"""
    return {
        'source_tier': cfg.get('source_tier', 'L3 区域生态源'),
        'source_role': cfg.get('source_role', 'regional_ecosystem'),
        'vertical': cfg.get('vertical', ''),
        'source_type': cfg.get('source_type', ''),
        'access_method': cfg.get('access_method', ''),
        'signal_types': cfg.get('signal_types', []),
        'source_id': cfg.get('id', cfg.get('name', '')),
        'credibility_score': cfg.get('credibility_score', 0),
        'noise_level': cfg.get('noise_level', ''),
        'scope_industries': cfg.get('scope_industries', []),
        'scope_regions': cfg.get('scope_regions', []),
        'publisher_type': cfg.get('publisher_type', ''),
        'authority_domains': cfg.get('authority_domains', []),
        'claim_roles': cfg.get('claim_roles', []),
        'access_level': cfg.get('access_level', ''),
        'report_access_level': cfg.get('report_access_level', cfg.get('access_level', '')),
        'methodology_visibility': cfg.get('methodology_visibility', ''),
        'report_methodology_visible': cfg.get('report_methodology_visible', False),
    }

def _with_source_meta(item, cfg):
    item.update(_source_meta(cfg))
    company = item.get('company_name') or ''
    if company and item.get('is_company') and not _title_mentions_aliases(item.get('title', ''), _get_company_aliases(company)):
        item['title'] = f"{company}: {item.get('title', '')}"
    item['region'] = infer_event_region(item.get('title', ''), item.get('region', cfg.get('region', '未知')))
    item['signal_taxonomy'] = infer_signal_taxonomy(item)
    return item

REGISTRY_TIER_MAP = {
    'L1': 'L1 官方/IR源',
    'L2': 'L2 垂直交易源',
    'L3': 'L3 区域生态源',
    'L4': 'L4 垂直赛道精品源',
    'L5': 'L5 Google News 补漏源',
}

REGISTRY_ROLE_MAP = {
    'newsroom': 'official_ir',
    'ir': 'official_ir',
    'changelog': 'developer_change',
    'developer_changelog': 'developer_change',
    'engineering_blog': 'industry_vertical',
    'research_report': 'industry_vertical',
    'industry_media': 'industry_vertical',
    'media': 'regional_ecosystem',
}

def _registry_source_to_cfg(src):
    tier = REGISTRY_TIER_MAP.get(src.get('tier'), src.get('source_tier') or src.get('tier') or 'L3 区域生态源')
    source_type = src.get('source_type') or 'media'
    role = src.get('source_role') or REGISTRY_ROLE_MAP.get(source_type, 'regional_ecosystem')
    cfg = {
        'id': src.get('id') or src.get('name'),
        'name': src.get('name'),
        'url': src.get('url'),
        'source': src.get('source') or src.get('name'),
        'region': src.get('region', '全球'),
        'priority': src.get('priority', 2),
        'source_tier': tier,
        'source_role': role,
        'source_type': source_type,
        'access_method': src.get('access_method') or src.get('method') or 'rss',
        'signal_types': src.get('signal_types') or src.get('bd_signal_types') or [],
        'vertical': src.get('track', ''),
        'max_scan': src.get('max_scan', 12),
        'max': src.get('max', 4),
        'signal_only': src.get('signal_only', True),
        'credibility_score': src.get('credibility_score', 0),
        'noise_level': src.get('noise_level', ''),
        'scope_industries': src.get('scope_industries') or [],
        'scope_regions': src.get('scope_regions') or [],
    }
    for key in ('company_name', 'is_company', 'include_url_patterns', 'allowed_scope_layers'):
        if key in src:
            cfg[key] = src[key]
    for key in ('access_level', 'report_access_level', 'methodology_visibility', 'report_methodology_visible'):
        if key in src:
            cfg[key] = src[key]
    if (
        tier == 'L1 官方/IR源'
        and source_type in {'changelog', 'developer_changelog', 'newsroom', 'ir'}
        and not cfg.get('company_name')
    ):
        cfg['company_name'] = src.get('company_name') or _source_entity_name(src.get('source') or src.get('name'))
        cfg['is_company'] = True
    return cfg

def _source_entity_name(value):
    name = (value or '').strip()
    for suffix in (
        ' Developer Changelog',
        ' Changelog',
        ' Newsroom',
        ' IR News',
        ' IR',
        ' Press',
        ' News',
    ):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name

def _source_metric_key(item):
    return item.get('source_id') or item.get('source') or item.get('display_source') or item.get('source_detail') or '未知来源'

def _count_by_source(items):
    counts = {}
    for item in items:
        key = _source_metric_key(item)
        counts[key] = counts.get(key, 0) + 1
    return counts

def _source_funnel_stage(items, total_key):
    rows = {}
    for key, count in _count_by_source(items).items():
        rows[key] = {total_key: count}
    return rows

def _merge_source_funnel(target, stage_counts):
    for key, counts in stage_counts.items():
        row = target.setdefault(key, {})
        for metric, count in counts.items():
            row[metric] = row.get(metric, 0) + count

def load_registry_sources(path=None):
    """从 source_registry 读动态源。

    读不到时返回空列表（调用方据此只用内置 RSS_SOURCES/HTML_SOURCES），
    与历史行为一致；但会打一行警告——此前静默吞异常，配合相对路径 bug
    会让整个动态源清单悄悄失效且无人察觉。
    """
    path = path or data_path('source_registry.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            registry = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'⚠️ 读取 source_registry 失败，动态源退化为空: {path} ({type(exc).__name__})',
              file=sys.stderr)
        return [], []
    sources = registry.get('sources') or registry.get('active_sources') or []
    rss, html = [], []
    existing_names = {cfg.get('name') for cfg in RSS_SOURCES + HTML_SOURCES}
    existing_urls = {cfg.get('url') for cfg in RSS_SOURCES + HTML_SOURCES}
    for src in sources:
        if src.get('status') not in {'active', 'enabled'}:
            continue
        if not src.get('url') or src.get('name') in existing_names or src.get('url') in existing_urls:
            continue
        cfg = _registry_source_to_cfg(src)
        method = cfg.get('access_method')
        if method in {'rss', 'atom'}:
            rss.append(cfg)
        elif method in {'html', 'sitemap', 'pressroom', 'changelog'}:
            html.append(cfg)
    return rss, html


def _is_official_cfg(cfg):
    return cfg.get('source_tier') == 'L1 官方/IR源' or cfg.get('source_role') == 'official_ir'
