import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from check_data_health import (
    _future_event_count,
    build_health_report,
    build_quick_health_report,
    collect_failures,
    print_quick_report,
    print_report,
)
from generate_html import build_company_cards, build_date_panel
from run_metrics import latest_run_metrics, write_run_metrics


class Args:
    min_today = 1
    min_company_quality_nonzero = 1
    min_feed_entries = 1
    max_feed_google_ratio = 0
    max_duplicate_ratio = 0.35
    require_run_metrics = False


def test_current_data_health_contract():
    report = build_health_report(days=7)
    failures = collect_failures(report, Args)
    assert failures == []


def test_health_report_prints_observation_failures():
    report = build_health_report(days=7)
    output = io.StringIO()
    with redirect_stdout(output):
        print_report(report)
    assert 'observation | must=' in output.getvalue()


def test_quick_health_uses_persisted_facts(tmp_path):
    (tmp_path / 'events.json').write_text(
        '{"2026-08-02": [{"title": "one"}], "2026-08-03": [], "2026-09-01": [{"title": "future"}]}',
        encoding='utf-8',
    )
    (tmp_path / 'run_metrics.json').write_text(
        '[{"date":"2026-08-02","filtering":{"scope_qualified_count":4,"scope_candidate_count":1,"scope_filtered_count":3},"storage":{"added_count":2}}]',
        encoding='utf-8',
    )
    (tmp_path / 'ledger.json').write_text('{"entities":[]}', encoding='utf-8')
    (tmp_path / 'pool.json').write_text('{"portfolio":{"must":[]}}', encoding='utf-8')
    (tmp_path / 'jobs.json').write_text('{"source_stats":{}}', encoding='utf-8')
    (tmp_path / 'candidates.json').write_text('{"candidates":[]}', encoding='utf-8')
    report = build_quick_health_report(
        str(tmp_path / 'events.json'),
        str(tmp_path / 'run_metrics.json'),
        str(tmp_path / 'ledger.json'),
        str(tmp_path / 'pool.json'),
        str(tmp_path / 'jobs.json'),
        str(tmp_path / 'candidates.json'),
        now='2026-08-03T12:00:00+08:00',
    )
    assert report['latest_data_date'] == '2026-08-02'
    assert report['future_event_count'] == 1
    assert report['scope_qualified'] == 4
    output = io.StringIO()
    with redirect_stdout(output):
        print_quick_report(report)
    assert 'scope | qualified=4 candidate=1 filtered=3' in output.getvalue()


def test_run_metrics_roundtrip():
    path = Path('data/test_run_metrics.json')
    if path.exists():
        path.unlink()
    write_run_metrics({
        'run_id': 'test-run',
        'date': '2026-06-01',
        'collection': {'raw_count': 10, 'unique_count': 8},
        'filtering': {'smart_filtered_count': 6, 'ai_filtered_count': 5},
        'storage': {'added_count': 3, 'duplicate_skipped': 2},
    }, path=path, keep=5)
    latest = latest_run_metrics(path)
    assert latest['run_id'] == 'test-run'
    assert latest['collection']['raw_count'] == 10
    path.unlink()


def test_future_event_count_detects_polluted_publication_dates(tmp_path):
    path = tmp_path / 'events.json'
    path.write_text(
        '{"2026-07-15": [{"date": "2026-07-15"}], "2026-09-15": [{"published_at": "2026-09-15"}]}',
        encoding='utf-8',
    )
    assert _future_event_count(str(path), now='2026-07-15T14:30:00+08:00') == 1


def test_future_event_count_allows_today_and_next_day_dates(tmp_path):
    path = tmp_path / 'events.json'
    path.write_text(
        '{"2026-09-02": [{"date": "2026-09-02"}], '
        '"2026-09-03": [{"published_at": "2026-09-03"}], '
        '"2026-09-04": [{"published_at": "2026-09-04"}]}',
        encoding='utf-8',
    )
    assert _future_event_count(str(path), now='2026-09-02T04:56:00+08:00') == 1


def test_future_event_count_defaults_to_beijing_time(tmp_path):
    path = tmp_path / 'events.json'
    today = datetime.now(timezone(timedelta(hours=8))).date()
    buckets = [today, today + timedelta(days=1), today + timedelta(days=2)]
    path.write_text(
        json.dumps({day.isoformat(): [{'date': day.isoformat()}] for day in buckets}),
        encoding='utf-8',
    )
    assert _future_event_count(str(path)) == 1


