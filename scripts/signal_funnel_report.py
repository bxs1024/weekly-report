"""Report the signal funnel per content type.

Shows, for each content type, how many stored events are usable signals and
where they end up (main / review / dropped), plus their trend contribution.
Everything is measured on the stored event pool through the unified
prepare_event_contract entry, so the numbers are comparable to the daily and
weekly surfaces. The fetch-layer raw counts live in run_metrics per source and
cannot be split by content type, so they are summarized as a pipeline header.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    from event_contract import prepare_event_contract
    from event_value import should_show_in_main_list, should_show_in_review
    from repo_paths import data_path
    from scope_gate import is_scope_qualified
    from signal_scoring import CONTENT_TYPES, infer_content_type
    from source_conversion_report import classify_filter_reason
except ImportError:
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import should_show_in_main_list, should_show_in_review
    from scripts.repo_paths import data_path
    from scripts.scope_gate import is_scope_qualified
    from scripts.signal_scoring import CONTENT_TYPES, infer_content_type
    from scripts.source_conversion_report import classify_filter_reason


CONTENT_TYPE_ORDER = [
    'industry_report', 'model_release', 'regional_policy',
    'company_action', 'capital_event', 'generic_industry_change',
]


def _safe_print(text):
    print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _flatten_events(data):
    if isinstance(data, list):
        return data
    events = []
    for date_key, items in (data or {}).items():
        for event in items or []:
            item = dict(event)
            item.setdefault('date', date_key)
            events.append(item)
    return events


def _event_date(event):
    return (event.get('date') or '')[:10]


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _run_date(run):
    return (run.get('date') or run.get('started_at') or '')[:10]


def _period_dates(end_date, days):
    end = _parse_date(end_date)
    if not end:
        return set()
    return {
        (end - timedelta(days=offset)).strftime('%Y-%m-%d')
        for offset in range(days)
    }


def _content_type(event):
    return event.get('content_type') or infer_content_type(event) or 'unknown'


def _event_types_first(event):
    return (event.get('event_types') or ['other'])[0]


def _is_signal(event):
    """Product-口径 signal: scope 准入（legacy 事件默认合格）且非 other 类型。

    与 fetch_news._qualified_signal_count 的 scope_status 判定保持一致，
    legacy 事件经 is_scope_qualified 回退为合格，反映真实产品展示口径。
    """
    return is_scope_qualified(event) and _event_types_first(event) != 'other'


def _is_scope_explicit_signal(event):
    """Strict 口径 signal: 只有被 scope 契约显式重评的事件才计入。

    用于暴露历史事件未重评的差距（scope_status 缺失 = 产品口径合格，
    但严格口径不算信号）。
    """
    return event.get('scope_status') == 'qualified' and _event_types_first(event) != 'other'


def _pipeline_header(metrics, selected_dates):
    """取窗口内最近一次运行的 source_funnel 作为真实抓取层快照。

    逐次运行求和会重复计数同一批源，快照更干净，也能与下方按类型的
    pool→signal→main/review 漏斗对齐。
    """
    records = metrics if isinstance(metrics, list) else ([metrics] if metrics else [])
    latest = None
    for run in records:
        if selected_dates and _run_date(run) not in selected_dates:
            continue
        if latest is None or (_run_date(run) or '') >= (_run_date(latest) or ''):
            latest = run
    if latest is None:
        return 0, {}, ''
    funnel = {}
    for values in (latest.get('source_funnel') or {}).values():
        for key, value in (values or {}).items():
            if key in {'score_ai_tier', 'score_program_tier'}:
                key = 'tier_kept'
            funnel[key] = funnel.get(key, 0) + (value or 0)
    return 1, funnel, _run_date(latest)


def build_signal_funnel_report(
    days=15,
    events_path=None,
    metrics_path=None,
):
    events_path = events_path or data_path('events.json')
    metrics_path = metrics_path or data_path('run_metrics.json')
    events = [prepare_event_contract(event) for event in _flatten_events(_load_json(events_path))]
    metrics = _load_json(metrics_path)
    dates = sorted({_event_date(event) for event in events if _event_date(event)})
    end_date = dates[-1] if dates else ''
    selected_dates = _period_dates(end_date, days)
    window_events = [event for event in events if _event_date(event) in selected_dates]

    rows = defaultdict(lambda: {
        'pool': 0, 'signal': 0, 'scope_explicit': 0, 'main': 0, 'review': 0,
        'drop_reasons': Counter(),
    })
    for event in window_events:
        row = rows[_content_type(event)]
        row['pool'] += 1
        if _is_signal(event):
            row['signal'] += 1
        if _is_scope_explicit_signal(event):
            row['scope_explicit'] += 1
        if should_show_in_main_list(event):
            row['main'] += 1
        elif should_show_in_review(event):
            row['review'] += 1
        else:
            row['drop_reasons'][classify_filter_reason(event)] += 1

    runs, pipeline, pipeline_date = _pipeline_header(metrics, selected_dates)

    ordered = [t for t in CONTENT_TYPE_ORDER if rows[t]['pool']]
    for t in sorted(rows, key=lambda k: -rows[k]['pool']):
        if t not in ordered:
            ordered.append(t)

    finished = []
    for t in ordered:
        row = rows[t]
        pool = row['pool']
        finished.append({
            'content_type': t,
            'pool': pool,
            'signal': row['signal'],
            'scope_explicit': row['scope_explicit'],
            'main': row['main'],
            'review': row['review'],
            'drop': pool - row['main'] - row['review'],
            'signal_rate': row['signal'] / pool if pool else 0,
            'main_rate': row['main'] / pool if pool else 0,
            'top_drop_reason': row['drop_reasons'].most_common(1)[0][0] if row['drop_reasons'] else '',
            'drop_reasons': dict(row['drop_reasons']),
        })

    totals = {
        key: sum(row[key] for row in finished)
        for key in ('pool', 'signal', 'scope_explicit', 'main', 'review', 'drop')
    }
    totals['main_rate'] = totals['main'] / totals['pool'] if totals['pool'] else 0

    return {
        'end_date': end_date,
        'days': days,
        'window_events': len(window_events),
        'pipeline': dict(pipeline),
        'pipeline_runs': runs,
        'pipeline_date': pipeline_date,
        'totals': totals,
        'rows': finished,
    }


def print_report(report):
    pipeline = report['pipeline']
    totals = report['totals']
    _safe_print(
        f"signal funnel | end_date={report['end_date']} days={report['days']} "
        f"window_events={report['window_events']}"
    )
    _safe_print(
        "pipeline(latest run {date}) | "
        "raw={raw} unique={unique} scope_qualified={scope_qualified} "
        "smart_kept={smart_kept} tier_kept={tier_kept} "
        "analysis_events={analysis_events} added={added}".format(
            date=report['pipeline_date'],
            raw=pipeline.get('raw', 0), unique=pipeline.get('unique', 0),
            scope_qualified=pipeline.get('scope_qualified', 0),
            smart_kept=pipeline.get('smart_kept', 0),
            tier_kept=pipeline.get('tier_kept', 0),
            analysis_events=pipeline.get('analysis_events', 0),
            added=pipeline.get('added', 0),
        )
    )
    _safe_print(
        "content_type | pool | signal | scope_explicit | main | review | drop | "
        "signal_rate | main_rate | top_drop"
    )
    for row in report['rows']:
        _safe_print(
            "{content_type} | {pool} | {signal} | {scope_explicit} | {main} | "
            "{review} | {drop} | {signal_rate:.0%} | {main_rate:.0%} | "
            "{top_drop_reason}".format(**row)
        )
    _safe_print(
        "totals | pool={pool} signal={signal} scope_explicit={scope_explicit} "
        "main={main} review={review} drop={drop} main_rate={main_rate:.0%}".format(**totals)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=15)
    parser.add_argument('--events-path')
    parser.add_argument('--metrics-path')
    parser.add_argument('--json-out', help='Optional path to write JSON report')
    args = parser.parse_args()

    report = build_signal_funnel_report(args.days, args.events_path, args.metrics_path)
    print_report(report)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
