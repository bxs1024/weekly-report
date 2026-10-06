"""Report collection timing differences across workflow runs."""

import argparse
import json
import os
from pathlib import Path

# 路径锚定仓库根（见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_METRICS_PATH = Path(_REPO_ROOT) / 'data' / 'run_metrics.json'


def _read_metrics(path=DEFAULT_METRICS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _compact_counts(counts, limit=4):
    if not counts:
        return '-'
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ', '.join(f'{key}:{value}' for key, value in items[:limit])


def build_collection_timing_rows(metrics=None, limit=8):
    records = list(metrics if metrics is not None else _read_metrics())
    rows = []
    for record in records[-limit:]:
        collection = record.get('collection') or {}
        filtering = record.get('filtering') or {}
        storage = record.get('storage') or {}
        rows.append({
            'run_id': record.get('run_id') or '',
            'started_at': record.get('started_at') or '',
            'run_date': record.get('date') or '',
            'environment': record.get('environment') or '',
            'raw': collection.get('raw_count', 0),
            'unique': collection.get('unique_count', 0),
            'smart_filtered': filtering.get('smart_filtered_count', 0),
            'ai_filtered': filtering.get('ai_filtered_count', 0),
            'added': storage.get('added_count', 0),
            'generic_added': storage.get('generic_added', 0),
            'company_added': storage.get('company_added', 0),
            'duplicate_skipped': storage.get('duplicate_skipped', 0),
            'event_dates': storage.get('added_event_dates') or {},
            'source_tiers': storage.get('added_source_tiers') or {},
        })
    return rows


def print_collection_timing_report(rows):
    print(
        'collection timing | run_id | started_at | run_date | env | raw | unique | '
        'smart | ai_filtered | added | generic | company | duplicate | event_dates | source_tiers'
    )
    for row in rows:
        print(
            '{run_id} | {started_at} | {run_date} | {environment} | {raw} | {unique} | '
            '{smart_filtered} | {ai_filtered} | {added} | {generic_added} | '
            '{company_added} | {duplicate_skipped} | {event_dates} | {source_tiers}'.format(
                **{
                    **row,
                    'event_dates': _compact_counts(row.get('event_dates')),
                    'source_tiers': _compact_counts(row.get('source_tiers')),
                }
            )
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--path', default=str(DEFAULT_METRICS_PATH))
    args = parser.parse_args()
    rows = build_collection_timing_rows(_read_metrics(args.path), limit=args.limit)
    print_collection_timing_report(rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
