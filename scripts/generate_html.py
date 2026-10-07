"""
生成全球互联网动态情报站 HTML 页面
评分系统：基于 Galtung & Ruge 新闻价值理论 + 金融情报平台通用因子
"""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from jinja2 import Environment, select_autoescape

# 仓库根与 data 目录：一律基于 __file__ 定位，不依赖调用进程的 CWD。
# 历史上这里用裸相对路径 'data/xxx.json'，CI 从仓库根跑没问题，
# 但从 scripts/ 目录跑（如直接执行 scripts/test_*.py）会找不到文件。
# 见 docs/ARCHITECTURE.md「路径锚定仓库根」。
try:
    from repo_paths import REPO_ROOT, DATA_DIR, DOCS_DIR, data_path, docs_path
except ImportError:
    from scripts.repo_paths import REPO_ROOT, DATA_DIR, DOCS_DIR, data_path, docs_path

# 提示词外置：编辑层提示词在 scripts/prompts/editorial-{weekly,monthly}.md（P1）
try:
    from prompt_loader import prompt_version, render_prompt
except ImportError:
    from scripts.prompt_loader import prompt_version, render_prompt

try:
    from event_dates import is_display_date
    from event_contract import prepare_event_contract
    from event_value import (
        classify_bd_priority,
        event_score,
        event_type,
        is_company_quality_signal,
        is_google_news_event,
        follow_up_window_for_priority,
    )
    from signal_clusters import build_signal_clusters
    from narratives import build_narrative
    from period_themes import build_monthly_trends, build_weekly_themes, build_company_changes, build_industry_changes
    from entity_signal_conversion_report import event_matches_entity
    from internet_relevance import is_mainline_internet_event
    from fetch_news import _fingerprint_match, _is_same_event
    from view_selectors import (
        select_company_events,
        select_company_quality_events,
        select_homepage_events,
        is_main_view_event,
        is_period_high_value_event,
        select_main_list_events,
        select_mature_main_date,
        select_period_high_value_events,
        select_review_events,
        signal_sort_key,
    )
except ImportError:
    from scripts.event_dates import is_display_date
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import (
        classify_bd_priority,
        event_score,
        event_type,
        is_company_quality_signal,
        is_google_news_event,
        follow_up_window_for_priority,
    )
    from scripts.signal_clusters import build_signal_clusters
    from scripts.narratives import build_narrative
    from scripts.period_themes import build_monthly_trends, build_weekly_themes, build_company_changes, build_industry_changes
    from scripts.entity_signal_conversion_report import event_matches_entity
    from scripts.internet_relevance import is_mainline_internet_event
    from scripts.fetch_news import _fingerprint_match, _is_same_event
    from scripts.view_selectors import (
        select_company_events,
        select_company_quality_events,
        select_homepage_events,
        is_main_view_event,
        is_period_high_value_event,
        select_main_list_events,
        select_mature_main_date,
        select_period_high_value_events,
        select_review_events,
        signal_sort_key,
    )

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))

# Windows 控制台默认 GBK，emoji 打印会抛 UnicodeEncodeError；统一 UTF-8 输出
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


try:
    from publication.score import (
        _cn_now,
        _cn_today,
        CATEGORY_MAP,
        INSIGHT_LABEL_MAP,
        TRUNCATED_JUNK,
        _parse_amount,
        _format_amount,
        _extract_title_publisher,
        AMOUNT_BUCKETS,
        _amount_score,
        EVT_SCORE,
        REGION_WEIGHT,
        CHINESE_CAPITAL_COMPANIES,
        REGION_COMPANIES,
        _is_hot_industry,
        _has_top_investor,
        COMPARISON_VERBS,
        _chinese_entity_hit,
        _in_comparison_context,
        _is_chinese_capital,
        calculate_score,
    )
except ImportError:
    from scripts.publication.score import (
        _cn_now,
        _cn_today,
        CATEGORY_MAP,
        INSIGHT_LABEL_MAP,
        TRUNCATED_JUNK,
        _parse_amount,
        _format_amount,
        _extract_title_publisher,
        AMOUNT_BUCKETS,
        _amount_score,
        EVT_SCORE,
        REGION_WEIGHT,
        CHINESE_CAPITAL_COMPANIES,
        REGION_COMPANIES,
        _is_hot_industry,
        _has_top_investor,
        COMPARISON_VERBS,
        _chinese_entity_hit,
        _in_comparison_context,
        _is_chinese_capital,
        calculate_score,
    )

