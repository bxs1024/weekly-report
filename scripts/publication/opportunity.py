"""机会视图：BD 优先级、区域地图、动作建议、客户分层、主题聚合。"""

import re

try:
    from event_value import classify_bd_priority, event_score, event_type, is_google_news_event
    from view_selectors import is_period_high_value_event
    from publication.display import clean_display_title
except ImportError:
    from scripts.event_value import classify_bd_priority, event_score, event_type, is_google_news_event
    from scripts.view_selectors import is_period_high_value_event
    from scripts.publication.display import clean_display_title

def _bd_priority_rank(event):
    priority_rank = {'高': 3, '中': 2, '观察': 1}
    tier_rank = {
        'L1 官方/IR源': 5,
        'L2 垂直交易源': 4,
        'L3 区域生态源': 3,
        'L4 垂直赛道精品源': 3,
        'L4 深度趋势源': 2,
        'L5 Google News 补漏源': 1,
    }
    ev_type = (event.get('event_types') or ['other'])[0]
    type_rank = {'funding': 4, 'ma': 4, 'earnings': 3, 'strategy': 3, 'other': 1}.get(ev_type, 1)
    return (
        priority_rank.get(classify_bd_priority(event), 0),
        event_score(event),
        tier_rank.get(event.get('source_tier'), 0),
        type_rank,
        event.get('date', ''),
    )

def _short_event_text(event, max_len=54):
    text = clean_display_title(
        event.get('display_title') or event.get('summary_short') or event.get('reason') or event.get('title') or ''
    )
    return text if len(text) <= max_len else text[:max_len].rstrip() + '...'

def _build_top_opportunities(period_events, limit=5):
    seen = set()
    result = []
    for event in sorted(period_events, key=_bd_priority_rank, reverse=True):
        key = (event.get('company_name') or event.get('title') or '').lower()
        if key in seen:
            continue
        seen.add(key)
        result.append({
            'title': _short_event_text(event),
            'company': event.get('company_name') or (event.get('companies') or [''])[0] or '区域事件',
            'region': event.get('region') or '未知',
            'priority': event.get('bd_priority') or '观察',
            'trigger': ' / '.join(event.get('bd_triggers') or ['持续观察']),
            'direction': event.get('opportunity_direction') or '持续观察',
            'window': event.get('follow_up_window') or '持续观察',
            'source_tier': event.get('source_tier') or 'L3 区域生态源',
            'url': event.get('url') or '#',
        })
        if len(result) >= limit:
            break
    return result

def _build_regional_map(period_events, limit=6):
    grouped = {}
    for event in period_events:
        region = event.get('region') or '未知'
        item = grouped.setdefault(region, {
            'region': region,
            'count': 0,
            'high': 0,
            'companies': set(),
            'directions': {},
            'score_sum': 0,
        })
        item['count'] += 1
        item['score_sum'] += event_score(event)
        if is_period_high_value_event(event):
            item['high'] += 1
        if event.get('company_name'):
            item['companies'].add(event['company_name'])
        for direction in re.split(r'\s*/\s*', event.get('opportunity_direction') or ''):
            if direction:
                item['directions'][direction] = item['directions'].get(direction, 0) + 1

    result = []
    for item in grouped.values():
        top_direction = max(item['directions'].items(), key=lambda x: x[1])[0] if item['directions'] else '持续观察'
        avg_score = item['score_sum'] / item['count'] if item['count'] else 0
        result.append({
            'region': item['region'],
            'count': item['count'],
            'high': item['high'],
            'companies': len(item['companies']),
            'direction': top_direction,
            'avg_score': round(avg_score, 1),
        })
    result.sort(key=lambda x: (x['high'], x['count'], x['avg_score']), reverse=True)
    return result[:limit]

def _build_actions(period_events, limit=5):
    windows = ['7天内', '30天内', '持续观察']
    result = []
    for window in windows:
        candidates = [e for e in period_events if e.get('follow_up_window') == window]
        if not candidates:
            continue
        candidates.sort(key=_bd_priority_rank, reverse=True)
        top = candidates[0]
        result.append({
            'window': window,
            'action': f"围绕{top.get('region') or '重点区域'}的{top.get('opportunity_direction') or '合作机会'}建立跟进清单",
            'event': _short_event_text(top, 42),
            'count': len(candidates),
        })
        if len(result) >= limit:
            break
    return result

def _build_customer_tiers(period_events, limit=6):
    grouped = {}
    for event in period_events:
        company = event.get('company_name') or ((event.get('companies') or [''])[0] if event.get('companies') else '')
        if not company:
            continue
        item = grouped.setdefault(company, {
            'company': company,
            'region': event.get('region') or '未知',
            'count': 0,
            'high': 0,
            'score': 0,
            'direction': event.get('opportunity_direction') or '持续观察',
        })
        item['count'] += 1
        item['score'] = max(item['score'], event_score(event))
        if is_period_high_value_event(event):
            item['high'] += 1
        if event.get('opportunity_direction'):
            item['direction'] = event['opportunity_direction']

    result = []
    for item in grouped.values():
        if item['high'] > 0 or item['score'] >= 70:
            tier = 'A类：优先触达'
        elif item['count'] >= 2 or item['score'] >= 50:
            tier = 'B类：持续经营'
        else:
            tier = 'C类：观察入库'
        item['tier'] = tier
        result.append(item)
    result.sort(key=lambda x: (x['tier'], x['high'], x['score'], x['count']), reverse=True)
    return result[:limit]

def _build_themes(period_events, limit=6):
    counts = {}
    for event in period_events:
        for direction in re.split(r'\s*/\s*', event.get('opportunity_direction') or ''):
            if direction and direction != '持续观察':
                counts[direction] = counts.get(direction, 0) + 1
    return [
        {'name': name, 'count': count}
        for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    ]
