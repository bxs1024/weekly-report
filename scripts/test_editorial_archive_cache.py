"""已封档周期的编辑层缓存行为测试。

背景（2026-10-07）：编辑层缓存的键是「周期 + 输入指纹 + 提示词版本」，输入指纹
随事件累计而漂移。旧实现在缓存未精确命中时会**直接重发付费请求**，于是一次渲染
为 18 个历史周 + 5 个历史月反复调 AI——实测本地一次渲染发起 7 次调用、占渲染总
耗时约 2/3；CI 上（更慢的 runner + 更大的数据）直接顶穿 60 分钟超时，渲染被砍掉，
而「提交数据」排在渲染之后 → 当天采集的数据全部丢失。

现在的规则：`status == 'closed'` 的周期只读缓存（旧版也接受），绝不发起付费调用；
完全没缓存时才退回一次 AI，避免整页生成失败。

顺带锁住一个顺序问题：原实现把「没有 AI 通道就返回 None」放在缓存查询之前，
AI 总闸一关（AI_CALLS_ENABLED=0）连已缓存周期也一并失败。现在缓存优先。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import editorial.editorial as ed
import providers.llm as llm


THEMES = [{
    'key': 'k1',
    'title': '主题一',
    'region': '东南亚',
    'objects': '对象',
    'why': '理由',
    'evidence': [{'title': '证据一'}],
    'change_brief': [{'title': '证据一', 'date': '2026-05-01', 'type': 'funding'}],
}]

CACHED_EDITORIAL = {
    'editorial_title': '旧版标题',
    'mainline': '这是上一版已生成的导读，长度足够通过调用方的完整性检查。',
    'themes': {'k1': '旧版主题导读'},
    'theme_titles': {'k1': '旧版主题名'},
}


class _TempCache:
    """把编辑层缓存重定向到临时文件，测试不碰真实 data/editorial_cache.json。"""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix='editorial_cache_test_')
        self.path = os.path.join(self.dir, 'editorial_cache.json')
        self._old = ed._editorial_cache_path
        ed._editorial_cache_path = lambda: self.path
        return self

    def __exit__(self, *exc):
        ed._editorial_cache_path = self._old
        return False

    def write(self, periods):
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'periods': periods}, handle, ensure_ascii=False)


def _patch_apis(fn):
    """顶开总闸的唯一决策点（providers.llm._chat_api_candidates）。"""
    old = llm._chat_api_candidates
    llm._chat_api_candidates = fn
    return old


def _patch_post_chat(fn):
    old = llm._post_chat
    llm._post_chat = fn
    return old


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return {'choices': [{'message': {'content': json.dumps(self._payload)}}]}


def test_closed_period_reuses_stale_cache_and_never_calls_ai():
    """封档周期 + 指纹已漂移（stale）→ 用旧版缓存，一次 AI 都不发。"""
    with _TempCache() as cache:
        cache.write({'weekly:2026-W19': {
            'input_hash': '已被历史事件漂移掉的旧指纹',
            'editorial': CACHED_EDITORIAL,
            'channel': 'deepseek',
            'generated_at': '2026-05-10 09:00',
        }})

        def _boom():
            raise AssertionError('已封档周期不得发起 AI 调用')

        old = _patch_apis(_boom)
        try:
            result = ed.build_weekly_editorial(
                THEMES, '2026-W19', cache_key='weekly:2026-W19', allow_ai=False)
        finally:
            _patch_apis(old)

    assert result == CACHED_EDITORIAL


def test_closed_period_without_any_cache_falls_back_to_ai():
    """封档周期但完全没缓存（首次运行/缓存被清）→ 不能让整页失败，退回探一次通道。"""
    with _TempCache() as cache:
        cache.write({})
        calls = []

        def _apis():
            calls.append(1)
            return []

        old = _patch_apis(_apis)
        try:
            result = ed.build_weekly_editorial(
                THEMES, '2026-W19', cache_key='weekly:2026-W19', allow_ai=False)
        finally:
            _patch_apis(old)

    assert calls == [1], '没有缓存时应退回一次 AI 通道探测'
    assert result is None, '无通道且无缓存时返回 None，由调用方决定是否降级'


def test_closed_monthly_reuses_stale_cache_and_never_calls_ai():
    """月报同规则。"""
    trends = [{'key': 'k1', 'title': '趋势一', 'evidence': [{'title': '证据一'}]}]
    with _TempCache() as cache:
        cache.write({'monthly:2026-05': {
            'input_hash': '旧指纹',
            'editorial': CACHED_EDITORIAL,
            'channel': 'deepseek',
            'generated_at': '2026-06-01 09:00',
        }})

        def _boom():
            raise AssertionError('已封档月份不得发起 AI 调用')

        old = _patch_apis(_boom)
        try:
            result = ed.build_monthly_editorial(
                trends, '2026-05', cache_key='monthly:2026-05', allow_ai=False)
        finally:
            _patch_apis(old)

    assert result == CACHED_EDITORIAL


def test_exact_cache_hit_works_even_when_ai_channel_is_empty():
    """回归：AI 总闸关闭（_chat_api_candidates 返回空）时，精确命中的缓存仍要可用。

    旧实现先判通道、后查缓存，导致 AI_CALLS_ENABLED=0 时连已生成的导读都取不到，
    整页生成直接抛「拒绝发布降级版」。
    """
    with _TempCache() as cache:
        cache.write({})
        generated = {'mainline': '首次生成的导读内容，长度足够通过完整性检查。',
                     'editorial_title': '标题',
                     'themes': [{'key': 'k1', 'narrative': '导读', 'theme_title': '主题名'}]}

        old_apis = _patch_apis(lambda: [{'id': 'deepseek', 'name': 'DeepSeek'}])
        old_post = _patch_post_chat(lambda api, prompt, **kw: _FakeResponse(generated))
        try:
            first = ed.build_weekly_editorial(
                THEMES, '2026-W36', cache_key='weekly:2026-W36', allow_ai=True)
        finally:
            _patch_apis(old_apis)
            _patch_post_chat(old_post)

        assert first and first['mainline'] == generated['mainline']

        # 第二次：通道为空（等价于 AI_CALLS_ENABLED=0），仍应命中缓存
        old_apis = _patch_apis(lambda: [])
        try:
            second = ed.build_weekly_editorial(
                THEMES, '2026-W36', cache_key='weekly:2026-W36', allow_ai=True)
        finally:
            _patch_apis(old_apis)

    assert second and second['mainline'] == generated['mainline']


def test_period_report_wires_allow_ai_to_closed_status():
    """接线检查：build_period_report 必须把 status=='closed' 翻译成 allow_ai=False。"""
    import inspect
    import reports.period as period

    src = inspect.getsource(period.build_period_report)
    assert "allow_ai = status != 'closed'" in src, '封档判定缺失，历史周期会重新发起付费调用'
    assert src.count('allow_ai=allow_ai') == 2, '周报/月报两条路径都要传 allow_ai'