# ─── 预设公司名单 ─────────────────────────────────────────────

try:
    from publication.entity import (
        REGION_ORDER,
        load_entity_pool,
        _portfolio_by_entity,
        build_entity_event_timelines,
    )
except ImportError:
    from scripts.publication.entity import (
        REGION_ORDER,
        load_entity_pool,
        _portfolio_by_entity,
        build_entity_event_timelines,
    )

# ─── BD opportunity fallback ────────────────────────────────

try:
    from publication.bd import (
        VERTICAL_DEAL_SOURCES,
        REGIONAL_ECOSYSTEM_SOURCES,
        OFFICIAL_IR_SOURCE_HINTS,
        BD_TRIGGER_RULES,
        OPPORTUNITY_BY_TRIGGER,
        OPPORTUNITY_BY_TYPE,
        SOURCE_ROLE_BY_TIER,
        KNOWN_COMPANIES,
        CHINESE_OUTBOUND,
        _extract_subject,
        _build_reason,
        _infer_source_tier,
        infer_frontend_bd_context,
        ensure_business_fields,
        enrich,
    )
except ImportError:
    from scripts.publication.bd import (
        VERTICAL_DEAL_SOURCES,
        REGIONAL_ECOSYSTEM_SOURCES,
        OFFICIAL_IR_SOURCE_HINTS,
        BD_TRIGGER_RULES,
        OPPORTUNITY_BY_TRIGGER,
        OPPORTUNITY_BY_TYPE,
        SOURCE_ROLE_BY_TIER,
        KNOWN_COMPANIES,
        CHINESE_OUTBOUND,
        _extract_subject,
        _build_reason,
        _infer_source_tier,
        infer_frontend_bd_context,
        ensure_business_fields,
        enrich,
    )

try:
    from publication.date_panel import (
        DAILY_EVENT_GROUPS,
        _daily_event_group_key,
        build_daily_event_groups,
        build_daily_navigation_copy,
        build_date_panel,
        select_homepage_events_for_date,
        strip_cluster_event_payloads,
        group_events_by_date,
    )
except ImportError:
    from scripts.publication.date_panel import (
        DAILY_EVENT_GROUPS,
        _daily_event_group_key,
        build_daily_event_groups,
        build_daily_navigation_copy,
        build_date_panel,
        select_homepage_events_for_date,
        strip_cluster_event_payloads,
        group_events_by_date,
    )
try:
    from publication.summary import (
        load_events,
        split_company_events,
        get_signal_events,
        build_weekly_summary,
        build_trend_groups,
        keep_focus_date_clusters,
    )
except ImportError:
    from scripts.publication.summary import (
        load_events,
        split_company_events,
        get_signal_events,
        build_weekly_summary,
        build_trend_groups,
        keep_focus_date_clusters,
    )


try:
    from publication.display_dedupe import (
        DISPLAY_ENTITY_STOPWORDS,
        _normalize_display_subject,
        _title_subject_key,
        _display_subject_key,
        _normalized_title_key,
        _nearby_days,
        _title_similarity,
        _title_tokens,
        dedupe_display_events,
    )
except ImportError:
    from scripts.publication.display_dedupe import (
        DISPLAY_ENTITY_STOPWORDS,
        _normalize_display_subject,
        _title_subject_key,
        _display_subject_key,
        _normalized_title_key,
        _nearby_days,
        _title_similarity,
        _title_tokens,
        dedupe_display_events,
    )


try:
    from publication.clusters import (
        _cluster_objects,
        _period_event_object,
        _weekly_signal_key,
        _weekly_window_rank,
        _build_broad_weekly_focus_windows,
        _entity_region_map,
        _build_weekly_focus_windows,
    )
except ImportError:
    from scripts.publication.clusters import (
        _cluster_objects,
        _period_event_object,
        _weekly_signal_key,
        _weekly_window_rank,
        _build_broad_weekly_focus_windows,
        _entity_region_map,
        _build_weekly_focus_windows,
    )
try:
    from publication.opportunity import (
        _bd_priority_rank,
        _short_event_text,
        _build_top_opportunities,
        _build_regional_map,
        _build_actions,
        _build_customer_tiers,
        _build_themes,
    )
