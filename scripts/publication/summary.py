"""汇总视图：事件装载、公司/信号分流、周度摘要与趋势分组。"""

import json
import os
from datetime import timedelta

try:
    from event_dates import is_display_date
    from event_contract import prepare_event_contract
    from event_value import event_score
    from repo_paths import data_path
    from view_selectors import select_company_events
    from content.util import _cn_now, _cn_today
    from publication.bd import ensure_business_fields, enrich
    from publication.score import _format_amount, _parse_amount
except ImportError:
    from scripts.event_dates import is_display_date
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import event_score
    from scripts.repo_paths import data_path
    from scripts.view_selectors import select_company_events
    from scripts.content.util import _cn_now, _cn_today
    from scripts.publication.bd import ensure_business_fields, enrich
    from scripts.publication.score import _format_amount, _parse_amount

def load_events():
    with open(data_path('events.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        grouped = {}
        for event in data:
            date = event.get('date', _cn_today())[:10]
            grouped.setdefault(date, []).append(enrich(prepare_event_contract(dict(event))))
        return grouped
    return {
        k: [enrich(prepare_event_contract(dict(e))) for e in v]
        for k, v in data.items()
        if is_display_date(k, now=_cn_now())
    }

def split_company_events(events):
    """
    将事件拆分为公司动态和通用热点
    - 公司动态只保留7天内，不过滤
    - 通用热点：排除 other 类型，保留可解释、可展示的信号事件
    """
    week_ago = (_cn_now() - timedelta(days=7)).strftime('%Y-%m-%d')
    for evs in events.values():
        for e in evs:
            if not e.get('is_company'):
                ensure_business_fields(e)
    return select_company_events(events, week_ago)

def get_signal_events(events):
    """
    获取信号事件：
    1. 只取最近7天内的信号事件
    2. 排除中资出海
    3. 排除other类型
    4. 排除低评分（<5）事件
    5. 按日期倒序排序
    """
    seen = set()
    result = []

    week_ago = (_cn_now() - timedelta(days=7)).strftime('%Y-%m-%d')

    for date in sorted(events.keys(), reverse=True):
        # 只处理最近7天内的日期
        if date < week_ago:
            continue

        for event in events[date]:
            if event['url'] in seen:
                continue
            seen.add(event['url'])

            # 排除中资出海
            if event.get('is_chinese_capital'):
                continue

            # 只取信号事件（排除other类型）
            ev_type = event.get('event_types', ['other'])[0]
            if ev_type == 'other':
                continue

            # 排除低评分事件（规则层注意力分<50视为低质量）
            score = event_score(event)
            if score < 50:
                continue

            result.append(event)

    return result  # 已经在日期倒序遍历，返回即有序

def build_weekly_summary(all_feed, signals, latest_date_events, all_events, summary_date=None):
    """生成周报摘要：排除中资出海，只展示真正的"非中美"动态"""
    # 排除中资出海（中资有独立标签页）
    non_chinese = [e for e in all_feed if not e.get('is_chinese_capital')]
    # ── 数字统计 ───────────────────────────────────────────
    funding = sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'funding')
    ma      = sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'ma')
    earnings= sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'earnings')
    strategy= sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'strategy')
    total   = len(non_chinese)

    # ── type_counts：动态生成筛选按钮用 ───────────────────
    type_counts = {
        '融资': funding, '并购': ma, '财报': earnings, '战略': strategy,
    }

    # 区域分布
    region_counts = {}
    for e in non_chinese:
        r = e.get('region', '未知')
        if r != '未知':
            region_counts[r] = region_counts.get(r, 0) + 1
    region_counts = dict(sorted(region_counts.items(), key=lambda x: x[1], reverse=True))
    hot_region = max(region_counts, key=region_counts.get) if region_counts else ''

    # ── 金额计算（用于 headline）───────────────────────
    # 找最大融资事件
    funding_events = [e for e in non_chinese if e.get('event_types', [''])[0] == 'funding']
    top_funding = max(funding_events, key=lambda x: event_score(x), default=None)
    max_ma = next((e for e in non_chinese if e.get('event_types', [''])[0] == 'ma'), None)

    # ── Headline ────────────────────────────────────────
    # 用趋势描述，不用单一事件（避免"说亚太最强但Top3全是欧洲"的尴尬）
    parts_hl = []
    if funding > 0:
        parts_hl.append(f"融资{int(funding)}起")
    if ma > 0:
        parts_hl.append(f"并购{int(ma)}起")
    if earnings > 0:
        parts_hl.append(f"财报{int(earnings)}起")
    if hot_region and region_counts.get(hot_region):
        parts_hl.append(f"{hot_region}{region_counts[hot_region]}起")
    headline = "、".join(parts_hl) if parts_hl else f"共{int(total)}条动态"
    if len(region_counts) > 1:
        headline += f"覆盖{len(region_counts)}地区"

    # ── Summary ─────────────────────────────────────────
    parts = []
    if hot_region and region_counts.get(hot_region):
        parts.append(f"{hot_region}事件最多（{region_counts[hot_region]}起），占今日大头。")
    if funding >= 3:
        tf = top_funding
        top_co = tf.get('companies', [''])[0] if tf and tf.get('companies') else ''
        top_amt = _format_amount(_parse_amount(tf.get('title', ''))) if tf else ''
        if top_co and top_amt:
            parts.append(f"融资仍是主旋律，共{funding}起，最大单笔{top_co} {top_amt}。")
        elif top_co:
            parts.append(f"融资仍是主旋律，共{funding}起，最大单笔来自{top_co}。")
        else:
            parts.append(f"融资仍是主旋律，共{funding}起。")
    elif funding >= 1:
        parts.append(f"有{funding}起融资落地。")
    if ma >= 1:
        parts.append(f"另有{ma}起并购，显示{hot_region or '该地区'}行业整合加速。")
    if earnings >= 1:
        parts.append(f"本周财报季有{earnings}起值得关注。")
    if strategy >= 1:
        parts.append(f"另有{strategy}起战略动态值得关注。")
    if not parts:
        parts.append(f"共{total}条动态，覆盖{', '.join(region_counts.keys()) if region_counts else '各地区'}。")
    summary = ' '.join(parts)

    # Market Pulse must be scoped to the displayed batch. Otherwise historical
    # date panels show today's signals under an older date.
    mp_events = [
        e for e in non_chinese
        if e.get('event_types', ['other'])[0] != 'other'
    ]
    mp_events.sort(key=lambda e: (event_score(e), e.get('date', '')), reverse=True)
    mp_events = mp_events[:7]

    # ── P0 Agent：读取 AI 趋势分析，覆盖程序摘要 ──
    try:
        summary_path = data_path('summary.json')
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as sf:
                ai_summaries = json.load(sf)
            today_s = summary_date or _cn_today()
            if total and today_s in ai_summaries:
                ai_text = ai_summaries[today_s].strip()
                if len(ai_text) >= 20:
                    summary = ai_text  # 用 AI 生成的趋势分析代替程序摘要
    except Exception:
        pass  # 降级：保留程序生成摘要

    return {
        'total_events': total,
        'total_signals': len(signals),
        'funding': funding,
        'ma': ma,
        'earnings': earnings,
        'strategy': strategy,
        'regions': len(region_counts),
        'region_distribution': region_counts,
        'type_counts': type_counts,
        'headline': headline,
        'summary': summary,
        'top3': mp_events[:3],  # 保持兼容
        'top7': mp_events,  # 新增：今日要点7条
    }

def build_trend_groups(events):
    """将事件按趋势主题分组，如果没有 trend_topic 则按 company_name / insight_label 降级"""
    groups = {}
    for e in events:
        topic = e.get('trend_topic')
        if not topic:
            region = e.get('region', '')
            company = e.get('company_name', '')
            if company:
                topic = f"{company}动态 — {region}" if region else f"{company}动态"
            else:
                label = e.get('insight_label', '其他')
                topic = f"{label} — {region}" if region else label
        groups.setdefault(topic, []).append(e)
    result = [{'topic': k, 'events': v} for k, v in groups.items()]
    result.sort(key=lambda x: len(x['events']), reverse=True)
    return result

def keep_focus_date_clusters(clusters, limit=3):
    """Only keep rolling-window clusters that actually touch the selected date."""
    return [cluster for cluster in clusters or [] if cluster.get('has_focus_date')][:limit]
