"""Check dashboard data health after events have been stored.

It verifies the collection metrics -> stored-data -> selector -> display contract.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

try:
    from collection_timing_report import build_collection_timing_rows, print_collection_timing_report
    from daily_coverage_report import build_daily_coverage_report
    from event_contract import prepare_event_contract
    from event_dates import is_display_date
    from generate_html import build_company_cards, build_display_context
    from run_metrics import latest_run_metrics
    from source_conversion_report import build_source_conversion_report
    from source_quality_report import build_source_quality_report
    from view_selectors import select_feed_events, select_main_list_events, select_review_events
except ImportError:
    from scripts.collection_timing_report import build_collection_timing_rows, print_collection_timing_report
    from scripts.daily_coverage_report import build_daily_coverage_report
    from scripts.event_contract import prepare_event_contract
    from scripts.event_dates import is_display_date
    from scripts.generate_html import build_company_cards, build_display_context
    from scripts.run_metrics import latest_run_metrics
    from scripts.source_conversion_report import build_source_conversion_report
    from scripts.source_quality_report import build_source_quality_report
    from scripts.view_selectors import select_feed_events, select_main_list_events, select_review_events

# 仓库路径一律锚定 __file__，不依赖调用进程 CWD（见 docs/ARCHITECTURE.md）。
# 本模块会被 test_data_health.py 从 scripts/ 目录直接执行，
# 裸相对路径 'data/...' 在那种情况下指向 scripts/data/，会 FileNotFoundError。
try:
    from repo_paths import REPO_ROOT, DATA_DIR, DOCS_DIR, data_path, docs_path
except ImportError:
    from scripts.repo_paths import REPO_ROOT, DATA_DIR, DOCS_DIR, data_path, docs_path

# 阶段 0 四道不变规则的判定函数（2026-11-09 从 test_rule_checks.py 搬进生产模块）。
# 之前它们只在本地测试里被跑，线上自检从不调用——「报警器造好没插电」。
try:
    from rule_checks import (
        build_source_state,
        check_capability_validity,
        check_criterion_before_sort_change,
        check_latest_state_basis,
        check_source_decay,
    )
except ImportError:
    from scripts.rule_checks import (
        build_source_state,
        check_capability_validity,
        check_criterion_before_sort_change,
        check_latest_state_basis,
        check_source_decay,
    )


def _event_date(event):
    return (event.get('date') or '')[:10]


def _norm_title(event):
    title = (
        event.get('display_title')
        or event.get('summary_short')
        or event.get('title')
        or ''
    ).lower()
    return re.sub(r'[^\w]+', '', title)


def _date_range(end_date, days):
    end = datetime.strptime(end_date, '%Y-%m-%d')
    return [
        (end - timedelta(days=offset)).strftime('%Y-%m-%d')
        for offset in range(days)
    ]


def _source_is_google(event):
    source = (event.get('source') or '').lower()
    tier = event.get('source_tier') or ''
    url = (event.get('url') or '').lower()
    return source == 'google news' or tier == 'L5 Google News 补漏源' or 'news.google.com' in url


def _duplicate_ratio(events):
    keys = [
        _norm_title(event)
        for event in events
        if len(_norm_title(event)) > 10
    ]
    if not keys:
        return 0.0, 0
    counts = Counter(keys)
    duplicate_items = sum(count - 1 for count in counts.values() if count > 1)
    return duplicate_items / len(keys), duplicate_items


def _future_event_count(path=None, now=None, data=None):
    path = path or data_path('events.json')
    if data is None:
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return 0
    # 事件日期按北京时间落库而 CI runner 是 UTC，跨时区班次会把当天事件误判为未来；
    # 个别源（changelog 类）日期会提前一天出现，放行 1 天余量，只拦真正超前的脏数据。
    if now is None:
        now = datetime.now(timezone(timedelta(hours=8)))
    elif isinstance(now, str):
        now = datetime.fromisoformat(now)
    horizon = (now.date() + timedelta(days=1)).isoformat()
    count = 0
    for bucket, events in (data or {}).items():
        for event in events or []:
            value = event.get('published_at') or event.get('article_date') or event.get('date') or bucket
            if value and str(value)[:10] > horizon:
                count += 1
    return count


def _load_prepared_events(path=None):
    """Load full events.json and prepare every event once for shared reuse.

    Unlike load_events() (display window, filtered by is_display_date), this
    keeps all dates so reports counting stored events see the full set.
    """
    path = path or data_path('events.json')
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [prepare_event_contract(dict(event)) for event in data]
    return {
        date_key: [prepare_event_contract(dict(event)) for event in events or []]
        for date_key, events in data.items()
    }


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _observation_health(ledger, pool, jobs, candidates):
    must_ids = {
        row.get('entity_id')
        for row in (pool.get('portfolio') or {}).get('must') or []
        if row.get('entity_id')
    }
    rows = ledger.get('entities') or []
    must_rows = [row for row in rows if row.get('entity_id') in must_ids]
    effective = [
        row for row in must_rows
        if row.get('last_success_at')
        and row.get('coverage_status') not in {'pending', 'failed', 'unverified'}
    ]
    failed_points = [
        f"{row.get('entity')}:{point.get('point_type')}"
        for row in must_rows
        for point in row.get('observation_points') or []
        if point.get('status') == 'failed'
    ]
    jobs_failed = [
        source for source, stats in (jobs.get('source_stats') or {}).items()
        if stats.get('fetch_status') in {'failed', 'parse_failed'}
    ]
    candidate_rows = candidates.get('candidates') or []
    status_counts = Counter(row.get('status') or 'unknown' for row in candidate_rows)
    return {
        'must_total': len(must_rows),
        'must_effective': len(effective),
        'must_coverage_ratio': len(effective) / len(must_rows) if must_rows else 0,
        'failed_observation_points': failed_points,
        'jobs_failed': jobs_failed,
        'candidate_status_counts': dict(status_counts),
        'candidate_backlog': sum(status_counts.get(status, 0) for status in ('new', 'accumulating', 'qualified')),
        'candidate_promoted': status_counts.get('promoted', 0),
    }


def build_quick_health_report(
    events_path=None,
    metrics_path=None,
    ledger_path=None,
    pool_path=None,
    jobs_path=None,
    candidates_path=None,
    now=None,
):
    """Read the latest persisted health facts without rebuilding report views."""
    # 路径默认值在这里补全（不写成函数默认参数，避免 import 期求值的坑）
    events_path = events_path or data_path('events.json')
    metrics_path = metrics_path or data_path('run_metrics.json')
    ledger_path = ledger_path or data_path('entity_observation_ledger.json')
    pool_path = pool_path or data_path('entity_pool.json')
    jobs_path = jobs_path or data_path('job_observation_metrics.json')
    candidates_path = candidates_path or data_path('signal_candidates.json')
    # 事件日期按北京时间落库，CI runner 是 UTC；不锚定北京时区会把当天数据误判为未来。
    if now is None:
        now = datetime.now(timezone(timedelta(hours=8)))
    events = _load_json(events_path, {})
    valid_dates = sorted(
        date_key for date_key in events
        if events.get(date_key) and is_display_date(date_key, now=now)
    )
    latest_data_date = valid_dates[-1] if valid_dates else ''
    records = _load_json(metrics_path, [])
    latest_metrics = records[-1] if isinstance(records, list) and records else {}
    filtering = latest_metrics.get('filtering') or {}
    storage = latest_metrics.get('storage') or {}
    analysis = latest_metrics.get('analysis') or {}
    quality = analysis.get('quality') or {}
    observation = _observation_health(
        _load_json(ledger_path, {}),
        _load_json(pool_path, {}),
        _load_json(jobs_path, {}),
        _load_json(candidates_path, {}),
    )
    return {
        'latest_data_date': latest_data_date,
        'latest_bucket_events': len(events.get(latest_data_date) or []) if latest_data_date else 0,
        'future_event_count': _future_event_count(events_path, now=now, data=events),
        'run_metrics': latest_metrics,
        'run_date': latest_metrics.get('date') or '',
        'scope_qualified': filtering.get('scope_qualified_count', 0),
        'scope_candidate': filtering.get('scope_candidate_count', 0),
        'scope_filtered': filtering.get('scope_filtered_count', 0),
        'added_count': storage.get('added_count', 0),
        'repair_ratio': quality.get('repair_ratio', 0),
        'fallback_or_failed': quality.get('fallback_or_failed', 0),
        'analysis_total': quality.get('total', 0),
        'observation_health': observation,
    }


def print_quick_report(report):
    observation = report.get('observation_health') or {}
    print(
        "quick health | latest_data_date={latest_data_date} latest_events={latest_bucket_events} "
        "future={future_event_count} run_date={run_date} added={added_count}".format(**report)
    )
    print(
        "scope | qualified={scope_qualified} candidate={scope_candidate} "
        "filtered={scope_filtered}".format(**report)
    )
    print(
        "analysis | total={analysis_total} repair_ratio={repair_ratio:.1%} "
        "fallback_or_failed={fallback_or_failed}".format(**report)
    )
    print(
        "observation | must={must_effective}/{must_total} coverage={must_coverage_ratio:.1%} "
        "failed_points={failed} jobs_failed={jobs_failed_count} candidate_backlog={candidate_backlog} "
        "promoted={candidate_promoted}".format(
            failed=len(observation.get('failed_observation_points') or []),
            jobs_failed_count=len(observation.get('jobs_failed') or []),
            **observation,
        )
    )
    if observation.get('failed_observation_points'):
        print("observation failures | " + ', '.join(observation['failed_observation_points']))
    if observation.get('jobs_failed'):
        print("jobs failures | " + ', '.join(observation['jobs_failed']))


def build_health_report(days=7):
    context = build_display_context()
    main_date = context['main_date']
    all_visible = context['all_events_for_list']
    today_events = context['today_events']
    raw_today = context['raw_today_events']

    feed_events, fallback_feed_date = select_feed_events(today_events, all_visible)
    company_cards = build_company_cards(
        context['preset_company_list'],
        main_date,
        context.get('entity_observation_ledger'),
    )
    company_quality_nonzero = sum(1 for card in company_cards if card.get('quality_30', 0) > 0)

    dates = _date_range(context['latest_data_date'] or main_date, days)
    daily = []
    recent_events = []
    for date_key in dates:
        raw_day = [event for event in all_visible if _event_date(event) == date_key]
        main_day = select_main_list_events(raw_day)
        review_day = select_review_events(raw_day, limit=None)
        recent_events.extend(raw_day)
        daily.append({
            'date': date_key,
            'visible': len(raw_day),
            'main': len(main_day),
            'review': len(review_day),
            'google': sum(1 for event in raw_day if _source_is_google(event)),
        })

    duplicate_ratio, duplicate_items = _duplicate_ratio(recent_events)
    feed_google = sum(1 for event in feed_events if _source_is_google(event))
    feed_google_ratio = feed_google / len(feed_events) if feed_events else 0.0
    run_metrics = latest_run_metrics()
    collection_timing = build_collection_timing_rows(limit=8)
    source_report = build_source_quality_report(days=days, context=context)
    all_events = _load_prepared_events()
    source_conversion = build_source_conversion_report(days=days, events=all_events)
    daily_coverage = build_daily_coverage_report(days=days, events=all_events)
    future_event_count = _future_event_count(data=all_events)
    observation_health = _observation_health(
        context.get('entity_observation_ledger') or {},
        _load_json(data_path('entity_pool.json'), {}),
        _load_json(data_path('job_observation_metrics.json'), {}),
        _load_json(data_path('signal_candidates.json'), {}),
    )

    return {
        'main_date': main_date,
        'latest_data_date': context['latest_data_date'],
        'run_metrics': run_metrics,
        'collection_timing': collection_timing,
        'today_events': len(today_events),
        'raw_today_events': len(raw_today),
        'all_visible_events': len(all_visible),
        'company_quality_nonzero': company_quality_nonzero,
        'feed_entries': len(feed_events),
        'feed_google': feed_google,
        'feed_google_ratio': feed_google_ratio,
        'feed_fallback_date': fallback_feed_date,
        'duplicate_ratio': duplicate_ratio,
        'duplicate_items': duplicate_items,
        'daily': daily,
        'source_quality': source_report,
        'source_conversion': source_conversion,
        'daily_coverage': daily_coverage,
        'future_event_count': future_event_count,
        'observation_health': observation_health,
    }


def print_report(report):
    metrics = report.get('run_metrics') or {}
    collection = metrics.get('collection') or {}
    filtering = metrics.get('filtering') or {}
    scoring = metrics.get('scoring') or {}
    storage = metrics.get('storage') or {}
    if metrics:
        print(
            "run | date={date} env={environment} raw={raw} unique={unique} "
            "smart_filtered={smart_filtered} ai_filtered={ai_filtered} "
            "ai_tier={ai_tier} program_tier={program_tier} dropped={dropped} "
            "added={added} duplicate_skipped={duplicate_skipped}".format(
                date=metrics.get('date', ''),
                environment=metrics.get('environment', ''),
                raw=collection.get('raw_count', 0),
                unique=collection.get('unique_count', 0),
                smart_filtered=filtering.get('smart_filtered_count', 0),
                ai_filtered=filtering.get('ai_filtered_count', 0),
                ai_tier=scoring.get('ai_tier_count', 0),
                program_tier=scoring.get('program_tier_count', 0),
                dropped=scoring.get('dropped_count', 0),
                added=storage.get('added_count', 0),
                duplicate_skipped=storage.get('duplicate_skipped', 0),
            )
        )
    else:
        print("run | no run_metrics.json found")
    print(
        "health | main_date={main_date} latest_data_date={latest_data_date} "
        "today={today_events} raw_today={raw_today_events} visible={all_visible_events}".format(**report)
    )
    print(
        "health | company_quality_nonzero={company_quality_nonzero} "
        "feed_entries={feed_entries} feed_google={feed_google} "
        "feed_google_ratio={feed_google_ratio:.1%} duplicate_ratio={duplicate_ratio:.1%} "
        "duplicate_items={duplicate_items}".format(**report)
    )
    print(f"health | future_event_count={report.get('future_event_count', 0)}")
    observation = report.get('observation_health') or {}
    print(
        "observation | must={must_effective}/{must_total} coverage={must_coverage_ratio:.1%} "
        "failed_points={failed} jobs_failed={jobs_failed_count} candidate_backlog={candidate_backlog} "
        "promoted={candidate_promoted}".format(
            failed=len(observation.get('failed_observation_points') or []),
            jobs_failed_count=len(observation.get('jobs_failed') or []),
            **observation,
        )
    )
    if observation.get('failed_observation_points'):
        print("observation failures | " + ', '.join(observation['failed_observation_points']))
    if observation.get('jobs_failed'):
        print("jobs failures | " + ', '.join(observation['jobs_failed']))
    if report['feed_fallback_date']:
        print(f"health | feed_fallback_date={report['feed_fallback_date']}")
    if report.get('collection_timing'):
        print_collection_timing_report(report['collection_timing'])
    print("date | visible | main | review | google")
    for row in report['daily']:
        print(
            "{date} | {visible} | {main} | {review} | {google}".format(**row)
        )
    source_rows = (report.get('source_quality') or {}).get('rows') or []
    if source_rows:
        print("top sources | source | visible | main | high | rss | google")
        for row in source_rows[:8]:
            print(
                "{source} | {stored_visible} | {main} | {high_value} | {rss} | {google}".format(**row)
                .encode(sys.stdout.encoding or 'utf-8', errors='replace')
                .decode(sys.stdout.encoding or 'utf-8')
            )
    conversion = report.get('source_conversion') or {}
    conversion_totals = conversion.get('totals') or {}
    if conversion_totals:
        print(
            "source conversion | raw={raw} signal={signal} stored={stored} main={main} "
            "review={review} out_of_scope_industry={out_of_scope_industry} "
            "capital_only_low_actionability={capital_only_low_actionability} "
            "quality_review={quality_review} "
            "lost_after_signal≈{lost_after_signal}".format(**conversion_totals)
        )
    high_signal_low_main = (conversion.get('high_signal_low_main') or [])[:5]
    if high_signal_low_main:
        print("source conversion watch | source | signal | stored | main | lost_after_signal≈")
        for row in high_signal_low_main:
            print(
                "{source} | {signal} | {stored} | {main} | {lost_after_signal}".format(**row)
                .encode(sys.stdout.encoding or 'utf-8', errors='replace')
                .decode(sys.stdout.encoding or 'utf-8')
            )
    governance_actions = conversion.get('governance_actions') or {}
    if governance_actions:
        print("source governance actions | action | source | signal | stored | main")
        for action in (
            'audit_raw_signal_quality',
            'instrument_candidate_loss',
            'audit_conversion_loss',
            'downgrade_or_reclassify',
            'promote_weight',
        ):
            for row in governance_actions.get(action, [])[:3]:
                print(
                    f"{action} | {row['source']} | {row['signal']} | "
                    f"{row['stored']} | {row['main']}"
                )
    coverage = report.get('daily_coverage') or {}
    coverage_totals = coverage.get('totals') or {}
    if coverage_totals:
        print(
            "daily coverage | stored={stored_events} main={main_events} review={review_events} "
            "status={status_counts} actions={action_counts}".format(**coverage_totals)
        )
        print("daily coverage rows | date | stored | main | entities | regions | tracks | google | company | status | action")
        for row in (coverage.get('rows') or [])[:8]:
            print(
                "{date} | {stored_events} | {main_events} | {entities} | {regions} | "
                "{tracks} | {google_events} | {company_events} | {status} | {action}".format(**row)
            )


def collect_failures(report, args):
    failures = []
    if report.get('future_event_count', 0):
        failures.append(f"future_event_count {report['future_event_count']} > 0")
    if report['today_events'] < args.min_today:
        failures.append(f"today_events {report['today_events']} < {args.min_today}")
    if report['company_quality_nonzero'] < args.min_company_quality_nonzero:
        failures.append(
            f"company_quality_nonzero {report['company_quality_nonzero']} "
            f"< {args.min_company_quality_nonzero}"
        )
    if report['feed_entries'] < args.min_feed_entries:
        failures.append(f"feed_entries {report['feed_entries']} < {args.min_feed_entries}")
    if report['feed_google_ratio'] > args.max_feed_google_ratio:
        failures.append(
            f"feed_google_ratio {report['feed_google_ratio']:.1%} "
            f"> {args.max_feed_google_ratio:.1%}"
        )
    if report['duplicate_ratio'] > args.max_duplicate_ratio:
        failures.append(
            f"duplicate_ratio {report['duplicate_ratio']:.1%} "
            f"> {args.max_duplicate_ratio:.1%}"
        )
    metrics = report.get('run_metrics') or {}
    if args.require_run_metrics and not metrics:
        failures.append("run_metrics missing")
    observation = report.get('observation_health') or {}
    min_must_coverage = getattr(args, 'min_must_coverage', 0)
    max_failed_points = getattr(args, 'max_failed_observation_points', 999)
    if observation.get('must_total') and observation.get('must_coverage_ratio', 0) < min_must_coverage:
        failures.append(
            f"must_coverage_ratio {observation.get('must_coverage_ratio', 0):.1%} "
            f"< {min_must_coverage:.1%}"
        )
    if len(observation.get('failed_observation_points') or []) > max_failed_points:
        failures.append(
            f"failed_observation_points {len(observation.get('failed_observation_points') or [])} "
            f"> {max_failed_points}"
        )
    if observation.get('jobs_failed') and getattr(args, 'fail_on_jobs_error', False):
        failures.append('jobs_failed ' + ','.join(observation['jobs_failed']))
    return failures


def collect_quick_failures(report, args):
    failures = []
    if report.get('future_event_count', 0):
        failures.append(f"future_event_count {report['future_event_count']} > 0")
    if args.require_run_metrics and not report.get('run_metrics'):
        failures.append('run_metrics missing')
    observation = report.get('observation_health') or {}
    if observation.get('must_total') and observation.get('must_coverage_ratio', 0) < args.min_must_coverage:
        failures.append(
            f"must_coverage_ratio {observation.get('must_coverage_ratio', 0):.1%} "
            f"< {args.min_must_coverage:.1%}"
        )
    if len(observation.get('failed_observation_points') or []) > args.max_failed_observation_points:
        failures.append(
            f"failed_observation_points {len(observation.get('failed_observation_points') or [])} "
            f"> {args.max_failed_observation_points}"
        )
    if observation.get('jobs_failed') and args.fail_on_jobs_error:
        failures.append('jobs_failed ' + ','.join(observation['jobs_failed']))
    max_repair_ratio = getattr(args, 'max_repair_ratio', 0.9)
    if report.get('repair_ratio', 0) > max_repair_ratio:
        failures.append(
            f"repair_ratio {report.get('repair_ratio', 0):.1%} "
            f"> {max_repair_ratio:.1%}"
        )
    return failures


# 生成页里「更新日志」这一块的唯一锚点。用 section 标签而不是 data-panel 字符串：
# tab 按钮上也有 data-panel="updates"，从按钮开始切会把整页其他板块一起带进来。
_UPDATES_PANEL_START = '<section class="proto-panel" data-panel="updates">'
_UPDATES_PANEL_END = '<section class="proto-panel" data-panel="models">'
_U_VER_RE = re.compile(r'<span class="u-ver">\s*([^<]+?)\s*<span class="u-caret"')
_U_DATE_RE = re.compile(r'<small>\s*(\d{4}-\d{2}-\d{2})\s*·')
_U_COUNT_RE = re.compile(r'(\d+)\s*条记录')


def _updates_panel(html):
    """只截出更新日志板块；截不到返回空串，由调用方按「查不到」报警。"""
    left = html.find(_UPDATES_PANEL_START)
    if left < 0:
        return ''
    right = html.find(_UPDATES_PANEL_END, left + len(_UPDATES_PANEL_START))
    return html[left:right if right > left else len(html)]


def check_updates_log_sync(updates_path=None, html_path=None):
    """防更新日志断档：把「更新日志」整块与 site_updates.json 逐条对账。

    旧版只查「最新版本号这几个字在不在 index.html」，故障注入抓到两个真实漏报：
      ① 把页面上的更新日志整块删光，只要别处还残留 V6.5 字样，照样通过；
      ② 把 site_updates.json 回滚成旧版、页面仍是新版，照样通过。
    现在改成双向整块比对：条数、版本号序列、日期序列都要与清单一致，且页面里
    不允许出现清单之外的版本号（这一头专治②）。
    """
    failures = []
    updates_path = updates_path or data_path('site_updates.json')
    html_path = html_path or docs_path('index.html')
    try:
        if not os.path.exists(updates_path) or not os.path.exists(html_path):
            return failures
        with open(updates_path, encoding='utf-8') as f:
            updates = json.load(f)
        if not updates:
            return failures
        with open(html_path, encoding='utf-8') as f:
            panel = _updates_panel(f.read())
        if not panel:
            failures.append(
                '更新日志断档：页面里找不到更新日志板块（section[data-panel="updates"]），'
                '散落在别处的版本号字样不算数'
            )
            return failures

        expected_versions = [str(u.get('version') or '') for u in updates]
        expected_dates = [str(u.get('date') or '')[:10] for u in updates]
        page_versions = [v.strip() for v in _U_VER_RE.findall(panel)]
        page_dates = _U_DATE_RE.findall(panel)
        count_match = _U_COUNT_RE.search(panel)
        shown_count = int(count_match.group(1)) if count_match else None

        if shown_count != len(updates):
            failures.append(
                f'更新日志断档：页面标称 {shown_count} 条记录，'
                f'但 site_updates.json 有 {len(updates)} 条'
            )
        if not page_versions:
            failures.append('更新日志断档：页面更新日志板块里一条记录卡片都没有')
        elif page_versions != expected_versions:
            extra = [v for v in page_versions if v and v not in expected_versions]
            missing = [v for v in expected_versions if v and v not in page_versions]
            if extra:
                failures.append(
                    f'更新日志断档：页面比清单新（页面多出 {", ".join(extra[:5])}），'
                    'site_updates.json 可能被回滚或页面未重新生成'
                )
            failures.append(
                f'更新日志断档：页面版本号序列与清单不一致（页面 {len(page_versions)} 条 / '
                f'清单 {len(updates)} 条，缺 {", ".join(missing[:5]) or "无"}）'
            )
        if page_dates != expected_dates:
            failures.append('更新日志断档：页面记录日期序列与 site_updates.json 不一致')
    except Exception as e:  # noqa: BLE001
        failures.append(f"更新日志一致性检查失败: {e}")
    return failures


# 更新日志滞后宽限期（天）：代码先动、日志晚几天补是正常节奏，超过这个天数才算断档。
LOG_LAG_GRACE_DAYS = 3

# 产品代码路径：只有改动这些才需要更新日志。
# 测试文件与验收工具排除在外——加测试不需要写更新日志。
# 含 .github/workflows：workflow 改动直接决定产品行为（采集时点、提交范围、
# 失败是否可见），本次 AIHOT 事故与流水线改造都落在这些文件上。
_CODE_PATHSPEC = (
    'scripts',
    ':(exclude)scripts/test_*.py',
    ':(exclude)scripts/run_tests.py',
    '.github/workflows',
    'generate_feed.py',
    'requirements.txt',
)


def latest_code_commit_date():
    """最近一次改动产品代码的提交日期（YYYY-MM-DD）。

    浅克隆（CI 默认 fetch-depth=1）拿不到历史，或本机没有 git 时返回 None，
    由调用方静默跳过——拿不到证据就不报警，避免误报。

    用 cwd=REPO_ROOT 锚定仓库根（架构契约「路径锚定仓库根」）：paths 里的
    'scripts' 是相对路径，若跟随调用进程 CWD 漂移（例如从 scripts/ 目录启动），
    git 会找不到该路径而静默返回空结果。
    """
    try:
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%cs', '--', *_CODE_PATHSPEC],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def check_updates_log_freshness(grace_days=None):
    """防「代码改了但日志没记」。

    data/site_updates.json 是纯人工维护的——全仓没有任何脚本写它。所以「改了代码
    忘记补记」和「文件被覆盖回滚」都不会被任何检查发现，页面照常生成、日志停在旧
    版本，失败与成功长得一样。check_updates_log_sync() 只做单向校验（已记录的版本
    有没有渲染进页面），管不到这一头。

    这里用 git 历史做交叉校验：最近一次产品代码提交若比日志最新日期新出 grace_days
    以上，说明代码先动了、日志没跟上。
    """
    if grace_days is None:
        grace_days = LOG_LAG_GRACE_DAYS
    failures = []
    code_date = latest_code_commit_date()
    if not code_date:
        return failures
    try:
        with open(data_path('site_updates.json'), encoding='utf-8') as f:
            updates = json.load(f)
    except Exception:  # noqa: BLE001
        # 文件缺失/损坏由 check_updates_log_sync 负责报，这里不重复报
        return failures
    if not updates:
        return failures
    log_date = max((u.get('date') or '') for u in updates)
    if not log_date:
        return failures
    try:
        lag = (datetime.strptime(code_date, '%Y-%m-%d')
               - datetime.strptime(log_date, '%Y-%m-%d')).days
    except ValueError:
        return failures
    if lag > grace_days:
        failures.append(
            f"更新日志断档：最近一次产品代码提交 {code_date} 比日志最新日期 {log_date} "
            f"新 {lag} 天（宽限 {grace_days} 天），请补记 data/site_updates.json"
        )
    return failures


# ---------------------------------------------------------------------------
# 阶段 0 四道不变规则：接产线（quick / full 两条路径之后无条件执行）
# ---------------------------------------------------------------------------

# 规则 2 的「排序轴是否变更」目前没有可采的独立开关，只能由人在改主轴时显式
# 声明。这一项按「查不到自动证据」如实输出，不当作健康。
SORT_AXIS_DECLARATION = {
    'sort_axis_changed': False,
    'criterion_defined': False,
    'backtest_conclusion': False,
    'weights_fixed': False,
    'axis': '程序分（confidence_score / attention_score / signal_change_score）',
}


def _workflow_rule_state(path=None):
    """从 update.yml 采规则 4 的四个静态事实；读不到 collect 段返回 None（查不到）。"""
    path = path or os.path.join(REPO_ROOT, '.github', 'workflows', 'update.yml')
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError:
        return None
    match = re.search(r'(?ms)^  collect:\s*\n(.*?)(?=^  [\w-]+:\s*\n|\Z)', text)
    if not match:
        return None
    collect = match.group(1)
    window = re.search(r'alert_window_days[:=]\s*(\d+)', text)
    return {
        'collect_syncs_latest': 'git fetch origin main' in collect,
        'commit_retry_on_reject': bool(re.search(r'git pull --rebase|git rebase|\bretry\b', collect)),
        'alert_match': ('recent_window' if re.search(r'created_at|window_days|recent_window', text)
                        else 'any_same_title'),
        'alert_window_days': int(window.group(1)) if window else None,
    }


def _capability_state(runs):
    """从 run_metrics.json 的多次运行采规则 1 的输入。

    ai_quality_judge 只有「输入数 / 丢弃数」，没有丢弃原因埋点，无法区分
    「从不判低分」和「每次降级」——按三值规矩记为「查不到」，不硬判。
    """
    values = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        value = (run.get('ai_scoring') or {}).get('scored')
        if isinstance(value, int):
            values.append(value)
    if not values:
        return {}, ['规则 1: 查不到 ai_scoring.scored 埋点']
    latest = (runs[-1].get('ai_scoring') or {}) if isinstance(runs[-1], dict) else {}
    capabilities = {
        'ai_scoring': {
            'enabled': bool(latest.get('enabled', True)),
            'calls': (latest.get('scored') or 0) + (latest.get('failed') or 0),
            'hits': latest.get('scored'),
            'values': values[-30:],
        },
    }
    unknown = [
        '规则 1: ai_quality_judge 查不到「丢弃原因」埋点，无法区分从不判低分与每次降级',
        '规则 3: official_sources（模型榜）的停更天数未纳入本次采集口径',
    ]
    return capabilities, unknown


def run_rule_checks(metrics_path=None, workflow_path=None, plans_dir=None):
    """四道规则接产线：返回 (告警列表, 查不到列表)。

    证据一律从本机现成产物采；采不到写「查不到」，不猜、不静默当健康。
    告警默认只打印（RULE WARNING），不打断线上 --strict；显式加
    --strict-rules 才计入 failures。这样接线的同时，不把已知欠账变成每日红灯。
    """
    issues, unknown = [], []
    runs = _load_json(metrics_path or data_path('run_metrics.json'), [])
    if not isinstance(runs, list) or not runs:
        unknown.append('规则 1/3: 查不到 run_metrics.json 的多次运行记录')
    else:
        capabilities, cap_unknown = _capability_state(runs)
        unknown += cap_unknown
        if capabilities:
            issues += [f'规则1 {x}' for x in check_capability_validity(capabilities)]
        sources = build_source_state(runs)
        if sources:
            issues += [f'规则3 {x}' for x in check_source_decay(sources)]
        else:
            unknown.append('规则 3: 查不到信源状态（rss / html / company 均无 source_stats）')

    plans_dir = plans_dir or os.path.join(REPO_ROOT, 'docs', 'plans')
    criterion = dict(SORT_AXIS_DECLARATION)
    has_criterion_doc = os.path.exists(
        os.path.join(plans_dir, '2026-11-05-重要性判据定义.md')
    )
    criterion['criterion_defined'] = has_criterion_doc
    criterion['backtest_conclusion'] = has_criterion_doc
    issues += [f'规则2 {x}' for x in check_criterion_before_sort_change(criterion)]
    unknown.append('规则 2: 排序轴是否变更靠人工声明（SORT_AXIS_DECLARATION），无自动开关')

    workflow = _workflow_rule_state(workflow_path)
    if workflow is None:
        unknown.append('规则 4: 查不到 update.yml 的 collect job 段')
    else:
        issues += [f'规则4 {x}' for x in check_latest_state_basis(workflow)]
    return issues, unknown


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true', help='Read persisted health facts without rebuilding reports')
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--min-today', type=int, default=1)
    parser.add_argument('--min-company-quality-nonzero', type=int, default=1)
    parser.add_argument('--min-feed-entries', type=int, default=1)
    parser.add_argument('--max-feed-google-ratio', type=float, default=0)
    parser.add_argument('--max-duplicate-ratio', type=float, default=0.35)
    parser.add_argument('--require-run-metrics', action='store_true')
    parser.add_argument('--min-must-coverage', type=float, default=0)
    parser.add_argument('--max-failed-observation-points', type=int, default=999)
    parser.add_argument('--fail-on-jobs-error', action='store_true')
    parser.add_argument('--max-repair-ratio', type=float, default=0.9,
                        help='AI 修复失败率上限（repair_ratio 超过即报警，默认 0.9）')
    parser.add_argument('--strict', action='store_true', help='Exit non-zero when health checks fail')
    parser.add_argument('--strict-rules', action='store_true',
                        help='把阶段 0 四道规则告警也计入 --strict 失败（默认只打印，不阻断线上）')
    args = parser.parse_args()

    if args.quick:
        report = build_quick_health_report()
        print_quick_report(report)
        failures = collect_quick_failures(report, args)
    else:
        report = build_health_report(args.days)
        print_report(report)
        failures = collect_failures(report, args)
    failures += check_updates_log_sync()
    failures += check_updates_log_freshness()
    rule_issues, rule_unknown = run_rule_checks()
    for item in rule_unknown:
        print(f"RULE UNKNOWN: {item}")
    for item in rule_issues:
        print(f"RULE WARNING: {item}")
    if args.strict_rules:
        failures += [f'[阶段0规则] {item}' for item in rule_issues]
    for failure in failures:
        print(f"WARNING: {failure}")
    if args.strict and failures:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
