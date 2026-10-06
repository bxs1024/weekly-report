"""Build an auditable observation ledger for monitored entities."""

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# 仓库路径锚定仓库根（见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
try:
    from repo_paths import data_path
except ImportError:
    from scripts.repo_paths import data_path

try:
    from entity_signal_conversion_report import event_matches_entity
    from event_dates import is_display_date
    from event_contract import prepare_event_contract
    from event_value import should_show_in_main_list
except ImportError:
    from scripts.entity_signal_conversion_report import event_matches_entity
    from scripts.event_dates import is_display_date
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import should_show_in_main_list


STATUS_LABELS = {
    'active': '近期有动作',
    'quiet': '已检查，暂无显著变化',
    'changed_below_threshold': '有变化，未达情报门槛',
    'failed': '接入失效',
    'partial': '部分覆盖',
    'pending': '待接入',
    'paused': '已暂停接入',
    'unverified': '状态待确认',
}


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _normalize_url(value):
    if not value:
        return ''
    parts = urlsplit(value.strip())
    path = parts.path.rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix('www.'), path, '', ''))


def _flatten_events(data):
    events = []
    for bucket, items in (data or {}).items():
        for source_event in items or []:
            event = dict(source_event)
            event.setdefault('date', bucket)
            events.append(event)
    return events


def _metric_sources(record):
    rows = {}
    for group in ('rss', 'html', 'company', 'jobs'):
        for name, value in ((record.get(group) or {}).get('source_stats') or {}).items():
            rows[name] = value or {}
    return rows


def _registry_match(point, registry_sources):
    source_id = point.get('source_id') or ''
    if source_id:
        return next((source for source in registry_sources if source.get('id') == source_id), None)
    point_url = _normalize_url(point.get('url'))
    if point_url:
        exact = next(
            (source for source in registry_sources if _normalize_url(source.get('url')) == point_url),
            None,
        )
        if exact:
            return exact
    return None


def _point_events(events, entity, source):
    source_id = (source or {}).get('id') or ''
    source_name = (source or {}).get('name') or ''
    matched = []
    for event in events:
        if not event_matches_entity(event, entity):
            continue
        if source_id and event.get('source_id') == source_id:
            matched.append(event)
        elif source_name and event.get('source') == source_name:
            matched.append(event)
    return matched


def _point_status(point, source, latest_metric, qualified_count, raw_change_count):
    if point.get('status') == 'paused':
        return 'paused'
    if not point.get('instrumented') and latest_metric is None:
        return 'pending'
    if not source or latest_metric is None:
        return 'unverified'
    fetch_status = latest_metric.get('fetch_status') or (
        'failed' if latest_metric.get('status') == 'failed' else 'unknown'
    )
    if fetch_status in {'failed', 'parse_failed'}:
        return 'failed'
    if qualified_count:
        return 'active'
    if raw_change_count:
        return 'changed_below_threshold'
    if fetch_status == 'success':
        return 'quiet'
    return 'unverified'


def _entity_status(point_statuses):
    if not point_statuses or all(status == 'pending' for status in point_statuses):
        return 'pending'
    non_pending = [status for status in point_statuses if status != 'pending']
    if non_pending and all(status == 'failed' for status in non_pending):
        return 'failed'
    if any(status in {'failed', 'unverified', 'pending'} for status in point_statuses):
        return 'partial'
    if any(status == 'active' for status in point_statuses):
        return 'active'
    if any(status == 'changed_below_threshold' for status in point_statuses):
        return 'changed_below_threshold'
    return 'quiet'


