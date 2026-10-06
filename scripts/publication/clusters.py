"""信号簇视图：周期事件对象、周度焦点窗口聚合。"""

try:
    from event_value import classify_bd_priority, event_score, event_type, is_google_news_event
    from internet_relevance import is_mainline_internet_event
    from period_themes import build_weekly_themes
    from view_selectors import is_period_high_value_event
    from publication.entity import load_entity_pool
    from publication.opportunity import _short_event_text
except ImportError:
    from scripts.event_value import classify_bd_priority, event_score, event_type, is_google_news_event
    from scripts.internet_relevance import is_mainline_internet_event
    from scripts.period_themes import build_weekly_themes
    from scripts.view_selectors import is_period_high_value_event
    from scripts.publication.entity import load_entity_pool
    from scripts.publication.opportunity import _short_event_text

def _cluster_objects(cluster):
    companies = cluster.get('companies') or []
    if companies:
        return '、'.join(companies[:4])
    topic = cluster.get('topic') or ''
    if topic:
        return topic
    return cluster.get('region') or '区域对象'

def _period_event_object(event):
    if event.get('company_name'):
        return event['company_name']
    companies = event.get('companies') or []
    if companies:
        return companies[0]
    return _short_event_text(event, 18)

def _weekly_signal_key(event):
    ev_type = event_type(event)
    if ev_type == 'funding':
        return 'funding', '资金进入窗口'
    if ev_type == 'ma':
        return 'ma', '整合窗口'
    if ev_type == 'earnings':
        return 'earnings', '经营拐点窗口'
    triggers = event.get('bd_triggers') or []
    if any(trigger in triggers for trigger in ['扩张窗口', '生态窗口']):
        return 'expansion', '扩张与生态窗口'
    if '合规窗口' in triggers:
        return 'compliance', '合规窗口'
    direction = (event.get('opportunity_direction') or '').split('/')[0].strip()
    if '支付' in direction:
        return 'payment', '支付升级窗口'
    if '云' in direction or 'AI' in direction or '基础设施' in direction:
        return 'ai_infra', 'AI与基础设施窗口'
    return 'strategy', '战略观察窗口'

def _weekly_window_rank(event):
    priority_rank = {'高': 3, '中': 2, '观察': 1}
    return (
        priority_rank.get(classify_bd_priority(event), 0),
        event_score(event),
        event.get('date', ''),
    )

def _build_broad_weekly_focus_windows(period_events, limit=3):
    grouped = {}
    for event in period_events:
        ev_type = event_type(event)
        if ev_type == 'other':
            continue
        if is_google_news_event(event):
            continue
        if not is_mainline_internet_event(event):
            continue
        if not (is_period_high_value_event(event) or classify_bd_priority(event) in {'高', '中'} or ev_type in {'funding', 'ma', 'earnings'}):
            continue
        key, label = _weekly_signal_key(event)
        region = event.get('region') or '多地区'
        bucket = grouped.setdefault((region, key), {
            'region': region,
            'direction': label,
            'events': [],
        })
        bucket['events'].append(event)

    windows = []
    for bucket in grouped.values():
        events = sorted(bucket['events'], key=_weekly_window_rank, reverse=True)
        if len(events) < 2:
            continue
        objects = []
        sources = set()
        dates = set()
        for event in events:
            obj = _period_event_object(event)
            if obj and obj not in objects:
                objects.append(obj)
            if event.get('source_tier') or event.get('source'):
                sources.add(event.get('source_tier') or event.get('source'))
            if event.get('date'):
                dates.add((event.get('date') or '')[:10])
        if len(objects) < 2 and len(sources) < 2 and len(dates) < 2:
            continue
        evidence = [
            {
                'title': _short_event_text(event),
                'url': event.get('url') or '#',
                'date': (event.get('date') or '')[:10],
                'source': event.get('display_source') or event.get('source') or '公开来源',
                'type': event.get('insight_label') or event_type(event),
            }
            for event in events[:3]
        ]
        high_count = sum(1 for event in events if classify_bd_priority(event) == '高' or is_period_high_value_event(event))
        confidence = '高' if len(events) >= 4 and high_count >= 2 else '中' if high_count else '观察'
        windows.append({
            'title': f"{bucket['region']}{bucket['direction']}",
            'region': bucket['region'],
            'objects': '、'.join(objects[:4]) if objects else bucket['region'],
            'direction': bucket['direction'],
            'confidence': confidence,
            'evidence_count': len(events),
            'action': '进入下周观察名单，优先复核对象、预算和合作入口',
            'why': events[0].get('reason') or events[0].get('summary_short') or '',
            'evidence': evidence,
            'score': len(events) * 3 + high_count * 5 + len(objects),
        })
    windows.sort(key=lambda item: (item['confidence'] == '高', item['score'], item['evidence_count']), reverse=True)
    return windows[:limit]

def _entity_region_map():
    pool = load_entity_pool()
    return {
        entity.get('name'): entity.get('region') or '全球'
        for entity in pool.get('entities') or []
        if entity.get('name')
    }

def _build_weekly_focus_windows(period_events, end_date, limit=6):
    return build_weekly_themes(period_events, _entity_region_map(), limit=limit)
