import json
import re
import unittest.mock as mock

import generate_html
from generate_html import (
    _editorial_input_hash,
    build_period_report,
    build_weekly_editorial,
)
import fetch_news  # noqa: F401 — 确保模块已导入，便于 patch

# 模块级保护：默认不调真实 LLM，避免测试依赖 API key 或污染线上
_patch_api = mock.patch('fetch_news._chat_api_candidates', return_value=[])
_patch_api.start()

# 编辑层缓存读写隔离：测试绝不读写真实 data/editorial_cache.json
# 注意 side_effect 每次返回新 dict：_editorial_cache_put 是 load→改→存的真实实现，
# 若共享同一 dict 会把上一条测试写入的缓存泄漏给下一條测试
_patch_cache_read = mock.patch('generate_html._load_editorial_cache', side_effect=lambda: {})
_patch_cache_read.start()
_patch_cache_write = mock.patch('generate_html._save_editorial_cache')
_patch_cache_write.start()


def event(**overrides):
    base = {
        'title': 'Example AI infra startup raises funding',
        'display_title': 'Example AI infra startup raises funding',
        'summary_short': '欧洲AI基础设施公司融资扩张',
        'url': 'https://example.com/a',
        'source': 'Tech.eu',
        'source_tier': 'L2 垂直交易源',
        'event_types': ['funding'],
        'score': 7,
        'region': '欧洲',
        'company_name': 'ExampleAI',
        'companies': ['ExampleAI'],
        'reason': '欧洲AI基础设施公司融资，云、数据中心和开发者生态出现预算窗口',
        'impact': '云服务商、AI基础设施供应商',
        'trend_topic': '欧洲AI基础设施',
        'opportunity_direction': '云与AI基础设施',
        'bd_triggers': ['预算窗口'],
        'follow_up_window': '7天内',
        'bd_priority': '高',
        'date': '2026-06-03',
    }
    base.update(overrides)
    return base


