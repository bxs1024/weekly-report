"""Report daily coverage health from stored events.

This answers whether a day is useful as an intelligence navigation surface,
not just whether it has many event cards.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

try:
    from event_contract import prepare_event_contract
    from event_value import (
        classify_bd_priority,
        event_type,
        is_google_news_event,
        should_show_in_main_list,
        should_show_in_review,
    )
    from view_selectors import is_main_view_event, is_review_view_event
    from repo_paths import data_path
except ImportError:
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import (
        classify_bd_priority,
        event_type,
        is_google_news_event,
        should_show_in_main_list,
        should_show_in_review,
    )
    from scripts.repo_paths import data_path
    from scripts.view_selectors import is_main_view_event, is_review_view_event


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


def _period_dates(end_date, days):
    end = _parse_date(end_date)
    if not end:
        return []
    return [
        (end - timedelta(days=offset)).strftime('%Y-%m-%d')
        for offset in range(days)
    ]


def _event_entity(event):
    if event.get('company_name'):
        return event['company_name']
    companies = event.get('companies') or []
    if companies:
        return companies[0]
    return ''


def _event_track(event):
    trend = event.get('trend_topic') or ''
    if trend:
        return trend.split('—')[0].strip()
    label = event.get('insight_label') or ''
    if label:
        return label
    return event_type(event)


def _daily_group_counts(events):
    counts = Counter()
    for event in events:
        priority = classify_bd_priority(event)
        if priority == '高':
            counts['selected'] += 1
        elif priority == '中':
            counts['important'] += 1
        else:
            counts['watch'] += 1
    return counts


def _coverage_status(row):
    if row['main_events'] >= 8 and row['entities'] >= 5 and row['regions'] >= 3:
        return 'healthy'
    if row['stored_events'] >= 8 and row['entities'] >= 3 and row['regions'] >= 2:
        return 'thin_but_usable'
    if row['stored_events'] >= 3:
        return 'thin'
    return 'sparse'


def _coverage_action(row):
    if row['status'] == 'healthy':
        return 'normal'
    if row['google_ratio'] >= 0.4:
        return 'check_source_mix'
    if row['company_ratio'] >= 0.45 and row['main_events'] <= 2:
        return 'check_entity_observation_points'
    if row['entities'] <= 2:
        return 'expand_object_coverage'
    if row['main_events'] <= 2:
        return 'check_main_conversion'
    return 'observe'


def build_daily_coverage_report(days=15, events_path=None, events=None):
    if events is None:
        events_path = events_path or data_path('events.json')
        events = [prepare_event_contract(event) for event in _flatten_events(_load_json(events_path))]
    else:
        events = _flatten_events(events)
    dates = sorted({_event_date(event) for event in events if _event_date(event)})
    end_date = dates[-1] if dates else ''
    selected_dates = _period_dates(end_date, days)
    rows = []
    for date_key in selected_dates:
        day_events = [event for event in events if _event_date(event) == date_key]
        main_events = [event for event in day_events if is_main_view_event(event)]
        review_events = [
            event for event in day_events
            if is_review_view_event(event) and not is_main_view_event(event)
        ]
        entities = {_event_entity(event) for event in day_events if _event_entity(event)}
        regions = {event.get('region') for event in day_events if event.get('region')}
        tracks = {_event_track(event) for event in day_events if _event_track(event)}
        group_counts = _daily_group_counts(day_events)
        source_counts = Counter(event.get('source') or '未知来源' for event in day_events)
        row = {
            'date': date_key,
            'stored_events': len(day_events),
            'main_events': len(main_events),
            'review_events': len(review_events),
            'entities': len(entities),
            'regions': len(regions),
            'tracks': len(tracks),
            'google_events': sum(1 for event in day_events if is_google_news_event(event)),
            'company_events': sum(1 for event in day_events if event.get('is_company')),
            'selected': group_counts['selected'],
            'important': group_counts['important'],
            'watch': group_counts['watch'],
            'top_source': source_counts.most_common(1)[0][0] if source_counts else '',
        }
        row['google_ratio'] = row['google_events'] / row['stored_events'] if row['stored_events'] else 0
        row['company_ratio'] = row['company_events'] / row['stored_events'] if row['stored_events'] else 0
        row['main_ratio'] = row['main_events'] / row['stored_events'] if row['stored_events'] else 0
        row['status'] = _coverage_status(row)
        row['action'] = _coverage_action(row)
        rows.append(row)
    rows.sort(key=lambda row: row['date'], reverse=True)
    totals = {
        'days': len(rows),
        'stored_events': sum(row['stored_events'] for row in rows),
        'main_events': sum(row['main_events'] for row in rows),
        'review_events': sum(row['review_events'] for row in rows),
    }
    totals['status_counts'] = dict(Counter(row['status'] for row in rows))
    totals['action_counts'] = dict(Counter(row['action'] for row in rows))
    return {
        'end_date': end_date,
        'days': days,
        'totals': totals,
        'rows': rows,
    }


def print_report(report, limit=30):
    totals = report['totals']
    _safe_print(
        f"daily coverage | end_date={report['end_date']} days={report['days']} "
        f"stored={totals['stored_events']} main={totals['main_events']} review={totals['review_events']}"
    )
    _safe_print(f"coverage status | {json.dumps(totals['status_counts'], ensure_ascii=False)}")
    _safe_print(f"coverage actions | {json.dumps(totals['action_counts'], ensure_ascii=False)}")
    _safe_print(
        "date | stored | main | review | entities | regions | tracks | google | company | "
        "selected | important | watch | status | action | top_source"
    )
    for row in report['rows'][:limit]:
        _safe_print(
            "{date} | {stored_events} | {main_events} | {review_events} | {entities} | "
            "{regions} | {tracks} | {google_events} | {company_events} | {selected} | "
            "{important} | {watch} | {status} | {action} | {top_source}".format(**row)
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=15)
    parser.add_argument('--events-path')
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--json-out', help='Optional path to write JSON report')
    args = parser.parse_args()
    report = build_daily_coverage_report(args.days, args.events_path)
    print_report(report, args.limit)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
