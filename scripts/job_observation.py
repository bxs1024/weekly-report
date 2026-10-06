"""Snapshot and diff job boards without turning individual jobs into events."""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# 仓库路径锚定仓库根（见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
try:
    from repo_paths import data_path
except ImportError:
    from scripts.repo_paths import data_path


HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; WeeklyReportObserver/1.0)'}

JOB_PATHS = {
    'Grab': re.compile(r'/en/jobs/\d+/[^/?#]+/?$', re.I),
    'Stripe': re.compile(r'/careers/listing/[^/?#]+/\d+/?$', re.I),
    'Shopify': re.compile(r'/careers/[^/?#]+_[0-9a-f-]{36}/?$', re.I),
}

FUNCTION_KEYWORDS = {
    'ai': (' ai ', 'machine learning', 'artificial intelligence', 'data science'),
    'engineering': ('engineer', 'developer', 'platform', 'infrastructure', 'architect'),
    'payments': ('payment', 'fintech', 'financial services', 'treasury'),
    'compliance': ('compliance', 'risk', 'privacy', 'legal', 'regulatory'),
    'partnerships': ('partner', 'partnership', 'ecosystem'),
    'sales': ('sales', 'account executive', 'business development', 'growth'),
    'operations': ('operations', 'ops ', 'support', 'people'),
}


def _job_id(url):
    path = urlparse(url).path.rstrip('/')
    tail = path.rsplit('/', 1)[-1]
    match = re.search(r'([0-9a-f]{8}-[0-9a-f-]{27}|\d{6,})', path, re.I)
    return match.group(1).lower() if match else tail.lower()


def _function_tags(title):
    text = f" {(title or '').lower()} "
    return [tag for tag, keywords in FUNCTION_KEYWORDS.items() if any(keyword in text for keyword in keywords)]


def _job_fingerprint(title):
    return re.sub(r'[^a-z0-9]+', '', (title or '').lower())


def extract_job_links(entity, base_url, html, limit=120):
    pattern = JOB_PATHS.get(entity)
    if not pattern or not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    jobs = []
    seen = set()
    seen_titles = set()
    for link in soup.select('a[href]'):
        absolute = urljoin(base_url, link.get('href') or '')
        if not pattern.search(urlparse(absolute).path):
            continue
        identifier = _job_id(absolute)
        if identifier in seen:
            continue
        title = ' '.join(link.get_text(' ', strip=True).split())
        if not title:
            title = urlparse(absolute).path.rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')
        title_fingerprint = _job_fingerprint(title)
        if title_fingerprint in seen_titles:
            continue
        seen.add(identifier)
        seen_titles.add(title_fingerprint)
        jobs.append({
            'id': identifier,
            'title': title,
            'url': absolute,
            'function_tags': _function_tags(title),
        })
        if len(jobs) >= limit:
            break
    return jobs


def diff_job_snapshots(previous, current):
    previous_by_id = {job['id']: job for job in previous or []}
    current_by_id = {job['id']: job for job in current or []}
    added = [job for identifier, job in current_by_id.items() if identifier not in previous_by_id]
    removed = [job for identifier, job in previous_by_id.items() if identifier not in current_by_id]
    clusters = Counter(tag for job in added for tag in job.get('function_tags') or [])
    removed_clusters = Counter(tag for job in removed for tag in job.get('function_tags') or [])
    return {
        'added_count': len(added),
        'removed_count': len(removed),
        'clusters': dict(clusters),
        'removed_clusters': dict(removed_clusters),
        'candidate_signal': (
            len(added) >= 3
            or len(removed) >= 3
            or any(count >= 2 for count in clusters.values())
            or any(count >= 2 for count in removed_clusters.values())
        ),
        'added': added,
        'removed': removed,
    }


def source_reset_suspected(previous, current, diff):
    """Detect ATS refreshes or truncated-list churn before creating signals."""
    previous_count = len(previous or [])
    current_count = len(current or [])
    if previous_count < 10 or current_count < 10:
        return False
    churn = (diff.get('added_count', 0) + diff.get('removed_count', 0)) / max(previous_count, current_count, 1)
    return current_count < previous_count * 0.4 or churn >= 1.0


def _candidate_window(observed_at):
    date_value = datetime.fromisoformat(observed_at).date()
    year, week, _ = date_value.isocalendar()
    return f'{year}-W{week:02d}'


