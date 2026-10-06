"""周期报告：周/月归档、AIHOT 档案接入、周期报告构建。"""

import json
import os
from datetime import datetime, timedelta

try:
    from period_themes import build_company_changes, build_industry_changes, build_monthly_trends
    from repo_paths import data_path
    from view_selectors import select_period_high_value_events
    from editorial.editorial import build_monthly_editorial, build_weekly_editorial
    from publication.clusters import _build_weekly_focus_windows, _entity_region_map
    from publication.display import _front_trend_topic
    from publication.opportunity import (
        _build_actions, _build_customer_tiers, _build_regional_map, _build_top_opportunities,
    )
except ImportError:
    from scripts.period_themes import build_company_changes, build_industry_changes, build_monthly_trends
    from scripts.repo_paths import data_path
    from scripts.view_selectors import select_period_high_value_events
    from scripts.editorial.editorial import build_monthly_editorial, build_weekly_editorial
    from scripts.publication.clusters import _build_weekly_focus_windows, _entity_region_map
    from scripts.publication.display import _front_trend_topic
    from scripts.publication.opportunity import (
        _build_actions, _build_customer_tiers, _build_regional_map, _build_top_opportunities,
    )

def _load_aihot_archive(start_date, end_date, weekly=False):
    """读取周期内 AIHOT 归档热点（data/aihot_hot/YYYY-MM-DD.json），按标题去重。

    AIHOT 是实时外部数据，归属按自然周期（周报=自然周、月报=自然月）判定，
    不受站内事件截止日（end_date）限制——否则当天的实时热点会被挡在周期外。
    """
    import calendar
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    if weekly:
        natural_end_dt = start_dt + timedelta(days=(6 - start_dt.weekday()))
    else:
        last_day = calendar.monthrange(start_dt.year, start_dt.month)[1]
        natural_end_dt = datetime(start_dt.year, start_dt.month, last_day)
    end = max(end_date, natural_end_dt.strftime('%Y-%m-%d'))

    archive_dir = data_path('aihot_hot')
    if not os.path.isdir(archive_dir):
        return []
    items = []
    seen_titles = set()
    for filename in sorted(os.listdir(archive_dir)):
        date_key = filename[:-5]
        if not (start_date <= date_key <= end):
            continue
        try:
            with open(os.path.join(archive_dir, filename), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for item in data.get('items') or []:
            title = (item.get('title') or item.get('list_title') or '').strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            url = ''
            if item.get('original_links'):
                url = item['original_links'][0].get('url') or ''
            if not url:
                url = item.get('story_url') or ''
            items.append({
                'rank': len(items) + 1,
                'title': title,
                'heat': item.get('heat'),
                'url': url,
                'date': date_key,
            })
    return items[:10]

def _aihot_items_to_list(data, limit=10, date_key=None):
    """把 AIHOT 热点数据（当前快照或单日归档的 dict）转为统一列表结构。

    统一结构 [{rank,title,heat,url,date}]：今日 tab 与周报/月报共用同一份数据，
    展示层回退逻辑也以这份结构为锚。数据为空或结构异常返回 []。
    """
    if not data or not isinstance(data, dict):
        return []
    items = data.get('items') or []
    if not items:
        return []
    if date_key is None:
        date_key = (data.get('fetched_date') or '')[:10]
    out = []
    for idx, it in enumerate(items[:limit], 1):
        url = ''
        if it.get('original_links'):
            url = it['original_links'][0].get('url') or ''
        if not url:
            url = it.get('story_url') or ''
        out.append({
            'rank': idx,
            'title': it.get('title') or it.get('list_title') or '',
            'heat': it.get('heat'),
            'url': url,
            'date': date_key,
        })
    return out

def _latest_aihot_items(limit=10):
    """取最近一期有数据的 AIHOT 热点（按归档日期倒序），统一列表结构。

    当前/当期快照为空时用它兜底展示：宁可用最近一期有效数据，也不让区块消失。
    """
    archive_dir = data_path('aihot_hot')
    if not os.path.isdir(archive_dir):
        return []
    for filename in sorted(os.listdir(archive_dir), reverse=True):
        if not filename.endswith('.json'):
            continue
        date_key = filename[:-5]
        try:
            with open(os.path.join(archive_dir, filename), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        items = _aihot_items_to_list(data, limit, date_key=date_key)
        if items:
            return items
    return []

def build_period_report(events, start_date, end_date, label, period_id=None, status='closed',
                        focus_windows_enabled=False, require_editorial=False):
    """按 BD 机会视角聚合周报/月报；生产档案可要求 AI 编辑成功后才输出。"""
    period_events = [
        e for e in events
        if start_date <= (e.get('date') or '')[:10] <= end_date
    ]
    regions = sorted({e.get('region') for e in period_events if e.get('region')})
    companies = sorted({
        e.get('company_name') for e in period_events
        if e.get('is_company') and e.get('company_name')
    })

    trend_counts = {}
    trend_regions = {}
    for e in period_events:
        topic = _front_trend_topic(e)
        trend_counts[topic] = trend_counts.get(topic, 0) + 1
        region = e.get('region') or '未知'
        trend_regions.setdefault(topic, {})
        trend_regions[topic][region] = trend_regions[topic].get(region, 0) + 1

    trends = []
    for topic, count in sorted(trend_counts.items(), key=lambda x: x[1], reverse=True):
        region_map = trend_regions.get(topic, {})
        top_region = max(region_map.items(), key=lambda x: x[1])[0] if region_map else '多地区'
        trends.append({'topic': topic, 'count': count, 'region': top_region})

    top_opportunities = _build_top_opportunities(period_events, 5)
    regional_map = _build_regional_map(period_events, 6)
    actions = _build_actions(period_events, 5)
    customer_tiers = _build_customer_tiers(period_events, 6)
    focus_windows = _build_weekly_focus_windows(period_events, end_date, 6) if focus_windows_enabled else []
    monthly_trends = (
        build_monthly_trends(events, start_date, end_date, _entity_region_map(), limit=6)
        if not focus_windows_enabled else []
    )
    themes = focus_windows if focus_windows_enabled else monthly_trends
    company_changes = (
        build_company_changes(events, start_date, end_date, _entity_region_map(), limit=5)
        if not focus_windows_enabled else []
    )
    industry_changes = (
        build_industry_changes(events, start_date, end_date, _entity_region_map(), limit=5)
        if not focus_windows_enabled else []
    )
    high_count = len(select_period_high_value_events(period_events))

    # AI 编辑层：生产档案要求成功生成；单元测试可显式允许模板降级
    narrative_result = None
    editorial_title = ''
    editorial_required = bool(themes) and (focus_windows_enabled or status != 'preview')
    if focus_windows_enabled and themes:
        narrative_result = build_weekly_editorial(
            themes, period_id, cache_key=f"weekly:{period_id or label}")
    elif (not focus_windows_enabled) and monthly_trends and status != 'preview':
        narrative_result = build_monthly_editorial(
            monthly_trends, period_id, cache_key=f"monthly:{period_id or start_date[:7]}")
    if require_editorial and editorial_required and not narrative_result:
        period_type = '周报' if focus_windows_enabled else '月报'
        raise RuntimeError(f'{period_type} {period_id or label} 的 AI 编辑层生成失败，已终止页面生成，拒绝发布降级版')
    if narrative_result:
        editorial_title = narrative_result.get('editorial_title') or ''

    if period_events:
        title = f"{label}{'关注主题周报' if focus_windows_enabled else '趋势与结构月报'}"
        leading_region = regional_map[0]['region'] if regional_map else '多地区'
        if focus_windows_enabled:
            if focus_windows:
                if narrative_result:
                    summary = narrative_result['mainline']
                else:
                    leading_theme = focus_windows[0]['direction']
                    leading_region = focus_windows[0]['region']
                    summary = (
                        f"本周期从 {len(period_events)} 条合格事实中形成 {len(focus_windows)} 个主题。"
                        f"本周主线是{leading_theme}，再回到独立事实确认。"
                    )
            else:
                summary = (
                    f"本周期收录 {len(period_events)} 条合格事实，但尚未形成满足独立证据门槛的主题。"
                    f"本周只保留事件导航，不硬凑结论。"
                )
        else:
            if status == 'preview':
                summary = (
                    f"当前月尚在观察期，已收录 {len(period_events)} 条合格事件。"
                    f"趋势结论待积累完整观察周后输出，先保留事实。"
                )
            elif monthly_trends:
                if narrative_result:
                    summary = narrative_result['mainline']
                else:
                    leading_theme = monthly_trends[0]['title']
                    summary = (
                        f"本周期从 {len(period_events)} 条合格事件中形成 {len(monthly_trends)} 个跨周趋势。"
                        f"本月主线是{leading_theme}，每个判断均可回到独立证据。"
                    )
            else:
                summary = (
                    f"本周期共收录 {len(period_events)} 条事件，但尚未形成跨周、可比较的结构趋势。"
                    f"先保留事实，不用默认标签填充月报。"
                )
    else:
        title = f"{label}{'关注主题周报' if focus_windows_enabled else '趋势与结构月报'}"
        summary = "当前周期事件数量较少，先保留为观察入口。"
    date_label = start_date if start_date == end_date else f"{start_date} 至 {end_date}"
    status_label = {'preview': '观察中', 'mature': '更新中', 'open': '更新中', 'closed': '已封存'}.get(status, '已封存')

    if narrative_result:
        theme_details = narrative_result.get('themes') or {}
        theme_titles = narrative_result.get('theme_titles') or {}
        for t in themes:
            detail = theme_details.get(t.get('key'))
            if detail:
                if isinstance(detail, dict):
                    t['narrative'] = detail.get('narrative') or t.get('narrative') or ''
                    if detail.get('drivers'):
                        t['drivers'] = detail['drivers']
                    if detail.get('uncertainty'):
                        t['uncertainty'] = detail['uncertainty']
                    if detail.get('next_validation'):
                        t['next_validation'] = detail['next_validation']
                else:
                    t['narrative'] = detail
            specific_title = theme_titles.get(t.get('key'))
            if specific_title:
                t['title'] = specific_title
                t['direction'] = specific_title

    return {
        'id': period_id or f"{start_date}_{end_date}",
        'start': start_date,
        'end': end_date,
        'date_label': date_label,
        'month': start_date[:7],
        'label': label,
        'status': status,
        'status_label': status_label,
        'title': title,
        'editorial_title': editorial_title,
        'summary': summary,
        'total': len(period_events),
        'companies': len(companies),
        'regions': len(regions),
        'trends': trends or [{'topic': '暂无趋势', 'count': 0, 'region': '无'}],
        'top_opportunities': top_opportunities,
        'focus_windows': focus_windows,
        'regional_map': regional_map,
        'actions': actions,
        'customer_tiers': customer_tiers,
        'themes': themes,
        'period_themes': themes,
        'company_changes': company_changes,
        'industry_changes': industry_changes,
        'high_priority': high_count,
        'aihot_hot': _load_aihot_archive(start_date, end_date, weekly=focus_windows_enabled)
                    or _latest_aihot_items(),
    }

def build_weekly_archives(events, reference_date, require_editorial=True):
    """按自然周生成独立周报档案，已结束周固定封存，当前周更新至最新日期。"""
    grouped = {}
    reference_dt = datetime.strptime(reference_date, '%Y-%m-%d')
    for event in events:
        date_key = (event.get('date') or '')[:10]
        if not date_key:
            continue
        try:
            dt = datetime.strptime(date_key, '%Y-%m-%d')
        except ValueError:
            continue
        week_start_dt = dt - timedelta(days=dt.weekday())
        week_end_dt = week_start_dt + timedelta(days=6)
        year, week, _ = dt.isocalendar()
        key = f"{year}-W{week:02d}"
        item = grouped.setdefault(key, {
            'id': key,
            'label': f"{year}年第{week:02d}周",
            'start': week_start_dt.strftime('%Y-%m-%d'),
            'natural_end': week_end_dt.strftime('%Y-%m-%d'),
            'end': week_end_dt.strftime('%Y-%m-%d'),
        })
        if week_start_dt <= reference_dt <= week_end_dt:
            item['end'] = reference_date
    archives = []
    for item in grouped.values():
        status = 'open' if item['start'] <= reference_date <= item['natural_end'] else 'closed'
        label = item['label'] if status == 'closed' else f"{item['label']}（更新中）"
        archives.append(build_period_report(
            events, item['start'], item['end'], label, item['id'], status,
            focus_windows_enabled=True, require_editorial=require_editorial,
        ))
    archives.sort(key=lambda x: x['start'], reverse=True)
    return archives

def build_monthly_archives(events, reference_date, require_editorial=True):
    """按自然月生成独立月报档案，已结束月份固定封存，当前月更新至最新日期。"""
    months = sorted({(e.get('date') or '')[:7] for e in events if (e.get('date') or '')[:7]}, reverse=True)
    archives = []
    main_month = reference_date[:7]
    for month in months:
        start_date = f"{month}-01"
        if month == main_month:
            end_date = reference_date
            first_day = datetime.strptime(f"{month}-01", '%Y-%m-%d')
            reference_dt = datetime.strptime(reference_date, '%Y-%m-%d')
            if (reference_dt - first_day).days >= 14:
                status = 'mature'
                label = f"{month} 月度趋势更新"
            else:
                status = 'preview'
                label = f"{month} 月度观察"
        else:
            y, m = [int(x) for x in month.split('-')]
            next_month = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
            end_date = (next_month - timedelta(days=1)).strftime('%Y-%m-%d')
            status = 'closed'
            label = f"{month} 月报"
        archives.append(build_period_report(
            events, start_date, end_date, label, month, status,
            require_editorial=require_editorial,
        ))
    return archives