def test_weekly_report_builds_focus_windows_from_repeated_signals():
    report = build_period_report([
        event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
        event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
    ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)

    assert report['focus_windows']
    window = report['focus_windows'][0]
    assert window['direction'] == 'AI与云基础设施'
    assert window['evidence_count'] == 2
    assert 'ExampleAI' in window['objects']
    assert 'CloudBox' in window['objects']
    assert len(window['evidence']) == 2


def test_monthly_trend_requires_cross_week_evidence_and_comparison():
    report = build_period_report([
        event(date='2026-06-03', url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
        event(date='2026-06-10', url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
        event(date='2026-06-18', url='https://example.com/c', company_name='InfraCo', companies=['InfraCo']),
    ], '2026-06-01', '2026-06-30', '6 月报', '2026-06', 'closed')

    assert report['period_themes']
    trend = report['period_themes'][0]
    assert trend['week_count'] >= 2
    assert trend['count'] >= 3
    assert trend['change'] in {'新增', '升温', '延续', '降温'}


def test_weekly_report_does_not_promote_single_event_to_focus_window():
    report = build_period_report([
        event(url='https://example.com/a'),
    ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)

    assert report['focus_windows'] == []


def test_monthly_report_does_not_enable_weekly_focus_windows_by_default():
    report = build_period_report([
        event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
        event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
    ], '2026-06-01', '2026-06-30', '6 月报', '2026-06', 'open')

    assert report['focus_windows'] == []
    assert '周报先看' not in report['summary']


def test_weekly_broad_window_keeps_out_of_scope_events_out():
    report = build_period_report([
        event(
            url='https://example.com/health-a',
            title='Tavo Biotherapeutics raises funding for ophthalmology therapies',
            display_title='Tavo Biotherapeutics raises funding for ophthalmology therapies',
            summary_short='Tavo Biotherapeutics获融资开发眼科疗法',
            reason='眼科疗法和生物制药研发获融资',
            impact='医疗器械供应商、临床试验服务商',
            trend_topic='非洲医疗科技融资',
            region='非洲',
            company_name='Tavo Biotherapeutics',
            companies=['Tavo Biotherapeutics'],
        ),
        event(
            url='https://example.com/health-b',
            title='Secretome Therapeutics raises funding for cardiac therapy',
            display_title='Secretome Therapeutics raises funding for cardiac therapy',
            summary_short='Secretome获融资用于心脏细胞治疗',
            reason='心脏细胞疗法和生物制药融资',
            impact='医疗技术供应商',
            trend_topic='非洲医疗科技融资',
            region='非洲',
            company_name='Secretome Therapeutics',
            companies=['Secretome Therapeutics'],
        ),
    ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)

    assert report['focus_windows'] == []


def _fake_apis():
    return [{'id': 'test', 'name': 'Test', 'url': 'http://fake', 'key': 'k' * 10, 'model': 'm'}]


def test_weekly_narrative_overrides_mainline_when_llm_succeeds():
    def fake_post_chat(api, prompt, **kw):
        keys = [m.group(1) for m in re.finditer(r'"key": "([^"]+)"', prompt)] or ['ai_infra']
        content = json.dumps({
            'mainline': '本周AI与云基础设施成为主线，资本同步加码算力与支付赛道。',
            'themes': [{'key': k, 'narrative': f'{k}主题的叙事导读'} for k in keys],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=fake_post_chat)
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
            event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
        ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    assert '叙事导读' in report['summary'] or 'AI' in report['summary']
    assert all(w.get('narrative') for w in report['focus_windows'])


def test_weekly_narrative_falls_back_to_template_when_llm_fails():
    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=Exception('boom'))
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
            event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
        ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    assert '本周期从' in report['summary']
    assert not any('narrative' in w for w in report['focus_windows'])


def test_weekly_editorial_failure_blocks_production_output():
    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=Exception('boom'))
    ctx_api.start()
    ctx_llm.start()
    try:
        try:
            build_period_report([
                event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
                event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
            ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open',
                focus_windows_enabled=True, require_editorial=True)
        except RuntimeError as exc:
            assert '拒绝发布降级版' in str(exc)
        else:
            raise AssertionError('生产周报在 AI 编辑失败时必须终止生成')
    finally:
        ctx_llm.stop()
        ctx_api.stop()


def test_monthly_comparison_counts_atoms_in_both_periods():
    # 上月 5 条原始事件中 4 条是同事实转载（压成 1 个 atom），修正后按 atom 口径 = 2
    previous_events = [
        event(url='https://example.com/p1', company_name='ExampleAI', companies=['ExampleAI'],
              title='Global AI startup closes funding round', display_title='Global AI startup closes funding round',
              summary_short='ExampleAI完成新一轮融资', date='2026-06-05'),
        event(url='https://example.com/p2', company_name='ExampleAI', companies=['ExampleAI'],
              title='Global AI startup closes funding round', display_title='Global AI startup closes funding round',
              summary_short='ExampleAI完成新一轮融资', date='2026-06-06'),
        event(url='https://example.com/p3', company_name='ExampleAI', companies=['ExampleAI'],
              title='Global AI startup closes funding round', display_title='Global AI startup closes funding round',
              summary_short='ExampleAI完成新一轮融资', date='2026-06-06'),
        event(url='https://example.com/p4', company_name='ExampleAI', companies=['ExampleAI'],
              title='Global AI startup closes funding round', display_title='Global AI startup closes funding round',
              summary_short='ExampleAI完成新一轮融资', date='2026-06-07'),
        event(url='https://example.com/p5', company_name='CloudBox', companies=['CloudBox'],
              title='CloudBox raises for AI data center expansion', display_title='CloudBox raises for AI data center expansion',
              summary_short='CloudBox为AI数据中心扩张融资', date='2026-06-15'),
    ]
    current_events = [
        event(url='https://example.com/c1', company_name='ExampleAI', companies=['ExampleAI'],
              title='ExampleAI expands AI inference capacity', display_title='ExampleAI expands AI inference capacity',
              summary_short='ExampleAI扩大推理算力', date='2026-07-03'),
        event(url='https://example.com/c2', company_name='CloudBox', companies=['CloudBox'],
              title='CloudBox launches regional data center', display_title='CloudBox launches regional data center',
              summary_short='CloudBox启动区域数据中心', date='2026-07-10'),
        event(url='https://example.com/c3', company_name='InfraCo', companies=['InfraCo'],
              title='InfraCo secures GPU supply for inference', display_title='InfraCo secures GPU supply for inference',
              summary_short='InfraCo锁定推理GPU供应', date='2026-07-18'),
    ]
    report = build_period_report(previous_events + current_events, '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    assert report['period_themes']
    trend = report['period_themes'][0]
    assert trend['key'] == 'ai_infra'
    assert trend['previous_count'] == 2
    assert trend['change'] == '延续'


def test_monthly_trend_requires_min_absolute_delta():
    previous_events = [
        event(url='https://example.com/a1', company_name='ExampleAI', companies=['ExampleAI'],
              title='ExampleAI expands AI capacity', display_title='ExampleAI expands AI capacity', date='2026-06-04'),
        event(url='https://example.com/a2', company_name='CloudBox', companies=['CloudBox'],
              title='CloudBox expands AI capacity', display_title='CloudBox expands AI capacity', date='2026-06-11'),
        event(url='https://example.com/a3', company_name='InfraCo', companies=['InfraCo'],
              title='InfraCo expands AI capacity', display_title='InfraCo expands AI capacity', date='2026-06-18'),
    ]
    current_events = [
        event(url='https://example.com/b1', company_name='ExampleAI', companies=['ExampleAI'],
              title='ExampleAI adds regional inference nodes', display_title='ExampleAI adds regional inference nodes', date='2026-07-02'),
        event(url='https://example.com/b2', company_name='CloudBox', companies=['CloudBox'],
              title='CloudBox adds regional inference nodes', display_title='CloudBox adds regional inference nodes', date='2026-07-09'),
        event(url='https://example.com/b3', company_name='InfraCo', companies=['InfraCo'],
              title='InfraCo adds regional inference nodes', display_title='InfraCo adds regional inference nodes', date='2026-07-16'),
        event(url='https://example.com/b4', company_name='NebulaAI', companies=['NebulaAI'],
              title='NebulaAI adds regional inference nodes', display_title='NebulaAI adds regional inference nodes', date='2026-07-23'),
    ]
    report = build_period_report(previous_events + current_events, '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    assert report['period_themes']
    trend = report['period_themes'][0]
    # 4 相对 3 增幅 33%，但未达到 +2 绝对门槛，应判延续而非升温
    assert trend['previous_count'] == 3
    assert trend['change'] == '延续'


def test_monthly_trend_coverage_correction_suppresses_false_heat():
    """新增信源虚增当前窗口总量时，覆盖率校正应把"假升温"压回延续。

    5月/6月基线各 2 条 AI 事实（总事件 5），7月 4 条 AI 事实但总事件涨到 8
    （疑似新信源加入）。若不校正，4 vs 2 会误判升温；校正后应判延续。
    """
    def ai(name, url, date):
        return event(url=url, company_name=name, companies=[name],
                     title=f'{name} expands AI inference capacity',
                     display_title=f'{name} expands AI inference capacity',
                     reason='AI 推理能力扩张', date=date)

    def pay(name, url, date):
        return event(url=url, company_name=name, companies=[name],
                     title=f'{name} expands payment wallet in Southeast Asia',
                     display_title=f'{name} expands payment wallet in Southeast Asia',
                     reason='东南亚支付扩展', date=date)

    may = [
        ai('Alpha', 'https://example.com/m1', '2026-05-04'),
        ai('Beta', 'https://example.com/m2', '2026-05-18'),
        pay('G', 'https://example.com/m3', '2026-05-06'),
        pay('H', 'https://example.com/m4', '2026-05-13'),
        pay('I', 'https://example.com/m5', '2026-05-20'),
    ]
    jun = [
        ai('Gamma', 'https://example.com/j1', '2026-06-01'),
        ai('Delta', 'https://example.com/j2', '2026-06-15'),
        pay('J', 'https://example.com/j3', '2026-06-03'),
        pay('K', 'https://example.com/j4', '2026-06-10'),
        pay('L', 'https://example.com/j5', '2026-06-17'),
    ]
    jul = [
        ai('E', 'https://example.com/v1', '2026-07-01'),
        ai('Z', 'https://example.com/v2', '2026-07-08'),
        ai('K', 'https://example.com/v3', '2026-07-15'),
        ai('M', 'https://example.com/v4', '2026-07-22'),
        pay('N', 'https://example.com/v5', '2026-07-05'),
        pay('O', 'https://example.com/v6', '2026-07-12'),
        pay('P', 'https://example.com/v7', '2026-07-19'),
        pay('Q', 'https://example.com/v8', '2026-07-26'),
    ]
    report = build_period_report(may + jun + jul, '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    trend = next(t for t in report['period_themes'] if t['key'] == 'ai_infra')
    assert trend['baseline_count'] == 2
    assert trend['coverage_ratio'] >= 1.5          # 总量明显涨了
    assert trend['change'] == '延续'               # 校正后不误判升温


def test_monthly_preview_outputs_observation_summary():
    events = [
        event(url='https://example.com/c1', company_name='ExampleAI', companies=['ExampleAI'], date='2026-07-03'),
        event(url='https://example.com/c2', company_name='CloudBox', companies=['CloudBox'], date='2026-07-10'),
        event(url='https://example.com/c3', company_name='InfraCo', companies=['InfraCo'], date='2026-07-18'),
    ]
    report = build_period_report(events, '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'preview')
    assert '观察期' in report['summary']
    assert '本月主线是' not in report['summary']
    assert report['status_label'] == '观察中'
    assert report['period_themes']


def test_monthly_editorial_overrides_mainline_when_llm_succeeds():
    def fake_post_chat(api, prompt, **kw):
        keys = [m.group(1) for m in re.finditer(r'"key": "([^"]+)"', prompt)] or ['ai_infra']
        content = json.dumps({
            'editorial_title': '算力转向推理部署',
            'mainline': '本月AI基础设施资本转向推理与区域节点，支付行业同步进入商户入口争夺。',
            'themes': [
                {'key': k, 'narrative': f'{k}趋势本月向推理部署转向',
                 'drivers': ['区域数据中心扩张', '推理算力采购'],
                 'uncertainty': '部分扩张仍处规划阶段',
                 'next_validation': '观察数据中心是否进入运营披露'}
                for k in keys
            ],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=fake_post_chat)
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/c1', company_name='ExampleAI', companies=['ExampleAI'], date='2026-07-03'),
            event(url='https://example.com/c2', company_name='CloudBox', companies=['CloudBox'], date='2026-07-10'),
            event(url='https://example.com/c3', company_name='InfraCo', companies=['InfraCo'], date='2026-07-18'),
        ], '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    assert report['editorial_title'] == '算力转向推理部署'
    assert '本月AI基础设施资本转向' in report['summary']
    trend = report['period_themes'][0]
    assert trend.get('drivers') == ['区域数据中心扩张', '推理算力采购']
    assert trend.get('next_validation') == '观察数据中心是否进入运营披露'


def test_monthly_editorial_falls_back_to_template_when_llm_fails():
    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=Exception('boom'))
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/c1', company_name='ExampleAI', companies=['ExampleAI'], date='2026-07-03'),
            event(url='https://example.com/c2', company_name='CloudBox', companies=['CloudBox'], date='2026-07-10'),
            event(url='https://example.com/c3', company_name='InfraCo', companies=['InfraCo'], date='2026-07-18'),
        ], '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    assert '本周期从' in report['summary']
    assert not report['period_themes'][0].get('drivers')
    assert not report['editorial_title']


def test_weekly_theme_title_overrides_fixed_category_label():
    """反格式化：AI 返回具体 theme_title 时，主题标题不再是固定大类标签。"""
    def fake_post_chat(api, prompt, **kw):
        keys = [m.group(1) for m in re.finditer(r'"key": "([^"]+)"', prompt)] or ['ai_infra']
        content = json.dumps({
            'editorial_title': '算力走向区域部署',
            'mainline': '本周AI基础设施资本转向区域推理节点，支付进入商户入口争夺。',
            'themes': [{'key': k, 'theme_title': f'{k}区域推理部署加速', 'narrative': f'{k}主题叙事'} for k in keys],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=fake_post_chat)
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI']),
            event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox']),
        ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    assert report['focus_windows']
    window = report['focus_windows'][0]
    assert '区域推理部署加速' in window['title']
    assert window['title'] != 'AI与云基础设施'  # 不再是固定大类


def test_weekly_why_has_no_meta_boilerplate():
    """反格式化：主题 why 不再带'本周由 N 个独立事实支持'这类元描述套话。"""
    report = build_period_report([
        event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI'],
              reason='欧洲AI基础设施公司融资，云和数据中心出现预算窗口'),
        event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox'],
              reason='CloudBox扩张区域数据中心'),
    ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)

    assert report['focus_windows']
    window = report['focus_windows'][0]
    assert window['why']
    assert '由' not in window['why'] and '独立事实支持' not in window['why']


def test_weekly_editorial_fed_four_evidence_events():
    """反格式化：AI 编辑 prompt 应收到每条主题最多 4 条代表事件，而非仅 2 条。"""
    seen = {}

    def fake_post_chat(api, prompt, **kw):
        seen['prompt'] = prompt
        keys = [m.group(1) for m in re.finditer(r'"key": "([^"]+)"', prompt)] or ['ai_infra']
        content = json.dumps({
            'mainline': '本周主线叙事内容足够长以通过长度校验，继续输出主题导读。',
            'themes': [{'key': k, 'theme_title': '具体变化标题', 'narrative': '叙事'} for k in keys],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=fake_post_chat)
    ctx_api.start()
    ctx_llm.start()
    try:
        build_period_report([
            event(url='https://example.com/a', company_name='ExampleAI', companies=['ExampleAI'],
                  display_title='ExampleAI 获融资扩张欧洲推理集群', summary_short='A公司融资', reason='AI基础设施扩张'),
            event(url='https://example.com/b', company_name='CloudBox', companies=['CloudBox'],
                  display_title='CloudBox 启动中东区域数据中心', summary_short='B公司扩张', reason='数据中心布局'),
            event(url='https://example.com/c', company_name='NebulaAI', companies=['NebulaAI'],
                  display_title='NebulaAI 上线推理 API 服务', summary_short='C公司上线', reason='推理服务发布'),
            event(url='https://example.com/d', company_name='InfraCo', companies=['InfraCo'],
                  display_title='InfraCo 锁定推理 GPU 供应', summary_short='D公司采购', reason='GPU采购'),
        ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open', focus_windows_enabled=True)
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    # 4 条事件应进入 prompt（change_events 携带 display_title）
    assert '"change_events"' in seen['prompt']
    for ident in ('ExampleAI 获融资扩张欧洲推理集群', 'CloudBox 启动中东区域数据中心',
                  'NebulaAI 上线推理 API 服务', 'InfraCo 锁定推理 GPU 供应'):
        assert ident in seen['prompt']


def test_monthly_theme_title_overrides_fixed_category_label():
    """反格式化：月报趋势标题同样被具体变化标题覆盖。"""
    def fake_post_chat(api, prompt, **kw):
        keys = [m.group(1) for m in re.finditer(r'"key": "([^"]+)"', prompt)] or ['ai_infra']
        content = json.dumps({
            'editorial_title': '算力转向推理部署',
            'mainline': '本月AI基础设施资本转向推理与区域节点，支付行业同步进入商户入口争夺。',
            'themes': [
                {'key': k, 'theme_title': f'{k}推理部署转向',
                 'narrative': '叙事', 'drivers': ['区域数据中心扩张'], 'uncertainty': '部分扩张仍处规划',
                 'next_validation': '观察数据中心是否进入运营披露'}
                for k in keys
            ],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    ctx_api = mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis())
    ctx_llm = mock.patch('fetch_news._post_chat', side_effect=fake_post_chat)
    ctx_api.start()
    ctx_llm.start()
    try:
        report = build_period_report([
            event(url='https://example.com/c1', company_name='ExampleAI', companies=['ExampleAI'], date='2026-07-03'),
            event(url='https://example.com/c2', company_name='CloudBox', companies=['CloudBox'], date='2026-07-10'),
            event(url='https://example.com/c3', company_name='InfraCo', companies=['InfraCo'], date='2026-07-18'),
        ], '2026-07-01', '2026-07-31', '7 月报', '2026-07', 'mature')
    finally:
        ctx_llm.stop()
        ctx_api.stop()

    trend = report['period_themes'][0]
    assert '推理部署转向' in trend['title']
    assert trend['title'] != trend['key'] and '金融科技' not in trend['title']


def _minimal_themes():
    return [{'key': 'k1', 'direction': '方向一', 'region': '欧洲', 'evidence': [{'title': 't1'}]}]


def test_weekly_editorial_cache_hit_skips_llm():
    cached = {'editorial_title': '缓存标题', 'mainline': '这是缓存主线内容，长度足够用于校验。', 'themes': {'k1': '缓存导读'}, 'theme_titles': {'k1': '缓存主题标题'}}
    with mock.patch('generate_html._editorial_cache_get', return_value=(cached, cached)) as m_get, \
         mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis()), \
         mock.patch('fetch_news._post_chat') as m_post:
        result = build_weekly_editorial(_minimal_themes(), '2026-W23', cache_key='weekly:2026-W23')
    assert result == cached
    m_post.assert_not_called()
    assert m_get.call_args[0][0] == 'weekly:2026-W23'


def test_editorial_failure_falls_back_to_stale_cache():
    stale = {'editorial_title': '旧标题', 'mainline': '上一版主线，内容足够长可用于展示。', 'themes': {'k1': '旧导读'}, 'theme_titles': {}}

    def failing_post(api, prompt, **kw):
        raise RuntimeError('ReadTimeout')

    with mock.patch('generate_html._editorial_cache_get', return_value=(None, stale)), \
         mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis()), \
         mock.patch('fetch_news._post_chat', side_effect=failing_post):
        report = build_period_report([
            event(url='https://example.com/s1', company_name='ExampleAI', companies=['ExampleAI']),
            event(url='https://example.com/s2', company_name='CloudBox', companies=['CloudBox']),
        ], '2026-06-01', '2026-06-07', '2026年第23周', '2026-W23', 'open',
            focus_windows_enabled=True, require_editorial=True)
    assert report['editorial_title'] == '旧标题'


def test_editorial_regenerates_when_input_changes_and_updates_cache():
    stale = {'editorial_title': '旧标题', 'mainline': '旧版主线内容，将被新版本覆盖。', 'themes': {'k1': '旧导读'}, 'theme_titles': {}}

    def fake_post(api, prompt, **kw):
        content = json.dumps({
            'editorial_title': '新标题由AI重新生成',
            'mainline': '新的主线叙事：AI 基础设施投资与支付并购同期升温，值得持续关注。',
            'themes': [{'key': 'k1', 'theme_title': '新主题标题', 'narrative': '新导读'}],
        }, ensure_ascii=False)
        return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': content}}]})

    with mock.patch('generate_html._editorial_cache_get', return_value=(None, stale)), \
         mock.patch('fetch_news._chat_api_candidates', return_value=_fake_apis()), \
         mock.patch('fetch_news._post_chat', side_effect=fake_post), \
         mock.patch('generate_html._editorial_cache_put') as m_put:
        result = build_weekly_editorial(_minimal_themes(), '2026-W23', cache_key='weekly:2026-W23')
    assert result['editorial_title'] == '新标题由AI重新生成'
    m_put.assert_called_once()
    assert m_put.call_args[0][0] == 'weekly:2026-W23'


def test_editorial_input_hash_is_content_addressed():
    brief = [{'key': 'k1', 'title': '标题'}]
    base = _editorial_input_hash(brief)
    assert base == _editorial_input_hash([{'title': '标题', 'key': 'k1'}])
    assert base != _editorial_input_hash([{'key': 'k1', 'title': '标题2'}])
    with mock.patch.object(generate_html, 'EDITORIAL_PROMPT_VERSION', 2):
        assert base != _editorial_input_hash(brief)


if __name__ == '__main__':
    test_weekly_report_builds_focus_windows_from_repeated_signals()
    test_weekly_report_does_not_promote_single_event_to_focus_window()
    test_monthly_report_does_not_enable_weekly_focus_windows_by_default()
    test_monthly_trend_requires_cross_week_evidence_and_comparison()
    test_weekly_broad_window_keeps_out_of_scope_events_out()
    test_weekly_narrative_overrides_mainline_when_llm_succeeds()
    test_weekly_narrative_falls_back_to_template_when_llm_fails()
    test_weekly_editorial_failure_blocks_production_output()
    test_monthly_comparison_counts_atoms_in_both_periods()
    test_monthly_trend_requires_min_absolute_delta()
    test_monthly_preview_outputs_observation_summary()
    test_monthly_editorial_overrides_mainline_when_llm_succeeds()
    test_monthly_editorial_falls_back_to_template_when_llm_fails()
    test_weekly_theme_title_overrides_fixed_category_label()
    test_weekly_why_has_no_meta_boilerplate()
    test_weekly_editorial_fed_four_evidence_events()
    test_monthly_theme_title_overrides_fixed_category_label()
    test_weekly_editorial_cache_hit_skips_llm()
    test_editorial_failure_falls_back_to_stale_cache()
    test_editorial_regenerates_when_input_changes_and_updates_cache()
    test_editorial_input_hash_is_content_addressed()
    print('period report tests passed')
