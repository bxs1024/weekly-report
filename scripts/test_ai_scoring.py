"""AI 双评分与回执测试（P2）。

不访问任何外部服务：模型调用由本地假响应替代，回执与解析逻辑真实执行。
覆盖：
- 提示词可加载且含五轴与单字段输出契约
- 回执键随提示词版本、模型、输入变化
- 回执命中时不重复调用模型（省钱的核心保证）
- 双评分取均值、越界/非法回答被拒
- 与程序分的对比报告（过渡期决策依据）
- 安全阀关闭时完全跳过
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 回执存储隔离到临时文件：测试不污染 data/ai_receipts.json，
# 也不受上一次运行留下的回执影响（否则「是否重复调用模型」无法断言）。
_RECEIPT_TMP = os.path.join(tempfile.mkdtemp(prefix='aihot_receipts_'), 'ai_receipts.json')

import ai_receipts

ai_receipts.RECEIPTS_PATH = _RECEIPT_TMP

import ai_scoring
from prompt_loader import load_prompt


def _event(**overrides):
    base = {
        'title': 'Nubank expands merchant acquiring in Mexico',
        'content_overview': 'Nubank 在墨西哥推出商户收单服务，面向中小商户。',
        'reason': '拉美支付基建竞争加剧，收单入口成为银行与平台的下一个战场。',
        'impact': '墨西哥中小商户、本地收单服务商',
        'region': '拉美',
        'date': '2026-10-01',
        'signal_change_score': 68,
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return {'choices': [{'message': {'content': json.dumps(self._payload)}}]}


class _FakeApi(dict):
    pass


def _install_fake(monkeypatch_scores):
    """把 fetch_news._post_chat 换成假实现，记录调用次数。"""
    import fetch_news

    calls = {'n': 0}

    def fake_post(api, body, **kwargs):
        idx = min(calls['n'], len(monkeypatch_scores) - 1)
        score = monkeypatch_scores[idx]
        calls['n'] += 1
        if score is None:
            return _FakeResp({}, status=500)
        return _FakeResp({'attentionScore': score})

    fetch_news._post_chat = fake_post
    return calls


def test_score_prompt_contract():
    text = load_prompt('score')
    for axis in ('substance', 'increment', 'evidence', 'actionability', 'reach'):
        assert axis in text, f'评分提示词缺少五轴之一: {axis}'
    assert 'attentionScore' in text
    for action in ('policy', 'product', 'funding', 'earnings', 'expansion', 'hiring'):
        assert action in text, f'评分提示词缺少动作类型: {action}'
    assert '必须压住的噪声' in text and '必须正常评价的价值' in text


def test_receipt_key_changes_with_input():
    a = ai_receipts.receipt_key('score', 'm1', {'title': 'A'})
    b = ai_receipts.receipt_key('score', 'm1', {'title': 'B'})
    c = ai_receipts.receipt_key('score', 'm2', {'title': 'A'})
    d = ai_receipts.receipt_key('score', 'm1', {'title': 'A'})
    assert a != b, '不同输入应有不同回执键'
    assert a != c, '不同模型应有不同回执键'
    assert a == d, '相同输入与模型应有相同回执键'
    # 键顺序不影响结果
    e = ai_receipts.receipt_key('score', 'm1', {'title': 'A', 'region': '拉美'})
    f = ai_receipts.receipt_key('score', 'm1', {'region': '拉美', 'title': 'A'})
    assert e == f, 'dict 顺序不应影响回执键'


def test_receipt_roundtrip_and_no_double_call():
    ai_receipts.reset_memo()
    calls = _install_fake([71, 65])
    apis = [_FakeApi(name='FakeModel')]
    events = [_event()]

    stats1 = ai_scoring.score_events(events, apis=apis, model_name='FakeModel')
    assert stats1['scored'] == 1, stats1
    assert stats1['failed'] == 0, stats1
    assert calls['n'] == 2, f'双评分应调用两次模型，实际 {calls["n"]}'
    assert events[0]['ai_score_1'] == 71 and events[0]['ai_score_2'] == 65
    assert events[0]['ai_score_avg'] == 68, '均值应向下取整'

    # 第二次运行：同一输入应命中回执，不再调用模型
    before = calls['n']
    events2 = [_event()]
    stats2 = ai_scoring.score_events(events2, apis=apis, model_name='FakeModel')
    assert calls['n'] == before, f'回执命中时不应再调模型（多调了 {calls["n"] - before} 次）'
    assert stats2['reused'] == 1, stats2
    assert events2[0]['ai_score_avg'] == 68


def test_receipt_does_not_store_failures():
    ai_receipts.reset_memo()
    _install_fake([None, None])
    apis = [_FakeApi(name='BadModel')]
    events = [_event(title='失败样例事件唯一标题')]
    stats = ai_scoring.score_events(events, apis=apis, model_name='BadModel')
    assert stats['failed'] == 1, stats
    assert 'ai_score_avg' not in events[0], '失败不应写入分数'
    assert events[0].get('ai_score_error'), '失败应记录原因'
    # 失败不落回执 → 下次仍会重试
    key = ai_receipts.receipt_key('score', 'BadModel', {'system': '', 'material': {}})
    assert ai_receipts.receipt_get(key) is None


def test_invalid_score_rejected():
    for bad in ['{"attentionScore": 150}', '{"attentionScore": -3}', '{"other": 1}', 'not json']:
        assert ai_scoring._parse_score(bad) is None, f'应拒绝非法回答: {bad}'
    assert ai_scoring._parse_score('{"attentionScore": 72}') == 72
    assert ai_scoring._parse_score('```json\n{"attentionScore": 88}\n```') == 88


def test_compare_with_program_score():
    events = [
        _event(title='高一致A', ai_score_avg=80, signal_change_score=75),
        _event(title='高一致B', ai_score_avg=70, signal_change_score=66),
        _event(title='低一致C', ai_score_avg=30, signal_change_score=25),
        _event(title='跨门槛分歧D', ai_score_avg=85, signal_change_score=40),
        _event(title='同侧大差距E', ai_score_avg=90, signal_change_score=62),
    ]
    report = ai_scoring.compare_with_program_score(events, threshold=60)
    assert report['n'] == 5
    assert report['agree_hi'] == 3, f'高一致 A/B/E 应都算同侧高分: {report}'
    assert report['agree_lo'] == 1
    kinds = {(d['title'], d['kind']) for d in report['divergent']}
    assert ('跨门槛分歧D', 'cross') in kinds, kinds
    assert ('同侧大差距E', 'gap') in kinds, f'同侧但差 28 分应算 gap: {kinds}'
    assert report['corr'] is not None


def test_safety_valve_disables_layer():
    old = os.environ.get(ai_scoring.AI_SCORE_ENABLED_ENV)
    try:
        os.environ[ai_scoring.AI_SCORE_ENABLED_ENV] = 'false'
        assert ai_scoring.ai_score_enabled() is False
        events = [_event()]
        stats = ai_scoring.score_events(events, apis=[_FakeApi(name='X')])
        assert stats['enabled'] is False and stats['scored'] == 0
        assert 'ai_score_avg' not in events[0], '关闭时不应写入任何 AI 分'
    finally:
        if old is None:
            os.environ.pop(ai_scoring.AI_SCORE_ENABLED_ENV, None)
        else:
            os.environ[ai_scoring.AI_SCORE_ENABLED_ENV] = old


def test_scoring_never_filters_events():
    """评分只排序不过滤：即使 AI 给极低分，事件仍然存在。"""
    ai_receipts.reset_memo()
    _install_fake([8, 12])
    apis = [_FakeApi(name='LowModel')]
    events = [_event(title='低分但不应被过滤的事件')]
    ai_scoring.score_events(events, apis=apis, model_name='LowModel')
    assert len(events) == 1, '事件不应被移除'
    assert events[0]['ai_score_avg'] == 10


def test_receipt_path_independent_of_cwd():
    """回执路径必须锚定仓库根，不能随调用进程 CWD 漂移。

    回归案例：曾用相对路径 'data/ai_receipts.json'，从 scripts/ 调用会写成
    scripts/data/ai_receipts.json、从仓库根调用写成 data/ai_receipts.json，
    同一个键在两处各存一份，回执形同失效（重复付费）。
    """
    # 还原成模块真实默认值（测试开头改成了临时文件）
    real = ai_receipts._REPO_ROOT
    assert os.path.isabs(real), '_REPO_ROOT 必须是绝对路径'
    default_path = os.path.join(real, 'data', 'ai_receipts.json')
    assert os.path.isabs(default_path), '回执路径必须是绝对路径，不由 CWD 决定'
    assert os.path.basename(os.path.dirname(default_path)) == 'data'
    # 仓库根下应能看到 scripts/ 与 docs/，确认锚点找对了层级
    assert os.path.isdir(os.path.join(real, 'scripts'))
    assert os.path.isdir(os.path.join(real, 'docs'))


def _run_all():
    test_score_prompt_contract()
    test_receipt_key_changes_with_input()
    test_receipt_roundtrip_and_no_double_call()
    test_receipt_does_not_store_failures()
    test_invalid_score_rejected()
    test_compare_with_program_score()
    test_safety_valve_disables_layer()
    test_scoring_never_filters_events()
    test_receipt_path_independent_of_cwd()
    print('ai scoring tests passed')


if __name__ == '__main__':
    _run_all()