def test_company_card_uses_observation_status_when_no_event_exists():
    cards = build_company_cards(
        [{'name': 'Stripe', 'region': '全球', 'count': 0, 'events': []}],
        '2026-07-15',
        {'entities': [{
            'entity': 'Stripe',
            'status': 'quiet',
            'status_label': '已检查，暂无显著变化',
            'last_checked_at': '2026-07-15T12:00:00+08:00',
            'raw_change_count_7d': 0,
            'qualified_event_count_30d': 0,
            'observation_points': [{'instrumented': True}],
        }]},
    )
    assert cards[0]['observation_status'] == 'quiet'
    assert cards[0]['observation_label'] == '已检查，暂无显著变化'
    assert cards[0]['observation_detail'] == '采集正常，近期没有显著组织行为变化'


def _panel_event(date, title, url, company, topic):
    return {
        'title': title,
        'display_title': title,
        'summary_short': title,
        'url': url,
        'source': 'TechCrunch',
        'source_tier': 'L2 垂直交易源',
        'event_types': ['funding'],
        'score': 7,
        'region': '欧洲',
        'company_name': company,
        'companies': [company],
        'reason': '欧洲AI基础设施公司融资，云和开发者生态出现预算窗口',
        'impact': '云服务商、AI基础设施供应商',
        'trend_topic': topic,
        'date': date,
    }


def test_date_panel_does_not_leak_current_day_content():
    old_events = [
        _panel_event('2026-05-30', 'Old AI infra A raises funding', 'https://example.com/old-a', 'OldA', '欧洲AI基础设施'),
        _panel_event('2026-05-30', 'Old AI infra B raises funding', 'https://example.com/old-b', 'OldB', '欧洲AI基础设施'),
    ]
    current_events = [
        _panel_event('2026-06-03', 'Current AI infra A raises funding', 'https://example.com/new-a', 'NewA', '欧洲AI基础设施'),
        _panel_event('2026-06-03', 'Current AI infra B raises funding', 'https://example.com/new-b', 'NewB', '欧洲AI基础设施'),
    ]
    events_by_date = {
        '2026-05-30': old_events,
        '2026-06-03': current_events,
    }
    panel = build_date_panel(
        '2026-05-30',
        old_events,
        events_by_date,
        old_events,
        cluster_events=old_events + current_events,
    )
    assert all(event['date'] == '2026-05-30' for event in panel['top3'])
    assert all(event['date'] == '2026-05-30' for event in panel['evidence_events'])
    assert all(
        event['date'] == '2026-05-30'
        for cluster in panel['signal_clusters']
        for event in cluster.get('evidence_events', [])
    )
    assert not any(event['date'] == '2026-06-03' for event in panel['top3'])


def test_date_panel_suppresses_stale_rolling_clusters():
    selected_day = [
        _panel_event('2026-05-30', 'Selected day signal', 'https://example.com/selected', 'SelectedCo', '欧洲AI基础设施'),
    ]
    stale_cluster_events = [
        _panel_event('2026-05-27', 'Stale cluster A', 'https://example.com/stale-a', 'StaleA', '欧洲旅游科技'),
        _panel_event('2026-05-27', 'Stale cluster B', 'https://example.com/stale-b', 'StaleB', '欧洲旅游科技'),
    ]
    panel = build_date_panel(
        '2026-05-30',
        selected_day,
        {'2026-05-30': selected_day, '2026-05-27': stale_cluster_events},
        selected_day,
        cluster_events=selected_day + stale_cluster_events,
    )
    assert panel['signal_clusters'] == []
    assert [event['date'] for event in panel['evidence_events']] == ['2026-05-30']


if __name__ == '__main__':
    test_current_data_health_contract()
    test_health_report_prints_observation_failures()
    with TemporaryDirectory() as temp_dir:
        test_quick_health_uses_persisted_facts(Path(temp_dir))
    test_run_metrics_roundtrip()
    with TemporaryDirectory() as temp_dir:
        test_future_event_count_detects_polluted_publication_dates(Path(temp_dir))
    with TemporaryDirectory() as temp_dir:
        test_future_event_count_allows_today_and_next_day_dates(Path(temp_dir))
    with TemporaryDirectory() as temp_dir:
        test_future_event_count_defaults_to_beijing_time(Path(temp_dir))
    test_company_card_uses_observation_status_when_no_event_exists()
    test_date_panel_does_not_leak_current_day_content()
    test_date_panel_suppresses_stale_rolling_clusters()
    print('data health tests passed')
