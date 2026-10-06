import json

from source_conversion_report import build_source_conversion_report, classify_filter_reason


def _event(**overrides):
    base = {
        'date': '2026-06-08',
        'title': 'European fintech startup raises funding for payment platform',
        'summary_short': 'European fintech startup raises funding for payment platform.',
        'reason': '支付平台融资会影响商户收单和跨境支付合作窗口。',
        'impact': '支付服务商、跨境电商商户',
        'source': 'TechCrunch',
        'source_tier': 'L2 垂直交易源',
        'event_types': ['funding'],
        'score': 7,
        'url': 'https://example.com/a',
    }
    base.update(overrides)
    return base


def test_filter_reason_main():
    assert classify_filter_reason(_event()) == 'main'


def test_filter_reason_out_of_scope_before_quality():
    event = _event(
        title='Biotech company raises funding for clinical trial',
        summary_short='Biotech company raises funding for clinical trial.',
        reason='临床试验融资。',
        impact='未知',
    )
    assert classify_filter_reason(event) == 'out_of_scope_industry'


def test_filter_reason_capital_only_low_actionability():
    event = _event(
        title='SaaS startup raises funding from global investors',
        summary_short='SaaS startup raises funding.',
        reason='资本进入但没有明确业务动作。',
        impact='投资机构',
        score=7,
    )
    assert classify_filter_reason(event) == 'capital_only_low_actionability'


def test_filter_reason_quality_review():
    event = _event(summary_short='', event_types=['other'], score=2)
    assert classify_filter_reason(event) == 'quality_review'


def test_conversion_aggregates_run_and_event_stats(tmp_path):
    data_path = tmp_path / 'data'
    data_path.mkdir()
    events_path = data_path / 'events.json'
    metrics_path = data_path / 'run_metrics.json'
    registry_path = data_path / 'source_registry.json'
    events_path.write_text(
        """
        {
          "2026-06-08": [
            {
              "date": "2026-06-08",
              "title": "European fintech startup raises funding for payment platform",
              "summary_short": "European fintech startup raises funding for payment platform.",
              "reason": "支付平台融资会影响商户收单和跨境支付合作窗口。",
              "impact": "支付服务商、跨境电商商户",
              "source": "TechCrunch",
              "source_tier": "L2 垂直交易源",
              "event_types": ["funding"],
              "score": 7,
              "url": "https://example.com/a"
            }
          ]
        }
        """,
        encoding='utf-8',
    )
    metrics_path.write_text(
        """
        [
          {
            "date": "2026-06-08",
            "rss": {
              "source_stats": {
                "TechCrunch": {"count": 10, "signal_count": 4, "status": "ok", "method": "rss"}
              }
            },
            "source_funnel": {
              "TechCrunch": {"raw": 10, "smart_kept": 4, "score_ai_tier": 1, "added": 1}
            }
          }
        ]
        """,
        encoding='utf-8',
    )
    registry_path.write_text('{"sources": []}', encoding='utf-8')

    # 路径按架构契约显式注入（读路径锚定仓库根，不靠 chdir 漂移）
    report = build_source_conversion_report(
        days=1,
        events=json.loads(events_path.read_text(encoding='utf-8')),
        metrics_path=str(metrics_path),
        registry_path=str(registry_path),
    )

    row = report['rows'][0]
    assert row['source'] == 'TechCrunch'
    assert row['raw'] == 10
    assert row['signal'] == 4
    assert row['stored'] == 1
    assert row['main'] == 1
    assert row['lost_after_signal'] == 3
    assert row['governance_action'] == 'observe'
    assert row['funnel']['smart_kept'] == 4
    assert row['funnel']['score_ai_tier'] == 1


def test_conversion_marks_high_signal_zero_main_for_governance(tmp_path):
    data_path = tmp_path / 'data'
    data_path.mkdir()
    (data_path / 'events.json').write_text('{"2026-06-08": []}', encoding='utf-8')
    (data_path / 'run_metrics.json').write_text(
        """
        [
          {
            "date": "2026-06-08",
            "rss": {
              "source_stats": {
                "Stripe Changelog": {"count": 60, "signal_count": 60, "status": "ok", "method": "rss"}
              }
            }
          }
        ]
        """,
        encoding='utf-8',
    )
    (data_path / 'source_registry.json').write_text('{"sources": []}', encoding='utf-8')

    report = build_source_conversion_report(
        days=1,
        events={},  # 该用例只考察 run_metrics 侧的漏斗，事件集为空
        metrics_path=str(data_path / 'run_metrics.json'),
        registry_path=str(data_path / 'source_registry.json'),
    )

    row = report['rows'][0]
    assert row['source'] == 'Stripe Changelog'
    assert row['governance_action'] == 'audit_raw_signal_quality'


def test_company_query_keeps_entity_source_lineage(tmp_path):
    data_path = tmp_path / 'data'
    data_path.mkdir()
    (data_path / 'events.json').write_text(
        '''{"2026-06-08":[{"date":"2026-06-08","title":"Naver launches enterprise AI platform","summary_short":"Naver launches enterprise AI platform","reason":"Naver expands its enterprise AI platform and cloud ecosystem.","impact":"Enterprise developers and cloud partners","source":"Google News","source_id":"Naver","is_company":true,"company_name":"Naver","source_tier":"L5 Google News 补漏源","event_types":["strategy"],"score":7,"url":"https://news.google.com/a"}]}''',
        encoding='utf-8',
    )
    (data_path / 'run_metrics.json').write_text(
        '''[{"date":"2026-06-08","company":{"source_stats":{"Naver":{"count":1,"signal_count":1,"status":"ok","method":"company"}}}}]''',
        encoding='utf-8',
    )
    (data_path / 'source_registry.json').write_text('{"sources":[]}', encoding='utf-8')
    report = build_source_conversion_report(
        days=1,
        events=json.loads((data_path / 'events.json').read_text(encoding='utf-8')),
        metrics_path=str(data_path / 'run_metrics.json'),
        registry_path=str(data_path / 'source_registry.json'),
    )
    row = next(row for row in report['rows'] if row['source'] == 'Naver')
    assert row['raw'] == 1
    assert row['stored'] == 1


if __name__ == '__main__':
    test_filter_reason_main()
    test_filter_reason_out_of_scope_before_quality()
    test_filter_reason_capital_only_low_actionability()
    test_filter_reason_quality_review()
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as temp_dir:
        test_conversion_aggregates_run_and_event_stats(Path(temp_dir))
    with TemporaryDirectory() as temp_dir:
        test_conversion_marks_high_signal_zero_main_for_governance(Path(temp_dir))
    with TemporaryDirectory() as temp_dir:
        test_company_query_keeps_entity_source_lineage(Path(temp_dir))
    print('source conversion tests passed')