except ImportError:
    from scripts.publication.opportunity import (
        _bd_priority_rank,
        _short_event_text,
        _build_top_opportunities,
        _build_regional_map,
        _build_actions,
        _build_customer_tiers,
        _build_themes,
    )


# ============================================================
# 编辑层缓存：编辑导读由输入主题唯一决定，封档周期输入冻结即输出冻结，
# 不应每班重调 AI。缓存键 = 周期 + 输入指纹 + 提示词版本；
# 提示词版本 = scripts/prompts/editorial-*.md 的内容哈希，
# 改提示词自动让旧缓存失效，不再需要手工递增版本号（2026-10-06 P1）。
# ============================================================


try:
    from editorial.editorial import (
        _editorial_prompt_version,
        _editorial_cache_path,
        _load_editorial_cache,
        _save_editorial_cache,
        _editorial_cache_get,
        _editorial_cache_put,
        _editorial_input_hash,
        build_weekly_editorial,
        build_monthly_editorial,
    )
except ImportError:
    from scripts.editorial.editorial import (
        _editorial_prompt_version,
        _editorial_cache_path,
        _load_editorial_cache,
        _save_editorial_cache,
        _editorial_cache_get,
        _editorial_cache_put,
        _editorial_input_hash,
        build_weekly_editorial,
        build_monthly_editorial,
    )
try:
    from reports.period import (
        _load_aihot_archive,
        _aihot_items_to_list,
        _latest_aihot_items,
        build_period_report,
        build_weekly_archives,
        build_monthly_archives,
    )
except ImportError:
    from scripts.reports.period import (
        _load_aihot_archive,
        _aihot_items_to_list,
        _latest_aihot_items,
        build_period_report,
        build_weekly_archives,
        build_monthly_archives,
    )


try:
    from publication.display import (
        clean_display_title,
        split_judgment,
        _has_cjk,
        _is_good_summary,
        _front_trend_topic,
        enrich_frontend_fields,
        _front_overview,
        refine_daily_headline,
        build_company_cards,
        group_company_cards,
    )
except ImportError:
    from scripts.publication.display import (
        clean_display_title,
        split_judgment,
        _has_cjk,
        _is_good_summary,
        _front_trend_topic,
        enrich_frontend_fields,
        _front_overview,
        refine_daily_headline,
        build_company_cards,
        group_company_cards,
    )

try:
    from publication.loaders import (
        load_site_updates,
        load_entity_observation_ledger,
        load_model_leaderboard,
        load_aihot_hot,
        _clean_hot_title,
        CHINESE_WEEKDAYS,
    )
except ImportError:
    from scripts.publication.loaders import (
        load_site_updates,
        load_entity_observation_ledger,
        load_model_leaderboard,
        load_aihot_hot,
        _clean_hot_title,
        CHINESE_WEEKDAYS,
    )


try:
    from publication.review import _quality_main_events, build_review_events
except ImportError:
    from scripts.publication.review import _quality_main_events, build_review_events


try:
    from publication.context import build_display_context
except ImportError:
    from scripts.publication.context import build_display_context


# ─── RSS 上下文缓存（避免整站装配跑两遍）─────────────────────────
# generate_feed.py 需要同一份展示上下文。原先它自己再调一次
# build_display_context()，于是整站装配在每次渲染里跑两遍——本地单次约 25 秒
# （其中展示层判重是 O(n²) 的主要来源），是渲染里最大的一块固定成本，也是
# CI 渲染顶穿超时的主因之一（2026-10-07 实测）。
# 这里把 RSS 真正需要的最小字段落到 data/.cache/（.gitignore 已排除），
# 并带上 data/events.json 的指纹；指纹不符就丢弃缓存、由 RSS 侧重算，
# 所以不存在「用了过期上下文」的窗口。
def _rss_context_path():
    return data_path('.cache', 'rss_context.json')


def _events_fingerprint():
    try:
        stat = os.stat(data_path('events.json'))
    except OSError:
        return None
    return f'{stat.st_size}:{int(stat.st_mtime)}'


