"""Report the source conversion funnel from run metrics and stored events.

This answers a narrower question than source health: which sources produce raw
items, which produce signals, and why stored events do or do not reach the main
daily surface.
"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from event_contract import prepare_event_contract
    from event_value import (
        event_filter_reason,
        event_type,
        is_google_news_event,
        needs_quality_review,
        should_show_in_main_list,
        should_show_in_review,
    )
    from internet_relevance import internet_relevance_score
    from view_selectors import is_main_view_event, is_review_view_event
except ImportError:
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import (
        event_filter_reason,
        event_type,
        is_google_news_event,
        needs_quality_review,
        should_show_in_main_list,
        should_show_in_review,
    )
    from scripts.internet_relevance import internet_relevance_score
    from scripts.view_selectors import is_main_view_event, is_review_view_event


DROP_REASONS = (
    'main',
    'review',
    'out_of_scope_industry',
    'capital_only_low_actionability',
    'quality_review',
    'google_not_main',
    'other_type',
    'weak_signal',
    'scope_no_target_industry',
    'scope_change_not_explicit',
    'scope_editorial_without_explicit_change',
)


def _safe_print(text):
    print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))


def _load_json(path, git_ref=None):
    # 约定：path 为「仓库相对路径」或「绝对路径」。
    #   - git_ref 分支：必须是仓库相对路径（git show <ref>:<path> 语义），
    #     因此测试注入绝对路径时不要同时传 git_ref。
    #   - 本地分支：相对路径锚定仓库根，不随 CWD 漂移。
    if git_ref:
        raw = subprocess.check_output(['git', 'show', f'{git_ref}:{path}'])
        return json.loads(raw.decode('utf-8'))
    if not os.path.isabs(path):
        path = os.path.join(_REPO_ROOT, path)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _flatten_events(data):
    if isinstance(data, list):
        return data
    events = []
    for date_key, items in (data or {}).items():
        for event in items or []:
            if not event.get('date'):
                event = dict(event)
                event['date'] = date_key
            events.append(event)
    return events


def _event_date(event):
    return (event.get('date') or '')[:10]


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _period_dates(end_date, days):
    end = _parse_date(end_date)
    if not end:
        return set()
    return {
        (end - timedelta(days=offset)).strftime('%Y-%m-%d')
        for offset in range(days)
    }


def _run_date(run):
    return (run.get('date') or run.get('started_at') or '')[:10]


def _registry_aliases(registry):
    aliases = {}
    for source in registry.get('sources') or []:
        name = source.get('name') or source.get('id') or ''
        if not name:
            continue
        aliases[name] = name
        if source.get('id'):
            aliases[source['id']] = name
    return aliases


def _source_names(event, aliases=None):
    aliases = aliases or {}
    origin_source_id = event.get('origin_source_id') or event.get('observation_entity_id')
    if origin_source_id:
        return [aliases.get(origin_source_id, origin_source_id)]
    source_id = event.get('source_id')
    if source_id and source_id in aliases:
        return [aliases[source_id]]
    if source_id and event.get('is_company') and source_id.lower() not in {'google-news', 'google news'}:
        return [source_id]
    source = event.get('source')
    if source and source in aliases:
        return [aliases[source]]
    if is_google_news_event(event):
        return ['Google News']
    for key in ('display_source', 'source_detail', 'source_id', 'source'):
        value = event.get(key)
        value = aliases.get(value, value)
        if value:
            return [value]
    return ['未知来源']


def classify_filter_reason(event):
    """Return the first product reason explaining stored-event visibility."""
    if is_main_view_event(event):
        return 'main'
    if event_filter_reason(event) == 'capital_only_low_actionability':
        return 'capital_only_low_actionability'
    if is_review_view_event(event):
        return 'review'
    return event_filter_reason(event)


def _empty_row(source):
    row = {
        'source': source,
        'raw': 0,
        'signal': 0,
        'stored': 0,
        'lost_after_signal': 0,
        'signal_rate': 0,
        'stored_per_signal': 0,
        'main_per_signal': 0,
        'main_per_stored': 0,
        'methods': Counter(),
        'statuses': Counter(),
        'dates': set(),
        'reason_counts': Counter(),
        'funnel': Counter(),
    }
    for reason in DROP_REASONS:
        row[reason] = 0
    return row


def _add_run_stats(rows, metrics, selected_dates):
    records = metrics if isinstance(metrics, list) else ([metrics] if metrics else [])
    selected_runs = []
    for run in records:
        date_key = _run_date(run)
        if selected_dates and date_key not in selected_dates:
            continue
        selected_runs.append(run)
        for group in ('rss', 'html', 'company'):
            source_stats = (run.get(group) or {}).get('source_stats') or {}
            for name, stats in source_stats.items():
                row = rows[name]
                row['source'] = name
                row['raw'] += stats.get('count') or 0
                row['signal'] += stats.get('signal_count') or 0
                row['methods'][stats.get('method') or group] += 1
                row['statuses'][stats.get('status') or 'unknown'] += 1
                if date_key:
                    row['dates'].add(date_key)
        for name, funnel in (run.get('source_funnel') or {}).items():
            row = rows[name]
            row['source'] = name
            row['funnel'].update(funnel or {})
    return selected_runs


def _add_event_stats(rows, events, selected_dates, aliases, prepared=False):
    selected_events = []
    for event in events:
        if not prepared:
            event = prepare_event_contract(dict(event))
        date_key = _event_date(event)
        if date_key not in selected_dates:
            continue
        selected_events.append(event)
        reason = classify_filter_reason(event)
        for name in _source_names(event, aliases):
            row = rows[name]
            row['source'] = name
            row['stored'] += 1
            row[reason] += 1
            row['reason_counts'][reason] += 1
            if date_key:
                row['dates'].add(date_key)
    return selected_events


def _finish_row(row):
    raw = row['raw']
    signal = row['signal']
    stored = row['stored']
    row['lost_after_signal'] = max(signal - stored, 0)
    row['signal_rate'] = signal / raw if raw else 0
    row['stored_per_signal'] = stored / signal if signal else 0
    row['main_per_signal'] = row['main'] / signal if signal else 0
    row['main_per_stored'] = row['main'] / stored if stored else 0
    row['methods_text'] = ','.join(name for name, _ in row['methods'].most_common())
    row['statuses_text'] = ','.join(f'{name}:{count}' for name, count in row['statuses'].most_common())
    return row


def _governance_action(row):
    signal = row['signal']
    stored = row['stored']
    main = row['main']
    raw = row['raw']
    lost = row['lost_after_signal']

    if row['source'] == 'Google News':
        return 'keep_as_gap_fill'
    if signal >= 50 and main == 0:
        return 'audit_raw_signal_quality'
    if signal >= 20 and stored == 0:
        return 'instrument_candidate_loss'
    if stored >= 20 and row['main_per_stored'] >= 0.4:
        return 'promote_weight'
    if main >= 10 or (main >= 5 and row['main_per_stored'] >= 0.35):
        return 'keep_core'
    if stored >= 5 and main == 0:
        return 'downgrade_or_reclassify'
    if raw == 0 and stored == 0:
        return 'pause_or_low_frequency'
    if lost >= 20 and row['stored_per_signal'] < 0.15:
        return 'audit_conversion_loss'
    return 'observe'


def _governance_reason(row):
    action = row.get('governance_action') or ''
    if action == 'keep_as_gap_fill':
        return 'Google News 只承担补漏，不作为主发现源'
    if action == 'audit_raw_signal_quality':
        return 'raw signal 很高但没有主展示贡献，先确认 signal 是否真是合格事件'
    if action == 'instrument_candidate_loss':
        return 'raw signal 不少但没有进入历史事件池，需补候选流失原因'
    if action == 'promote_weight':
        return '历史留存和主展示转化稳定'
    if action == 'keep_core':
        return '近期已有稳定主展示贡献'
    if action == 'downgrade_or_reclassify':
        return '有留存但无法进入主展示，需检查边界或源定位'
    if action == 'pause_or_low_frequency':
        return '近期没有抓取和留存贡献'
    if action == 'audit_conversion_loss':
        return 'signal 到 stored/main 的流失过高'
    return '继续观察，暂不调整'


def _build_governance_actions(rows, limit_per_action=8):
    actions = defaultdict(list)
    for row in rows:
        action = row.get('governance_action') or 'observe'
        if action == 'observe':
            continue
        actions[action].append(row)
    result = {}
    for action, items in actions.items():
        items.sort(
            key=lambda row: (
                row['signal'],
                row['lost_after_signal'],
                row['stored'],
                row['main'],
                row['raw'],
            ),
            reverse=True,
        )
        result[action] = [
            {
                'source': row['source'],
                'raw': row['raw'],
                'signal': row['signal'],
                'stored': row['stored'],
                'main': row['main'],
                'lost_after_signal': row['lost_after_signal'],
                'reason': row.get('governance_reason') or '',
            }
            for row in items[:limit_per_action]
        ]
    return result


def build_source_conversion_report(days=7, git_ref=None, events=None,
                                   metrics_path=None, registry_path=None):
    if events is None:
        events_data = _load_json('data/events.json', git_ref)
        events = _flatten_events(events_data)
        prepared = False
    else:
        events = _flatten_events(events)
        prepared = True
    metrics = _load_json(metrics_path or 'data/run_metrics.json', git_ref)
    registry = _load_json(registry_path or 'data/source_registry.json', git_ref)

    event_dates = sorted({_event_date(event) for event in events if _event_date(event)})
    run_dates = sorted({_run_date(run) for run in (metrics if isinstance(metrics, list) else []) if _run_date(run)})
    end_date = (event_dates or run_dates or [''])[-1]
    selected_dates = _period_dates(end_date, days)
    aliases = _registry_aliases(registry)
    rows = defaultdict(lambda: _empty_row(''))

    selected_runs = _add_run_stats(rows, metrics, selected_dates)
    selected_events = _add_event_stats(rows, events, selected_dates, aliases, prepared=prepared)
    for source in registry.get('sources') or []:
        name = source.get('name') or source.get('id') or ''
        if name and name not in rows:
            rows[name] = _empty_row(name)

    finished_rows = [_finish_row(row) for row in rows.values()]
    for row in finished_rows:
        row['governance_action'] = _governance_action(row)
        row['governance_reason'] = _governance_reason(row)
    finished_rows.sort(
        key=lambda row: (
            row['signal'] > 0 and row['main'] == 0,
            row['lost_after_signal'],
            row['signal'],
            row['raw'],
        ),
        reverse=True,
    )

    totals = _empty_row('TOTAL')
    for row in finished_rows:
        for key in ('raw', 'signal', 'stored', 'lost_after_signal', *DROP_REASONS):
            totals[key] += row[key]
    _finish_row(totals)
    scope_totals = Counter()
    for row in finished_rows:
        scope_totals['qualified'] += row['funnel'].get('scope_qualified', 0)
        scope_totals['candidate'] += row['funnel'].get('scope_candidate', 0)
        scope_totals['filtered'] += row['funnel'].get('scope_filtered', 0)

    high_signal_low_main = [
        row for row in finished_rows
        if row['signal'] >= 5 and row['main'] <= 1
    ]
    governance_counts = Counter(row['governance_action'] for row in finished_rows)
    governance_actions = _build_governance_actions(finished_rows)

    return {
        'end_date': end_date,
        'days': days,
        'git_ref': git_ref or '',
        'run_count': len(selected_runs),
        'event_count': len(selected_events),
        'totals': totals,
        'scope_totals': dict(scope_totals),
        'rows': finished_rows,
        'high_signal_low_main': high_signal_low_main,
        'governance_counts': dict(governance_counts),
        'governance_actions': governance_actions,
    }


def _jsonable_row(row):
    result = dict(row)
    result['methods'] = dict(row.get('methods') or {})
    result['statuses'] = dict(row.get('statuses') or {})
    result['dates'] = sorted(row.get('dates') or [])
    result['reason_counts'] = dict(row.get('reason_counts') or {})
    result['funnel'] = dict(row.get('funnel') or {})
    return result


def print_report(report, limit=30):
    totals = report['totals']
    _safe_print(
        f"source conversion | ref={report['git_ref'] or 'working-tree'} "
        f"end_date={report['end_date']} days={report['days']} "
        f"runs={report['run_count']} stored_events={report['event_count']}"
    )
    _safe_print(
        "totals | raw={raw} signal={signal} stored={stored} main={main} review={review} "
        "out_of_scope_industry={out_of_scope_industry} "
        "capital_only_low_actionability={capital_only_low_actionability} "
        "quality_review={quality_review} "
        "google_not_main={google_not_main} other_type={other_type} "
        "weak_signal={weak_signal} lost_after_signal≈{lost_after_signal}".format(**totals)
    )
    scope = report.get('scope_totals') or {}
    if any(scope.values()):
        _safe_print(
            f"scope | qualified={scope.get('qualified', 0)} "
            f"candidate={scope.get('candidate', 0)} filtered={scope.get('filtered', 0)}"
        )
    _safe_print(
        "source | raw | signal | stored | main | review | out_of_scope_industry | "
        "capital_only_low_actionability | quality_review | "
        "google_not_main | other_type | weak_signal | lost_after_signal≈ | signal_rate | main_per_signal | governance | funnel | status"
    )
    for row in report['rows'][:limit]:
        funnel = row.get('funnel') or {}
        funnel_text = ''
        if funnel:
            funnel_text = (
                f"scope:{funnel.get('scope_qualified', 0)}/"
                f"{funnel.get('scope_candidate', 0)}/"
                f"{funnel.get('scope_filtered', 0)},"
                f"smart:{funnel.get('smart_kept', 0)},"
                f"ai:{funnel.get('score_ai_tier', 0)},"
                f"program:{funnel.get('score_program_tier', 0)},"
                f"added:{funnel.get('added', 0)}"
            )
        _safe_print(
            "{source} | {raw} | {signal} | {stored} | {main} | {review} | "
            "{out_of_scope_industry} | {capital_only_low_actionability} | "
            "{quality_review} | {google_not_main} | {other_type} | "
            "{weak_signal} | {lost_after_signal} | {signal_rate:.0%} | "
            "{main_per_signal:.0%} | {governance_action} | ".format(**row)
            + f"{funnel_text} | {row['statuses_text']}"
        )
    if report.get('governance_counts'):
        _safe_print(f"governance | {json.dumps(report['governance_counts'], ensure_ascii=False)}")
    if report.get('governance_actions'):
        _safe_print("governance actions | action | source | signal | stored | main | reason")
        for action, items in report['governance_actions'].items():
            for row in items[:5]:
                _safe_print(
                    f"{action} | {row['source']} | {row['signal']} | "
                    f"{row['stored']} | {row['main']} | {row['reason']}"
                )
    if report['high_signal_low_main']:
        _safe_print("high signal low/no main | source | signal | stored | main | primary_loss")
        for row in report['high_signal_low_main'][:10]:
            losses = Counter({
                reason: row[reason]
                for reason in DROP_REASONS
                if reason not in {'main'} and row[reason]
            })
            primary_loss = losses.most_common(1)[0][0] if losses else 'lost_after_signal'
            _safe_print(f"{row['source']} | {row['signal']} | {row['stored']} | {row['main']} | {primary_loss}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--git-ref', help='Read data files from a git ref, e.g. origin/main')
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--json-out', help='Optional path to write JSON report')
    args = parser.parse_args()

    report = build_source_conversion_report(days=args.days, git_ref=args.git_ref)
    print_report(report, args.limit)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            **report,
            'totals': _jsonable_row(report['totals']),
            'rows': [_jsonable_row(row) for row in report['rows']],
            'high_signal_low_main': [_jsonable_row(row) for row in report['high_signal_low_main']],
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
