"""Check stored event analysis quality.

This is intentionally lightweight: it warns when events.json contains many
template-like analyses, and can fail CI when high-score events still need repair.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from analysis_quality import annotate_event_quality, summarize_quality
    from repo_paths import data_path
except ImportError:
    from scripts.analysis_quality import annotate_event_quality, summarize_quality
    from scripts.repo_paths import data_path


def _load_events(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return {'legacy': data}
    return data


def _select_events(data, date_key):
    if date_key:
        return list(data.get(date_key, []))
    events = []
    for day_events in data.values():
        events.extend(day_events)
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path')
    parser.add_argument('--date', help='Only check one YYYY-MM-DD bucket')
    parser.add_argument('--max-repair-ratio', type=float, default=0.35)
    parser.add_argument('--fail-on-high-score-repair', action='store_true')
    args = parser.parse_args()

    path = Path(args.path or data_path('events.json'))
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 1

    events = _select_events(_load_events(path), args.date)
    for event in events:
        annotate_event_quality(event)
    summary = summarize_quality(events)

    print(
        "quality | total={total} needs_repair={needs_repair} "
        "fallback_or_failed={fallback_or_failed} "
        "high_score_needs_repair={high_score_needs_repair} "
        "repair_ratio={repair_ratio:.1%}".format(**summary)
    )

    bad = False
    if summary['repair_ratio'] > args.max_repair_ratio:
        print(
            f"WARNING: repair ratio {summary['repair_ratio']:.1%} "
            f"> {args.max_repair_ratio:.1%}"
        )
        bad = True
    if args.fail_on_high_score_repair and summary['high_score_needs_repair']:
        print("ERROR: high-score events still need repair")
        bad = True

    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
