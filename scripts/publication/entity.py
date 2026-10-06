"""实体维度：实体池加载、投资组合映射、实体事件时间线。"""

import json

try:
    from repo_paths import data_path
    from entity_signal_conversion_report import event_matches_entity
    from event_value import event_score, is_company_quality_signal
    from view_selectors import is_main_view_event
except ImportError:
    from scripts.repo_paths import data_path
    from scripts.entity_signal_conversion_report import event_matches_entity
    from scripts.event_value import event_score, is_company_quality_signal
    from scripts.view_selectors import is_main_view_event

REGION_ORDER = ['全球', '北美', '亚太', '欧洲', '中东', '拉美', '非洲', '中资']

def load_entity_pool(path=None):
    try:
        with open(path or data_path('entity_pool.json'), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {'entities': [], 'portfolio': {}}

def _portfolio_by_entity(pool):
    result = {}
    for tier, rows in (pool.get('portfolio') or {}).items():
        for row in rows or []:
            result[row.get('entity_id')] = {
                'portfolio_tier': tier,
                'decision_use': row.get('decision_use') or '',
            }
    return result

def build_entity_event_timelines(events_by_date, entities):
    """Map every qualified event to the object aliases it actually describes.

    公司卡门槛 = 主列表事件 或 公司质量信号。主列表刻意整体排除 Google News
    聚合源（防全局 feed 被重复聚合噪声淹没），但公司卡应显示自家公司的强事件，
    哪怕来自 Google News（如 Adyen 无专属 RSS，覆盖面主要靠聚合源）。
    """
    candidates = [
        event
        for rows in (events_by_date or {}).values()
        for event in rows or []
        if is_main_view_event(event) or is_company_quality_signal(event)
    ]
    timelines = {}
    for entity in entities or []:
        matched = []
        seen = set()
        entity_name = entity.get('name') or ''
        for event in candidates:
            if not event_matches_entity(event, entity):
                continue
            key = event.get('url') or f"{event.get('date', '')}|{event.get('title', '')}"
            if key in seen:
                continue
            seen.add(key)
            matched.append(event)
            names = event.setdefault('matched_entities', [])
            if entity_name and entity_name not in names:
                names.append(entity_name)
        timelines[entity.get('id') or entity_name] = sorted(
            matched,
            key=lambda row: ((row.get('date') or '')[:10], event_score(row)),
            reverse=True,
        )
    return timelines