def _candidate_id(entity_id, signal_type, cluster, observed_at):
    raw = f'jobs|{entity_id}|{signal_type}|{cluster}|{_candidate_window(observed_at)}'
    return 'jobs-' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def build_job_candidate(entity, diff, previous_jobs, current_jobs, observed_at):
    if not diff.get('candidate_signal'):
        return None
    reset = source_reset_suspected(previous_jobs, current_jobs, diff)
    added_clusters = diff.get('clusters') or {}
    removed_clusters = diff.get('removed_clusters') or {}
    signal_type = 'function_buildout' if diff.get('added_count', 0) else 'organizational_contraction'
    cluster_counts = added_clusters if signal_type == 'function_buildout' else removed_clusters
    cluster = max(cluster_counts, key=cluster_counts.get) if cluster_counts else 'mixed'
    dominant_count = cluster_counts.get(cluster, 0)
    if reset:
        status = 'rejected'
        rejection_reason = 'source_reset_suspected'
    elif signal_type == 'organizational_contraction':
        status = 'accumulating'
        rejection_reason = 'insufficient_persistence'
    elif diff.get('added_count', 0) >= 3 and dominant_count >= 2:
        status = 'qualified'
        rejection_reason = ''
    else:
        status = 'accumulating'
        rejection_reason = 'insufficient_cluster_strength'
    entity_id = entity.get('id') or (entity.get('name') or '').lower()
    return {
        'candidate_id': _candidate_id(entity_id, signal_type, cluster, observed_at),
        'entity_id': entity_id,
        'entity': entity.get('name') or '',
        'region': entity.get('region') or '',
        'sector': entity.get('sector') or '',
        'source_type': 'jobs_snapshot',
        'signal_type': signal_type,
        'cluster': cluster,
        'window': _candidate_window(observed_at),
        'window_start': observed_at[:10],
        'window_end': observed_at[:10],
        'detected_at': observed_at,
        'baseline_count': len(previous_jobs or []),
        'current_count': len(current_jobs or []),
        'added_count': diff.get('added_count', 0),
        'removed_count': diff.get('removed_count', 0),
        'clusters': added_clusters,
        'removed_clusters': removed_clusters,
        'evidence_refs': [
            {'job_id': job.get('id'), 'title': job.get('title'), 'url': job.get('url'), 'change': 'added'}
            for job in (diff.get('added') or [])[:20]
        ] + [
            {'job_id': job.get('id'), 'title': job.get('title'), 'url': job.get('url'), 'change': 'removed'}
            for job in (diff.get('removed') or [])[:20]
        ],
        'status': status,
        'rejection_reason': rejection_reason,
        'promoted_event_id': '',
    }


def merge_candidate_pool(existing, candidates, observed_at):
    rows = {row.get('candidate_id'): dict(row) for row in (existing.get('candidates') or []) if row.get('candidate_id')}
    promoted_events = []
    for candidate in candidates:
        previous = rows.get(candidate['candidate_id']) or {}
        if previous.get('status') == 'promoted':
            continue
        merged = {**previous, **candidate}
        old_refs = previous.get('evidence_refs') or []
        refs = old_refs + candidate.get('evidence_refs', [])
        seen = set()
        merged['evidence_refs'] = [
            ref for ref in refs
            if not ((ref.get('job_id'), ref.get('change')) in seen or seen.add((ref.get('job_id'), ref.get('change'))))
        ]
        merged['window_start'] = min(previous.get('window_start') or candidate['window_start'], candidate['window_start'])
        merged['window_end'] = max(previous.get('window_end') or candidate['window_end'], candidate['window_end'])
        if merged.get('status') == 'qualified':
            event_id = 'event-' + merged['candidate_id']
            merged['status'] = 'promoted'
            merged['promoted_event_id'] = event_id
            promoted_events.append(build_job_event(merged, event_id))
        rows[merged['candidate_id']] = merged
    ordered = sorted(rows.values(), key=lambda row: row.get('detected_at') or '', reverse=True)[:500]
    return {
        'version': 1,
        'generated_at': observed_at,
        'candidates': ordered,
    }, promoted_events