def dump_rss_context(context):
    """把 RSS 需要的展示上下文落到本地缓存。失败不影响渲染。"""
    payload = {
        'fingerprint': _events_fingerprint(),
        'main_date': context.get('main_date'),
        'today_events': context.get('today_events') or [],
        'all_events_for_list': context.get('all_events_for_list') or [],
    }
    try:
        os.makedirs(os.path.dirname(_rss_context_path()), exist_ok=True)
        with open(_rss_context_path(), 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except OSError:
        pass


def generate_html(force=False, preview_mode=False):
    context = build_display_context()
    dump_rss_context(context)
    events = context['events']
    sorted_dates = context['sorted_dates']
    today_str = context['today_str']
    main_date = context['main_date']
    main_events = context['main_events']
    all_feed = context['all_feed']
    company_events = context['company_events']
    generic_events = context['generic_events']
    company_events_filtered = context['company_events_filtered']
    preset_company_list = context['preset_company_list']
    all_events_for_list = context['all_events_for_list']
    today_events = context['today_events']
    raw_today_events = context['raw_today_events']
    latest_data_date = context['latest_data_date']
    latest_visible_count = context['latest_visible_count']
    batch_notice = context['batch_notice']
    period_reference_date = context['period_reference_date']
    entity_observation_ledger = context['entity_observation_ledger']

    preset_company_list = build_company_cards(preset_company_list, main_date, entity_observation_ledger)
    company_groups = group_company_cards(preset_company_list)

    # 历史tab：除主tab批次之外的所有有内容日期
    history_dates = [d for d in sorted_dates if d != main_date]
    history = [(d, events.get(d, [])) for d in history_dates if events.get(d, [])]

    signals = get_signal_events(events)
    # ⚠️ 关键：weekly 必须从 today_events 计数，不是 all_feed
    # all_feed 过滤了 other 类型和低分事件，但页面上展示的是 today_events
    # 两个数据源不一致导致"共0条动态"而实际有 9 条的矛盾
    weekly = build_weekly_summary(today_events, signals, main_events, events, summary_date=main_date)
    # 公司动态也加入周报摘要
    weekly['company_count'] = len(company_events_filtered)
    weekly['company_list'] = preset_company_list

    trend_groups = build_trend_groups(today_events)
    repair_events = build_review_events(raw_today_events)
    daily_trend_signals = weekly.get('top3', [])
    signal_clusters = keep_focus_date_clusters(
        build_signal_clusters(all_events_for_list, main_date, limit=12)
    )
    narrative = build_narrative(signal_clusters, fallback_events=today_events)
    signal_clusters = strip_cluster_event_payloads(narrative.get('clusters', []))
    evidence_events = narrative.get('evidence_events') or today_events[:5]
    daily_event_groups = build_daily_event_groups(today_events)
    daily_headline, daily_lead = build_daily_navigation_copy(daily_event_groups)
    daily_trend_judgment = daily_lead
    total_stories = len(today_events)
    dt = datetime.strptime(today_str, '%Y-%m-%d')
    vol_label = f"VOL.{today_str}"
    cn_date = f"{dt.year}年{dt.month}月{dt.day}日 星期{CHINESE_WEEKDAYS[dt.weekday()]}"

    # 全部事件按日期分组
    date_grouped_events = group_events_by_date(all_events_for_list)
    date_event_counts = {group['date']: len(group['events']) for group in date_grouped_events}

    # 预计算各日期面板数据（供 JS 翻页切换）
    date_panels = {}
    available_dates = []
    for d in sorted_dates:
        raw_day_evs = [e for e in all_events_for_list if (e.get('date') or '')[:10] == d]
        day_evs = select_homepage_events_for_date(all_events_for_list, d)
        if not day_evs and not raw_day_evs:
            continue
        available_dates.append(d)
        date_panels[d] = build_date_panel(
            d,
            day_evs,
            events,
            raw_day_evs,
            cluster_events=all_events_for_list,
        )
        date_panels[d]['event_list_count'] = date_event_counts.get(d, len(raw_day_evs))

    # 今日页平铺事件：精选→重点→观察→待确认（新界面语言）
    merged_events = []
    for group in daily_event_groups:
        for ev in group['events']:
            merged_events.append({'ev': ev, 'tag': group['label'], 'pri': group['key']})
    for ev in repair_events:
        merged_events.append({'ev': ev, 'tag': '待确认', 'pri': 'review'})
    summary_parts = [f"{g['label']} {len(g['events'])}" for g in daily_event_groups if g['events']]
    if repair_events:
        summary_parts.append(f"待确认 {len(repair_events)}")
    summary_counts = ' · '.join(summary_parts)

    # 历史导航：天数 + 当日全部事件数（与"全部事件"面板展开后一致）
    history_dates = [d for d in available_dates if d != main_date][:5]
    history_counts = {
        d: len([e for e in all_events_for_list if (e.get('date') or '')[:10] == d])
        for d in history_dates
    }
    history_total = max(0, len(available_dates) - 1)

    # 周报/月报聚合用全量事件（含所有历史批次），不用展示管线的过滤视图——
    # 展示视图只保留主日期附近窗口，跨周证据会被砍到不足以判断变化。
    full_events_for_period = [
        e for date_key, evs in events.items()
        for e in (evs if isinstance(evs, list) else [])
    ]
    weekly_archives = build_weekly_archives(full_events_for_period, period_reference_date,
                                            require_editorial=not preview_mode)
    monthly_archives = build_monthly_archives(full_events_for_period, period_reference_date,
                                              require_editorial=not preview_mode)
    weekly_report = weekly_archives[0] if weekly_archives else build_period_report([], period_reference_date, period_reference_date, '本周', 'empty', 'open')
    monthly_report = monthly_archives[0] if monthly_archives else build_period_report([], period_reference_date, period_reference_date, '本月', 'empty', 'open')
    site_updates = load_site_updates()
    model_leaderboard = load_model_leaderboard()
    aihot_hot = _aihot_items_to_list(load_aihot_hot()) or _latest_aihot_items()
    update_time = f"最新采集 {period_reference_date}｜展示 {main_date} 成熟批次"

    env = Environment(autoescape=select_autoescape(['html', 'htm', 'xml']))
    template = env.from_string(open('scripts/template.html', 'r', encoding='utf-8').read())
    html = template.render(
        weekly=weekly,
        weekly_report=weekly_report,
        monthly_report=monthly_report,
        weekly_archives=weekly_archives,
        monthly_archives=monthly_archives,
        all_feed=all_feed,
        all_events_for_list=all_events_for_list,
        date_grouped_events=date_grouped_events,
        history=history,
        main_date=main_date,
        company_events=company_events,
        company_list=preset_company_list,
        company_groups=company_groups,
        update_time=update_time,
        trend_groups=trend_groups,
        repair_events=repair_events,
        daily_trend_judgment=daily_trend_judgment,
        daily_headline=daily_headline,
        daily_lead=daily_lead,
        daily_trend_signals=daily_trend_signals,
        signal_clusters=signal_clusters,
        evidence_events=evidence_events,
        daily_event_groups=daily_event_groups,
        merged_events=merged_events,
        summary_counts=summary_counts,
        history_dates=history_dates,
        history_counts=history_counts,
        history_total=history_total,
        narrative=narrative,
        total_stories=total_stories,
        vol_label=vol_label,
        cn_date=cn_date,
        date_panels=date_panels,
        date_event_counts=date_event_counts,
        available_dates=available_dates,
        latest_data_date=latest_data_date,
        latest_visible_count=latest_visible_count,
        batch_notice=batch_notice,
        site_updates=site_updates,
        model_leaderboard=model_leaderboard,
        aihot_hot=aihot_hot,
        feedback_endpoint=os.getenv('FEEDBACK_ENDPOINT', ''),
    )
    html = '\n'.join(line.rstrip() for line in html.splitlines()) + '\n'

    os.makedirs(DOCS_DIR, exist_ok=True)
    index_path = docs_path('preview.html') if preview_mode else docs_path('index.html')

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)

    mode = '预览' if preview_mode else '生产'
    print(f"OK | {mode}模式 | 通用{len(generic_events)}条 | 公司{len(company_events)}条 | {len(history)}天往期")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成全球互联网动态情报站 HTML')
    parser.add_argument('--force', action='store_true', help='强制重写 index.html（跳过内容对比）')
    parser.add_argument('--preview', action='store_true', help='生成本地预览文件 preview.html（不覆盖 index.html）')
    args = parser.parse_args()

    if args.preview:
        # 预览模式：生成到 preview.html
        generate_html(preview_mode=True)
    else:
        # 默认模式：生成到 index.html
        generate_html(force=args.force)
