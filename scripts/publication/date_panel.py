"""日期面板：日度事件分组、日期导航、首页按日选取。"""

from datetime import datetime

try:
    from event_value import classify_bd_priority
    from narratives import build_narrative
    from signal_clusters import build_signal_clusters
    from view_selectors import select_homepage_events
    from publication.loaders import CHINESE_WEEKDAYS
    from publication.review import build_review_events
    from publication.summary import (
        build_trend_groups, build_weekly_summary, get_signal_events, keep_focus_date_clusters,
    )
except ImportError:
    from scripts.event_value import classify_bd_priority
    from scripts.narratives import build_narrative
    from scripts.signal_clusters import build_signal_clusters
    from scripts.view_selectors import select_homepage_events
    from scripts.publication.loaders import CHINESE_WEEKDAYS
    from scripts.publication.review import build_review_events
    from scripts.publication.summary import (
        build_trend_groups, build_weekly_summary, get_signal_events, keep_focus_date_clusters,
    )

DAILY_EVENT_GROUPS = [
    ('selected', '精选', '最先看，强信号、强相关、可直接进入判断'),
    ('important', '重点', '值得继续跟，有明确对象或方向'),
    ('watch', '观察', '保留事实，用于背景留档和后续跟踪'),
]

def _daily_event_group_key(event):
    frozen = event.get('view_priority')
    if frozen in {'selected', 'important', 'watch'}:
        return frozen
    priority = classify_bd_priority(event)
    if priority == '高':
        return 'selected'
    if priority == '中':
        return 'important'
    return 'watch'

def build_daily_event_groups(events):
    """Group qualified daily events without weakening the main-list gate."""
    grouped = {key: [] for key, _, _ in DAILY_EVENT_GROUPS}
    for event in events:
        grouped[_daily_event_group_key(event)].append(event)
    return [
        {
            'key': key,
            'label': label,
            'description': description,
            'events': grouped[key],
        }
        for key, label, description in DAILY_EVENT_GROUPS
        if grouped[key]
    ]

def build_daily_navigation_copy(groups):
    """Build plain daily copy for the event-navigation layer."""
    total = sum(len(group['events']) for group in groups)
    if total <= 0:
        return '今日事件导航', '当前没有通过本站边界和信源筛选的日报事件。'
    counts = '，'.join(f"{group['label']} {len(group['events'])} 条" for group in groups)
    return (
        f"今日事件导航：{total} 条合格事件",
        f"{counts}。信源筛选和产品边界仍是准入门槛，分层只负责帮你决定先看什么。",
    )

def build_date_panel(date_str, day_events, all_events, raw_day_events=None, cluster_events=None):
    """预计算某日期的今日面板数据（趋势分组 + 判断 + 统计），供 JS 翻页切换"""
    signals = get_signal_events(all_events)
    weekly = build_weekly_summary(day_events, signals, day_events, all_events, summary_date=date_str)
    trend_groups = build_trend_groups(day_events)
    repair_events = build_review_events(raw_day_events or day_events)
    signal_clusters = keep_focus_date_clusters(
        build_signal_clusters(cluster_events or all_events, date_str, limit=12)
    )
    narrative = build_narrative(signal_clusters, fallback_events=day_events)
    daily_event_groups = build_daily_event_groups(day_events)
    daily_headline, daily_judgment = build_daily_navigation_copy(daily_event_groups)

    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return {
        'trend_groups': trend_groups,
        'repair_events': repair_events,
        'judgment': daily_judgment,
        'top3': weekly.get('top3', []),
        'signal_clusters': strip_cluster_event_payloads(narrative.get('clusters', [])),
        'evidence_events': narrative.get('evidence_events', []),
        'daily_event_groups': daily_event_groups,
        'total_stories': len(day_events),
        'vol_label': f"VOL.{date_str}",
        'cn_date': f"{dt.year}年{dt.month}月{dt.day}日 星期{CHINESE_WEEKDAYS[dt.weekday()]}",
        'headline': daily_headline,
        'funding': weekly.get('funding', 0),
        'ma': weekly.get('ma', 0),
        'earnings': weekly.get('earnings', 0),
        'regions': weekly.get('regions', 0),
    }

def select_homepage_events_for_date(all_visible_events, date_str, fallback_events=None):
    return select_homepage_events(all_visible_events, date_str, fallback_events)

def strip_cluster_event_payloads(clusters):
    public_clusters = []
    for cluster in clusters or []:
        public_cluster = dict(cluster)
        public_cluster.pop('evidence_events', None)
        public_clusters.append(public_cluster)
    return public_clusters

def group_events_by_date(events):
    """将事件按日期分组，按时间倒序"""
    groups = {}
    for e in events:
        d = (e.get('date') or '')[:10]
        groups.setdefault(d, []).append(e)
    result = [{'date': k, 'events': v} for k, v in sorted(groups.items(), reverse=True)]
    return result