def build_job_event(candidate, event_id):
    cluster_labels = {
        'ai': 'AI与数据', 'engineering': '工程与平台', 'payments': '支付',
        'compliance': '合规与风控', 'partnerships': '合作伙伴', 'sales': '企业销售',
        'operations': '运营', 'mixed': '多职能',
    }
    cluster = cluster_labels.get(candidate.get('cluster'), candidate.get('cluster') or '多职能')
    added = candidate.get('added_count', 0)
    entity = candidate.get('entity') or '重点对象'
    date_value = candidate.get('detected_at', '')[:10]
    return {
        'event_id': event_id,
        'candidate_id': candidate.get('candidate_id'),
        'title': f'{entity}集中新增{added}个{cluster}岗位',
        'url': (candidate.get('evidence_refs') or [{}])[0].get('url') or '',
        'source': f'{entity} Jobs',
        'source_id': f"{candidate.get('entity_id')}-jobs",
        'origin_source_id': f"{candidate.get('entity_id')}-jobs",
        'observation_entity_id': candidate.get('entity_id'),
        'discovery_source': 'jobs_snapshot',
        'source_type': 'jobs_snapshot',
        'source_tier': 'L1 官方/IR源',
        'source_role': 'official_ir',
        'region': candidate.get('region') or '全球',
        'event_types': ['strategy'],
        'level': 'C',
        'score': 5,
        'summary_short': f'{entity}在本周新增{added}个{cluster}相关职位',
        'reason': f'集中招聘显示{entity}正在加强{cluster}能力建设，形成可核验的组织投入信号',
        'impact': '相关技术供应商、渠道伙伴和企业服务商',
        'insight_label': '组织变化',
        'trend_topic': f'{entity}{cluster}能力建设',
        'companies': [entity],
        'is_company': True,
        'company_name': entity,
        'date': date_value,
        'observed_at': candidate.get('detected_at'),
        'date_basis': 'observed_at',
        'analysis_source': 'jobs_candidate',
        'analysis_status': 'complete',
        'needs_repair': False,
        'quality_flags': [],
        'signal_taxonomy': ['org_change'],
        'evidence_refs': candidate.get('evidence_refs') or [],
    }


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def collect_job_observations(
    pool_path=None,
    snapshot_path=None,
    candidate_path=None,
    observed_at=None,
):
    pool_path = pool_path or data_path('entity_pool.json')
    snapshot_path = snapshot_path or data_path('job_snapshots.json')
    candidate_path = candidate_path or data_path('signal_candidates.json')
    pool = _load(pool_path, {})
    previous = _load(snapshot_path, {'entities': {}})
    existing_candidates = _load(candidate_path, {'version': 1, 'candidates': []})
    observed_at = observed_at or datetime.now().astimezone().isoformat()
    snapshots = dict(previous.get('entities') or {})
    source_stats = {}
    candidates = []
    for entity in pool.get('entities') or []:
        jobs_point = next(
            (
                point for point in entity.get('observation_points') or []
                if point.get('type') == 'jobs' and point.get('instrumented') and point.get('url')
            ),
            None,
        )
        if not jobs_point:
            continue
        name = entity.get('name') or ''
        source_name = f'{name} Jobs'
        try:
            response = requests.get(jobs_point['url'], headers=HEADERS, timeout=15)
            response.raise_for_status()
            jobs = extract_job_links(name, response.url, response.text)
            fetch_status = 'success' if jobs else 'parse_failed'
        except requests.RequestException:
            jobs = []
            fetch_status = 'failed'
        snapshot_key = entity.get('id') or name.lower()
        has_baseline = snapshot_key in snapshots
        previous_jobs = (snapshots.get(snapshot_key) or {}).get('jobs') or []
        diff = diff_job_snapshots(previous_jobs, jobs) if fetch_status == 'success' and has_baseline else {
            'added_count': 0, 'removed_count': 0, 'clusters': {}, 'removed_clusters': {}, 'candidate_signal': False,
        }
        if fetch_status == 'success':
            snapshots[snapshot_key] = {
                'entity': name,
                'observed_at': observed_at,
                'jobs': jobs,
            }
        source_stats[source_name] = {
            'method': 'jobs_snapshot',
            'region': entity.get('region') or '',
            'status': 'ok' if fetch_status == 'success' else 'failed',
            'fetch_status': fetch_status,
            'count': diff.get('added_count', 0) + diff.get('removed_count', 0),
            'inventory_count': len(jobs),
            'signal_count': 1 if diff.get('candidate_signal') else 0,
            'added_count': diff.get('added_count', 0),
            'removed_count': diff.get('removed_count', 0),
            'clusters': diff.get('clusters', {}),
            'removed_clusters': diff.get('removed_clusters', {}),
        }
        candidate = build_job_candidate(entity, diff, previous_jobs, jobs, observed_at)
        if candidate:
            candidates.append(candidate)
    candidate_pool, promoted_events = merge_candidate_pool(existing_candidates, candidates, observed_at)
    return {
        'observed_at': observed_at,
        'source_count': len(source_stats),
        'raw_count': sum(row['inventory_count'] for row in source_stats.values()),
        'source_stats': source_stats,
        'candidate_signals': candidates,
        'promoted_events': promoted_events,
    }, {'version': 1, 'generated_at': observed_at, 'entities': snapshots}, candidate_pool


def write_job_snapshots(snapshot, path=None):
    target = Path(path or data_path('job_snapshots.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    return target


def write_job_observation_metrics(metrics, path=None):
    target = Path(path or data_path('job_observation_metrics.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=2)
    return target


def write_signal_candidates(candidate_pool, path=None):
    target = Path(path or data_path('signal_candidates.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(candidate_pool, handle, ensure_ascii=False, indent=2)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pool-path')
    parser.add_argument('--snapshot-path')
    parser.add_argument('--metrics-path')
    parser.add_argument('--candidate-path')
    args = parser.parse_args()
    metrics, snapshot, candidate_pool = collect_job_observations(
        args.pool_path, args.snapshot_path, args.candidate_path,
    )
    write_job_snapshots(snapshot, args.snapshot_path)
    write_job_observation_metrics(metrics, args.metrics_path)
    write_signal_candidates(candidate_pool, args.candidate_path)
    print(
        f"jobs observation | sources={metrics['source_count']} jobs={metrics['raw_count']} "
        f"candidates={len(metrics['candidate_signals'])}"
    )
    for source, row in metrics['source_stats'].items():
        print(
            f"{source} | fetch={row['fetch_status']} inventory={row['inventory_count']} changes={row['count']} "
            f"added={row['added_count']} removed={row['removed_count']} clusters={row['clusters']}"
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