def build_entity_observation_ledger(
    pool_path=None,
    registry_path=None,
    metrics_path=None,
    events_path=None,
    as_of=None,
    job_metrics_path=None,
    candidate_path=None,
):
    # 默认路径锚定仓库根（见 docs/ARCHITECTURE.md「路径锚定仓库根」）
    pool_path = pool_path or data_path('entity_pool.json')
    registry_path = registry_path or data_path('source_registry.json')
    metrics_path = metrics_path or data_path('run_metrics.json')
    events_path = events_path or data_path('events.json')
    job_metrics_path = job_metrics_path or data_path('job_observation_metrics.json')
    candidate_path = candidate_path or data_path('signal_candidates.json')
    pool = _load(pool_path, {})
    registry = _load(registry_path, {})
    records = _load(metrics_path, [])
    latest_job_metrics = _load(job_metrics_path, {})
    candidate_pool = _load(candidate_path, {'candidates': []})
    events = _flatten_events(_load(events_path, {}))
    as_of = as_of or datetime.now().strftime('%Y-%m-%d')
    if latest_job_metrics.get('source_stats'):
        observed_at = latest_job_metrics.get('observed_at') or ''
        records = list(records) + [{
            'run_id': f"jobs-{observed_at}",
            'date': observed_at[:10],
            'started_at': observed_at,
            'jobs': latest_job_metrics,
        }]
    start_7 = (datetime.strptime(as_of, '%Y-%m-%d') - timedelta(days=6)).strftime('%Y-%m-%d')
    start_30 = (datetime.strptime(as_of, '%Y-%m-%d') - timedelta(days=29)).strftime('%Y-%m-%d')
    registry_sources = list(registry.get('sources') or []) + list(registry.get('active_sources') or [])
    valid_events = [
        prepare_event_contract(event) for event in events
        if start_30 <= (event.get('date') or '')[:10] <= as_of
        and is_display_date(event.get('date'), now=f'{as_of}T23:59:59+08:00')
    ]
    valid_records = [
        record for record in records
        if (record.get('date') or '') <= as_of
    ]

    entity_rows = []
    for entity in pool.get('entities') or []:
        entity_events = [event for event in valid_events if event_matches_entity(event, entity)]
        qualified_entity_events = [event for event in entity_events if should_show_in_main_list(event)]
        entity_candidates = [
            row for row in candidate_pool.get('candidates') or []
            if row.get('entity_id') == entity.get('id')
        ]
        recent_candidates = [
            row for row in entity_candidates
            if (row.get('detected_at') or '')[:10] >= start_7
        ]
        promoted_candidates = [row for row in entity_candidates if row.get('status') == 'promoted']
        point_rows = []
        for point in entity.get('observation_points') or []:
            source = _registry_match(point, registry_sources)
            source_name = (source or {}).get('name') or ''
            source_records = []
            for record in valid_records:
                metric = _metric_sources(record).get(source_name) if source_name else None
                if metric is not None:
                    source_records.append((record, metric))
            latest_record, latest_metric = source_records[-1] if source_records else ({}, None)
            successful = [
                (record, metric) for record, metric in source_records
                if metric.get('fetch_status') == 'success'
                or (metric.get('status') == 'ok' and (metric.get('count') or 0) > 0)
            ]
            changed = [
                (record, metric) for record, metric in source_records
                if (metric.get('count') or 0) > 0
            ]
            recent_changed = [
                (record, metric) for record, metric in changed
                if (record.get('date') or '') >= start_7
            ]
            point_events = _point_events(valid_events, entity, source)
            qualified = [event for event in point_events if should_show_in_main_list(event)]
            raw_change_count = sum(metric.get('count') or 0 for _, metric in recent_changed)
            status = _point_status(point, source, latest_metric, len(qualified), raw_change_count)
            point_rows.append({
                'point_type': point.get('type') or 'unknown',
                'url': point.get('url') or '',
                'source_id': (source or {}).get('id') or '',
                'source_name': source_name,
                'instrumented': bool(point.get('instrumented') or latest_metric is not None),
                'status': status,
                'status_label': STATUS_LABELS[status],
                'last_checked_at': (latest_record.get('started_at') or '') if latest_metric is not None else '',
                'last_success_at': successful[-1][0].get('started_at', '') if successful else '',
                'fetch_status': (latest_metric or {}).get('fetch_status') or (latest_metric or {}).get('status') or '',
                'last_change_at': changed[-1][0].get('started_at', '') if changed else '',
                'raw_change_count_7d': raw_change_count,
                'qualified_event_count_30d': len(qualified),
                'last_qualified_event_at': max(((event.get('date') or '')[:10] for event in qualified), default=''),
            })
        point_statuses = [row['status'] for row in point_rows]
        coverage_status = _entity_status(point_statuses)
        status = 'active' if qualified_entity_events else coverage_status
        if qualified_entity_events or promoted_candidates:
            activity_status = 'active'
        elif recent_candidates:
            activity_status = 'candidate'
        elif any(row['raw_change_count_7d'] for row in point_rows):
            activity_status = 'changed_below_threshold'
        elif coverage_status in {'quiet', 'partial'}:
            activity_status = 'quiet'
        else:
            activity_status = 'unknown'
        entity_rows.append({
            'entity_id': entity.get('id') or '',
            'entity': entity.get('name') or '',
            'region': entity.get('region') or '',
            'sector': entity.get('sector') or '',
            'status': status,
            'status_label': STATUS_LABELS[status],
            'coverage_status': coverage_status,
            'activity_status': activity_status,
            'last_checked_at': max((row['last_checked_at'] for row in point_rows), default=''),
            'last_success_at': max((row['last_success_at'] for row in point_rows), default=''),
            'last_change_at': max((row['last_change_at'] for row in point_rows), default=''),
            'raw_change_count_7d': sum(row['raw_change_count_7d'] for row in point_rows),
            'qualified_event_count_30d': len(qualified_entity_events),
            'last_qualified_event_at': max(((event.get('date') or '')[:10] for event in qualified_entity_events), default=''),
            'candidate_signal_count_7d': len(recent_candidates),
            'last_candidate_at': max(((row.get('detected_at') or '') for row in entity_candidates), default=''),
            'last_promoted_at': max(((row.get('detected_at') or '') for row in promoted_candidates), default=''),
            'observation_points': point_rows,
        })
    return {
        'generated_at': datetime.now().astimezone().isoformat(),
        'as_of': as_of,
        'entities': entity_rows,
        'status_counts': dict(Counter(row['status'] for row in entity_rows)),
    }


def write_entity_observation_ledger(report, path=None):
    target = Path(path or data_path('entity_observation_ledger.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pool-path')
    parser.add_argument('--registry-path')
    parser.add_argument('--metrics-path')
    parser.add_argument('--events-path')
    parser.add_argument('--as-of')
    parser.add_argument('--json-out')
    parser.add_argument('--candidate-path')
    args = parser.parse_args()
    report = build_entity_observation_ledger(
        args.pool_path, args.registry_path, args.metrics_path, args.events_path, args.as_of,
        candidate_path=args.candidate_path,
    )
    write_entity_observation_ledger(report, args.json_out)
    print(f"entity observation ledger | as_of={report['as_of']} status={report['status_counts']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
