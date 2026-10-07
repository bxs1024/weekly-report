"""事件四关系归组与热度测试（P3）。

不访问外部服务：AI 判断用本地假响应替代。
覆盖：
- 提示词含四种关系定义与本站「拿不准不合并」口径
- 规则层指纹合并（含主类型漂移仲裁）
- AI 层 SAME_OCCURRENCE / SAME_STORY 合并、UNRELATED / ROUNDUP 不合并
- 置信度不足时不合并（本站保守口径）
- 历史真实漏并案例回归（Starcloud / Kakao 风格的措辞差异）
- 热度按独立来源计数、同一来源多篇只算一次、时间衰减
- 安全阀关闭时行为不变
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_RECEIPT_TMP = os.path.join(tempfile.mkdtemp(prefix='aihot_group_'), 'ai_receipts.json')
import ai_receipts

ai_receipts.RECEIPTS_PATH = _RECEIPT_TMP

import event_grouping
from prompt_loader import load_prompt


def _event(**overrides):
    base = {
        'event_id': 'e1',
        'title': 'Starcloud Raises $250 Million To Build AI Data Centers in Space',
        'content_overview': 'Starcloud 完成 2.5 亿美元融资，用于建设太空 AI 数据中心。',
        'source': 'Ventureburn',
        'date': '2026-08-22',
        'event_types': ['funding'],
        'canonical_company': 'Starcloud',
        'canonical_key': '250m',
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return {'choices': [{'message': {'content': json.dumps(self._payload)}}]}


_REAL_CANDIDATES = None


def _install_fake(verdicts):
    """把 AI 传输层与通道查询都换成假的，让归组逻辑可完全离线验证。

    通道查询也要换：`_chat_api_candidates()` 是总闸 AI_CALLS_ENABLED 的唯一
    决策点，运行者若在环境里设了 AI_CALLS_ENABLED=0（跑测试省钱是常规做法），
    它会返回空列表，AI 合并这条路就静默失效——测试结果会随环境变量变红变绿。
    本测试用的是假响应，不产生任何付费请求，所以直接把通道顶开。
    """
    global _REAL_CANDIDATES
    from providers import llm

    if _REAL_CANDIDATES is None:
        _REAL_CANDIDATES = llm._chat_api_candidates

    calls = {'n': 0}

    def fake_post(api, body, **kwargs):
        idx = min(calls['n'], len(verdicts) - 1)
        calls['n'] += 1
        return _FakeResp(verdicts[idx])

    llm._post_chat = fake_post
    llm._chat_api_candidates = lambda: [{'id': 'fake', 'name': 'FakeModel',
                                         'key': 'test', 'model': 'FakeModel',
                                         'url': 'http://localhost/fake'}]
    return calls


def _remove_fake():
    """还原通道查询，避免假通道漏到同一进程里的其它测试。"""
    if _REAL_CANDIDATES is None:
        return
    from providers import llm
    llm._chat_api_candidates = _REAL_CANDIDATES


def test_group_prompt_contract():
    text = load_prompt('group-pair')
    for rel in ('SAME_OCCURRENCE', 'SAME_STORY', 'UNRELATED', 'ROUNDUP'):
        assert rel in text, f'归组提示词缺少关系定义: {rel}'
    assert '拿不准' in text and 'UNRELATED' in text, '应写明本站保守口径'
    assert '{{> group-definitions}}' not in text or 'SAME_OCCURRENCE' in text


def test_rule_fingerprint_merges_same_event():
    a = _event()
    b = _event(event_id='e2', title='Space Data Centers: Starcloud Lands $250M Round',
               source='TechCrunch')
    assert event_grouping.rule_merge(a, b) is True, '指纹两项全匹配应合并'


def test_rule_type_drift_arbitrated_by_title():
    a = _event()
    b = _event(event_id='e2', event_types=['strategy'],
               title='Starcloud Raises $250 Million To Build AI Data Centers in Space',
               source='X')
    assert event_grouping.rule_merge(a, b) is True, '主类型漂移但标题一致应合并'

    c = _event(event_id='e3', event_types=['strategy'],
               title='Completely Unrelated Headline About Weather Patterns',
               canonical_company='OtherCo', canonical_key='1b')
    assert event_grouping.rule_merge(a, c) is None or event_grouping.rule_merge(a, c) is False


def test_should_group_conservative():
    """本站口径：拿不准不合并；置信度不足不合并。"""
    assert event_grouping.should_group(
        {'relation': 'SAME_OCCURRENCE', 'confidence': 0.9}) is True
    assert event_grouping.should_group(
        {'relation': 'SAME_STORY', 'confidence': 0.8}) is True, '进展应挂同一事件'
    assert event_grouping.should_group(
        {'relation': 'SAME_OCCURRENCE', 'confidence': 0.5}) is False, '低置信不应合并'
    assert event_grouping.should_group(
        {'relation': 'UNRELATED', 'confidence': 0.99}) is False
    assert event_grouping.should_group(
        {'relation': 'ROUNDUP', 'confidence': 0.99}) is False
    assert event_grouping.should_group(None) is False


def test_parse_relation_rejects_garbage():
    assert event_grouping.parse_relation('not json') is None
    assert event_grouping.parse_relation('{"relation":"MAYBE","confidence":0.9}') is None
    ok = event_grouping.parse_relation('```json\n{"relation":"same_story","confidence":0.82,"difference":"后续上架"}\n```')
    assert ok and ok['relation'] == 'SAME_STORY' and abs(ok['confidence'] - 0.82) < 1e-6


def test_assign_groups_ai_merges_followup():
    ai_receipts.reset_memo()
    _install_fake([{'relation': 'SAME_STORY', 'confidence': 0.88,
                    'a': 'A 发布产品', 'b': 'B 报道该产品上架第三方平台'}])
    try:
        events = [
            _event(event_id='p1', title='Acme Launches Pay Widget',
                   canonical_company='Acme', canonical_key='', date='2026-08-01',
                   event_types=['strategy']),
            _event(event_id='p2', title='Acme Pay Widget Arrives On Partner Platform',
                   canonical_company='', canonical_key='', date='2026-08-03',
                   source='OtherMedia', event_types=['strategy']),
        ]
        stats = event_grouping.assign_groups(events, model_name='FakeModel')
        assert stats['ai_merges'] == 1, stats
        assert stats['followups'] == 1, 'SAME_STORY 应标为 followup'
        assert events[0]['group_id'] == events[1]['group_id'], '进展应挂同一事件'
        assert events[1]['group_role'] == 'followup'
    finally:
        _remove_fake()


def test_assign_groups_unrelated_not_merged():
    ai_receipts.reset_memo()
    _install_fake([{'relation': 'UNRELATED', 'confidence': 0.95,
                    'difference': '不同公司不同产品'}])
    try:
        events = [
            _event(event_id='u1', title='Acme Launches Pay Widget',
                   canonical_company='Acme', canonical_key='', date='2026-08-01',
                   event_types=['strategy']),
            _event(event_id='u2', title='Beta Opens New Office In Jakarta',
                   canonical_company='Beta', canonical_key='', date='2026-08-02',
                   source='OtherMedia', event_types=['strategy']),
        ]
        stats = event_grouping.assign_groups(events, model_name='FakeModel')
        assert stats['ai_merges'] == 0, stats
        assert events[0]['group_id'] != events[1]['group_id'], '不同事不应合并'
    finally:
        _remove_fake()


def test_no_channel_reports_zero_ai_calls():
    """没通道时必须报「没跑」，而不是「跑了没并」。

    这条锁的是 ai_calls 的口径：它应该数「真的发起的 AI 判定」，
    而不是「想判定的对数」。否则日志里 ai_calls=37 / ai_merges=0 会
    把人引向「提示词不行」，真实原因却是 key 没配或总闸被关。
    """
    _remove_fake()
    old = os.environ.get('AI_CALLS_ENABLED')
    try:
        os.environ['AI_CALLS_ENABLED'] = '0'
        events = [
            _event(event_id='n1', title='Acme Launches Pay Widget',
                   canonical_company='Acme', canonical_key='', date='2026-08-01',
                   event_types=['strategy']),
            _event(event_id='n2', title='Acme Pay Widget Arrives On Partner Platform',
                   canonical_company='', canonical_key='', date='2026-08-03',
                   source='OtherMedia', event_types=['strategy']),
        ]
        stats = event_grouping.assign_groups(events, model_name='FakeModel')
        assert stats['ai_channel'] is False, stats
        assert stats['ai_calls'] == 0, stats
        assert stats['ai_merges'] == 0, stats
    finally:
        if old is None:
            os.environ.pop('AI_CALLS_ENABLED', None)
        else:
            os.environ['AI_CALLS_ENABLED'] = old


def test_safety_valve_disables_ai_grouping():
    old = os.environ.get(event_grouping.GROUP_ENABLED_ENV)
    try:
        os.environ[event_grouping.GROUP_ENABLED_ENV] = 'false'
        assert event_grouping.group_enabled() is False
        events = [
            _event(event_id='v1', title='Acme Launches Pay Widget',
                   canonical_company='Acme', canonical_key='', date='2026-08-01',
                   event_types=['strategy']),
            _event(event_id='v2', title='Acme Pay Widget Arrives On Partner Platform',
                   canonical_company='', canonical_key='', date='2026-08-03',
                   source='OtherMedia', event_types=['strategy']),
        ]
        stats = event_grouping.assign_groups(events, model_name='FakeModel')
        assert stats['ai_merges'] == 0 and stats['enabled'] is False
    finally:
        if old is None:
            os.environ.pop(event_grouping.GROUP_ENABLED_ENV, None)
        else:
            os.environ[event_grouping.GROUP_ENABLED_ENV] = old


def test_heat_counts_independent_sources_once():
    """热度按事件算：同一家媒体发十篇只算一次来源。"""
    now = datetime(2026, 8, 22, 12, 0, 0)
    made = now - timedelta(hours=6)
    date_str = made.strftime('%Y-%m-%d')
    events = [
        _event(event_id='h1', group_id='G1', source='媒体A', date=date_str),
        _event(event_id='h2', group_id='G1', source='媒体A', date=date_str),
        _event(event_id='h3', group_id='G1', source='媒体B', date=date_str),
        _event(event_id='h4', group_id='G1', source='媒体C', date=date_str),
    ]
    heat = event_grouping.compute_heat(events, now=now)
    assert heat['G1']['sources'] == 3, f'独立来源应为 3，实际 {heat["G1"]}'
    assert heat['G1']['heat'] == 3.0, f'6 小时内每源 1.0 分: {heat["G1"]}'
    assert heat['G1']['size'] == 4, '事件规模应记全部报道数'


def test_heat_decays_after_24h():
    now = datetime(2026, 8, 22, 12, 0, 0)
    fresh = (now - timedelta(hours=5)).strftime('%Y-%m-%d')
    old = (now - timedelta(hours=30)).strftime('%Y-%m-%d')
    events = [
        _event(event_id='d1', group_id='BA', source='A', date=fresh),
        _event(event_id='d2', group_id='BA', source='B', date=old),
    ]
    heat = event_grouping.compute_heat(events, now=now)
    assert abs(heat['BA']['heat'] - 1.5) < 1e-6, f'1.0 + 0.5 = 1.5: {heat["BA"]}'


def test_heat_window_excludes_stale():
    now = datetime(2026, 8, 22, 12, 0, 0)
    stale = (now - timedelta(hours=60)).strftime('%Y-%m-%d')
    events = [_event(event_id='s1', group_id='OLD', source='A', date=stale)]
    heat = event_grouping.compute_heat(events, now=now)
    assert heat['OLD']['heat'] == 0.0, '48h 窗口外的报道不计热度'
    assert heat['OLD']['size'] == 1, '但仍计入事件规模'


def test_heat_rank_orders_by_heat():
    events = [
        _event(event_id='r1', group_id='A', event_heat=1.0, event_source_count=1, date='2026-08-01'),
        _event(event_id='r2', group_id='B', event_heat=3.0, event_source_count=3, date='2026-08-01'),
    ]
    ranked = event_grouping.heat_rank(events)
    assert ranked[0]['group_id'] == 'B', '热度高的排前'


def _run_all():
    test_group_prompt_contract()
    test_rule_fingerprint_merges_same_event()
    test_rule_type_drift_arbitrated_by_title()
    test_should_group_conservative()
    test_parse_relation_rejects_garbage()
    test_assign_groups_ai_merges_followup()
    test_assign_groups_unrelated_not_merged()
    test_no_channel_reports_zero_ai_calls()
    test_safety_valve_disables_ai_grouping()
    test_heat_counts_independent_sources_once()
    test_heat_decays_after_24h()
    test_heat_window_excludes_stale()
    test_heat_rank_orders_by_heat()
    print('event grouping tests passed')


if __name__ == '__main__':
    _run_all()
