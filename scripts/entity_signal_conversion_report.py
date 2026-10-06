"""Report entity-pool coverage and current event conversion.

The first version is intentionally observational: it does not crawl new
observation points yet. It shows which entities already receive stored events
and which observation points still lack instrumentation.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    from event_contract import prepare_event_contract
    from event_value import should_show_in_main_list, should_show_in_review
    from repo_paths import data_path
except ImportError:
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import should_show_in_main_list, should_show_in_review
    from scripts.repo_paths import data_path


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


def _norm(value):
    return re.sub(r'[^a-z0-9]+', '', (value or '').lower())


def _event_company_tokens(event):
    values = []
    for key in ('company_name', 'company'):
        if event.get(key):
            values.append(event[key])
    values.extend(event.get('companies') or [])
    return {_norm(value) for value in values if _norm(value)}


def _event_text(event):
    return ' '.join([
        event.get('title') or '',
        event.get('display_title') or '',
        event.get('summary_short') or '',
        event.get('reason') or '',
    ]).lower()


def _alias_in_text(alias, text):
    alias = (alias or '').strip().lower()
    if not alias:
        return False
    if re.search(r'[\u4e00-\u9fff]', alias):
        return alias in text
    alias_tokens = re.findall(r'[a-z0-9]+', alias)
    text_tokens = ' '.join(re.findall(r'[a-z0-9]+', text))
    if not alias_tokens:
        return False
    phrase = re.escape(' '.join(alias_tokens))
    return bool(re.search(rf'(?<![a-z0-9]){phrase}(?![a-z0-9])', text_tokens))


def _entity_alias_tokens(entity):
    aliases = [entity.get('name') or '']
    aliases.extend(entity.get('aliases') or [])
    return {_norm(alias) for alias in aliases if _norm(alias)}


def event_matches_entity(event, entity):
    aliases = _entity_alias_tokens(entity)
    if not aliases:
        return False
    company_tokens = _event_company_tokens(event)
    if aliases & company_tokens:
        return True
    text = _event_text(event)
    names = [entity.get('name') or ''] + list(entity.get('aliases') or [])
    return any(_alias_in_text(name, text) for name in names)


def _point_counts(entity):
    counts = {
        'observation_points': 0,
        'active_points': 0,
        'candidate_points': 0,
        'not_instrumented_points': 0,
    }
    for point in entity.get('observation_points') or []:
        counts['observation_points'] += 1
        status = point.get('status') or 'candidate'
        if status == 'active':
            counts['active_points'] += 1
        if status == 'candidate':
            counts['candidate_points'] += 1
        if not point.get('instrumented'):
            counts['not_instrumented_points'] += 1
    return counts


def _instrumentation_status(point_counts):
    total = point_counts['observation_points']
    not_instrumented = point_counts['not_instrumented_points']
    if total and not_instrumented == 0:
        return 'instrumented'
    if total and not_instrumented < total:
        return 'partial'
    return 'not_instrumented'


def _point_type_counts(entity):
    counts = defaultdict(lambda: {
        'points': 0,
        'active': 0,
        'candidate': 0,
        'instrumented': 0,
        'entities': set(),
    })
    entity_name = entity.get('name') or ''
    for point in entity.get('observation_points') or []:
        point_type = point.get('type') or 'unknown'
        row = counts[point_type]
        row['points'] += 1
        row['entities'].add(entity_name)
        if point.get('status') == 'active':
            row['active'] += 1
        if point.get('status') == 'candidate':
            row['candidate'] += 1
        if point.get('instrumented'):
            row['instrumented'] += 1
    return counts


def _recommended_action(row):
    if row.get('instrumented_points') == 0 and row.get('points', 1) > 1:
        return 'instrument_conversion'
    if row['homepage_events'] >= 3:
        return 'scale'
    if row['stored_events'] >= 3 and row['homepage_events'] == 0:
        return 'audit_quality'
    if row['active_points'] and row['stored_events'] == 0:
        return 'fix_or_lower_frequency'
    if row['candidate_points'] and not row['active_points']:
        return 'instrument_next'
    return 'observe'


def build_entity_signal_conversion_report(days=30, pool_path=None, events_path=None):
    pool_path = pool_path or data_path('entity_pool.json')
    events_path = events_path or data_path('events.json')
    pool = _load_json(pool_path)
    events = _flatten_events(_load_json(events_path))
    dates = sorted({_event_date(event) for event in events if _event_date(event)})
    end_date = dates[-1] if dates else ''
    selected_dates = _period_dates(end_date, days)
    selected_events = [
        prepare_event_contract(event) for event in events
        if _event_date(event) in selected_dates
    ]

    rows = []
    point_type_rows = defaultdict(lambda: {
        'point_type': '',
        'points': 0,
        'active_points': 0,
        'candidate_points': 0,
        'instrumented_points': 0,
        'entities': set(),
        'stored_events': 0,
        'homepage_events': 0,
        'review_events': 0,
    })
    for entity in pool.get('entities') or []:
        matched = [event for event in selected_events if event_matches_entity(event, entity)]
        main = [event for event in matched if should_show_in_main_list(event)]
        review = [event for event in matched if should_show_in_review(event)]
        point_counts = _point_counts(entity)
        row = {
            'entity': entity.get('name') or '',
            'region': entity.get('region') or '',
            'sector': entity.get('sector') or '',
            'priority': entity.get('priority') or '',
            **point_counts,
            'stored_events': len(matched),
            'homepage_events': len(main),
            'review_events': len(review),
            'last_event_date': max((_event_date(event) for event in matched), default=''),
            'instrumentation_status': _instrumentation_status(point_counts),
        }
        row['governance_action'] = _recommended_action(row)
        rows.append(row)

        for point_type, point_row in _point_type_counts(entity).items():
            target = point_type_rows[point_type]
            target['point_type'] = point_type
            target['points'] += point_row['points']
            target['active_points'] += point_row['active']
            target['candidate_points'] += point_row['candidate']
            target['instrumented_points'] += point_row['instrumented']
            target['entities'].update(point_row['entities'])
            target['stored_events'] += len(matched)
            target['homepage_events'] += len(main)
            target['review_events'] += len(review)

    rows.sort(
        key=lambda row: (
            row['homepage_events'],
            row['stored_events'],
            row['active_points'],
            row['observation_points'],
        ),
        reverse=True,
    )
    totals = {
        'entities': len(rows),
        'observation_points': sum(row['observation_points'] for row in rows),
        'active_points': sum(row['active_points'] for row in rows),
        'candidate_points': sum(row['candidate_points'] for row in rows),
        'not_instrumented_points': sum(row['not_instrumented_points'] for row in rows),
        'stored_events': sum(row['stored_events'] for row in rows),
        'homepage_events': sum(row['homepage_events'] for row in rows),
        'review_events': sum(row['review_events'] for row in rows),
    }
    point_rows = []
    for row in point_type_rows.values():
        converted = dict(row)
        converted['entities'] = len(row['entities'])
        converted['homepage_per_entity'] = (
            converted['homepage_events'] / converted['entities']
            if converted['entities'] else 0
        )
        converted['governance_action'] = _recommended_action({
            'points': converted['points'],
            'instrumented_points': converted['instrumented_points'],
            'homepage_events': converted['homepage_events'],
            'stored_events': converted['stored_events'],
            'active_points': converted['active_points'],
            'candidate_points': converted['candidate_points'],
        })
        point_rows.append(converted)
    point_rows.sort(
        key=lambda row: (
            row['homepage_events'],
            row['stored_events'],
            row['active_points'],
            row['points'],
        ),
        reverse=True,
    )
    return {
        'end_date': end_date,
        'days': days,
        'pool_version': pool.get('version') or '',
        'totals': totals,
        'rows': rows,
        'point_rows': point_rows,
        'governance_counts': dict(Counter(row['governance_action'] for row in rows)),
    }


def print_report(report, limit=50):
    totals = report['totals']
    _safe_print(
        f"entity signal conversion | pool_version={report['pool_version']} "
        f"end_date={report['end_date']} days={report['days']}"
    )
    _safe_print(
        "totals | entities={entities} points={observation_points} active={active_points} "
        "candidate={candidate_points} not_instrumented={not_instrumented_points} "
        "stored={stored_events} homepage={homepage_events} review={review_events}".format(**totals)
    )
    _safe_print("entity | region | sector | points | active | candidate | stored | homepage | review | last_event | action | status")
    for row in report['rows'][:limit]:
        _safe_print(
            "{entity} | {region} | {sector} | {observation_points} | {active_points} | "
            "{candidate_points} | {stored_events} | {homepage_events} | {review_events} | "
            "{last_event_date} | {governance_action} | {instrumentation_status}".format(**row)
        )
    if report.get('point_rows'):
        _safe_print("observation point governance | type | points | active | candidate | entities | stored | homepage | review | action")
        for row in report['point_rows']:
            _safe_print(
                "{point_type} | {points} | {active_points} | {candidate_points} | "
                "{entities} | {stored_events} | {homepage_events} | {review_events} | "
                "{governance_action}".format(**row)
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--pool-path')
    parser.add_argument('--events-path')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--json-out', help='Optional path to write JSON report')
    args = parser.parse_args()

    report = build_entity_signal_conversion_report(args.days, args.pool_path, args.events_path)
    print_report(report, args.limit)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
