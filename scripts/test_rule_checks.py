"""阶段 0 四条不变规则的反事实验证（2026-11-09 起改为「验产线接线」）。

四个 check_* 纯函数已搬到 scripts/rule_checks.py，由 check_data_health.py
无条件调用（见 test_rule_checks_are_wired_into_health_check）。本文件继续用
正例 / 反事实例证锁住判定逻辑，并额外断言「造好了真的插上电」——
P2/P3 曾造好引擎却没接产线，文件清单全打勾也没发现。

反事实验证的含义：不只是看「现在没报警」，而是把输入换回故障态（部分用例
直接用 2026-11-05 线上真实指标），确认检查**真的会报**。绿灯本身不算证据。
"""

import os
import sys
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from rule_checks import (  # noqa: E402
    DEFAULT_DECAY_FAILURES,
    DEFAULT_MAX_STALE_DAYS,
    MIN_HIT_RATE,
    build_source_state,
    check_capability_validity,
    check_criterion_before_sort_change,
    check_latest_state_basis,
    check_source_decay,
)
import check_data_health as cdh  # noqa: E402

RULE_TITLES = (
    '自动能力必须配有效性校验',
    '「准」必须先定义再优化',
    '外部依赖配健康度衰减',
    '自动提交与告警判据基于最新状态',
)

# 2026-11-05 线上真实快照（来自 .data/online/run_metrics.json 与 events.json）：
#   归组 30 次运行 6000 次 AI 调用 / 28 次合并 = 0.47%
#   event_heat 92.5% 为 0，group_size 全为 1
#   7 个信源 30/30 次全失败（3 个 rss 源 + 4 个 html 源）
#   阶段 0 做真实数据验证时一度只喂 rss.source_stats，得出 3 个并误以为
#   体检数字错了；合并 rss + html 后复现为 7 个。教训：检查「接上了」
#   不等于「接全了」，聚合口径必须写死在代码里（见 build_source_state）。
#   collect 无「同步最新 main」；告警判据为「是否存在任意同名 Issue」
PRODUCTION_GROUPING = {
    'enabled': True, 'calls': 6000, 'hits': 28, 'values': [1] * 1134,
}
PRODUCTION_WORKFLOW = {
    'collect_syncs_latest': False,
    'commit_retry_on_reject': False,
    'alert_match': 'any_same_title',
    'alert_window_days': None,
}


# 四个判定函数已搬到 scripts/rule_checks.py（生产模块），本文件只留反事实例证。
# ---------------------------------------------------------------------------
# 反事实验证：正例 + 故障态（含线上真实状态）
# ---------------------------------------------------------------------------

def test_rule1_passes_on_healthy_capability():
    issues = check_capability_validity({
        'ai_scoring': {'enabled': True, 'calls': 662, 'hits': 662, 'values': [30, 61, 88]},
    })
    assert issues == [], issues


def test_rule1_reports_real_production_grouping_state():
    """反事实：喂 2026-11-05 线上真实指标，必须报命中率近零 + 全同。"""
    issues = check_capability_validity({'event_grouping': PRODUCTION_GROUPING})
    text = '\n'.join(issues)
    assert '命中率 0.47%' in text, issues
    assert '无区分度' in text, issues
    assert len(issues) == 2, issues


def test_rule1_detects_all_zero_all_same_and_no_calls():
    issues = check_capability_validity({
        'heat': {'enabled': True, 'calls': 100, 'hits': 100, 'values': [0, 0, 0]},
        'stuck': {'enabled': True, 'calls': 100, 'hits': 100, 'values': ['single'] * 5},
        'never_ran': {'enabled': True, 'calls': 0, 'hits': 0, 'values': []},
        'no_counters': {'enabled': True},
    })
    text = '\n'.join(issues)
    assert '全为 0' in text
    assert '无区分度' in text
    assert '这是「没跑」' in text
    assert '缺少调用计数' in text


def test_rule1_detects_dominant_single_value():
    """反事实：线上 event_heat 92.5% 为 0——既非全零也非全同，旧版检查会漏报。"""
    values = [0] * 925 + [i for i in range(1, 76)]
    issues = check_capability_validity({
        'event_heat': {'enabled': True, 'calls': 1000, 'hits': None, 'values': values},
    })
    assert any('92.5% 的取值都是 0' in x and '几乎无区分度' in x for x in issues), issues


def test_rule1_skips_explicitly_disabled_capability():
    """关停是明确的运维动作，不能和「静默空转」混为一谈。"""
    issues = check_capability_validity({
        'off': {'enabled': False, 'calls': 0, 'hits': 0, 'values': [0]},
    })
    assert issues == [], issues


def test_rule1_rejects_broken_counters():
    issues = check_capability_validity({
        'bad': {'enabled': True, 'calls': 10, 'hits': 99, 'values': [1, 2]},
    })
    assert any('统计口径坏了' in x for x in issues), issues


def test_rule2_passes_when_axis_untouched():
    """阶段 0/1 不动排序轴，不该被守门拦住。"""
    assert check_criterion_before_sort_change({
        'sort_axis_changed': False,
        'criterion_defined': False,
        'backtest_conclusion': False,
    }) == []


def test_rule2_blocks_axis_change_without_criterion_and_backtest():
    """反事实：这正是 2026-10-07 决策反馈里「C 方案停在纸面」若被硬推的样子。"""
    issues = check_criterion_before_sort_change({
        'sort_axis_changed': True, 'axis': 'AI 主判断',
        'criterion_defined': False, 'backtest_conclusion': False,
    })
    assert len(issues) == 2, issues
    assert any('什么叫准' in x for x in issues)
    assert any('回测结论' in x for x in issues)


def test_rule2_blocks_fixed_weights_even_with_backtest():
    issues = check_criterion_before_sort_change({
        'sort_axis_changed': True, 'criterion_defined': True,
        'backtest_conclusion': True, 'weights_fixed': True,
    })
    assert any('禁止设死权重' in x for x in issues), issues


def test_rule2_passes_only_with_full_evidence():
    assert check_criterion_before_sort_change({
        'sort_axis_changed': True, 'criterion_defined': True,
        'backtest_conclusion': True, 'weights_fixed': False,
    }) == []


def test_rule3_reports_consecutive_failed_sources():
    """反事实：模拟 7 个连续失败的源且仍在活跃列表 → 7 条降级告警。

    数字 7 与线上真实数量一致（3 个 rss 源 + 4 个 html 源，均为 30/30 失败），
    但检查能否看见这 7 个，取决于调用方有没有把两个分区都喂进来——
    所以 build_source_state() 把口径写死，见下一条测试。
    """
    sources = {
        f'dead_{i}': {'recent': [False] * 30, 'active': True, 'stale_days': 0}
        for i in range(7)
    }
    issues = check_source_decay(sources)
    assert len(issues) == 7, issues
    assert all('应自动降级' in x for x in issues), issues


def test_rule3_aggregation_must_cover_all_sections():
    """反事实：只喂 rss 会漏掉 html / company 的死源，口径必须由聚合函数固定。"""
    runs = [
        {
            'rss': {'source_stats': {'A': {'status': 'failed'}}},
            'html': {'source_stats': {'B': {'status': 'failed'}}},
            'company': {'source_stats': {'C': {'status': 'failed'}}},
        }
        for _ in range(7)
    ]
    state = build_source_state(runs)
    assert set(state) == {'rss:A', 'html:B', 'company:C'}, state
    assert len(check_source_decay(state)) == 3


def test_rule3_passes_after_demotion():
    sources = {'dead': {'recent': [False] * 30, 'active': False, 'stale_days': 0}}
    assert check_source_decay(sources) == []


def test_rule3_reports_stale_reuse():
    """模型榜 official_sources 停在 2026-09-07：不报错，但长期沿用旧值。"""
    issues = check_source_decay({
        'official_sources': {'recent': [True] * 30, 'active': True, 'stale_days': 59},
    })
    assert any('长期静默停更' in x for x in issues), issues


def test_rule3_does_not_flag_one_off_jitter():
    """临时抖动不该被判永久失效（与「空结果按失败处理」的分工）。"""
    sources = {'flaky': {'recent': [True] * 6 + [False], 'active': True, 'stale_days': 0}}
    assert check_source_decay(sources) == []


def test_rule4_reports_current_workflow_state():
    """反事实：collect 无同步 + 僵尸告警，必须各报一条。"""
    issues = check_latest_state_basis(PRODUCTION_WORKFLOW)
    assert len(issues) == 4, issues
    assert any('未同步最新 main' in x for x in issues)
    assert any('rebase 重试' in x for x in issues)
    assert any('僵尸 Issue' in x for x in issues)
    assert any('时间窗缺失或非法' in x for x in issues)


def test_rule4_passes_after_stage3_fix():
    assert check_latest_state_basis({
        'collect_syncs_latest': True,
        'commit_retry_on_reject': True,
        'alert_match': 'recent_window',
        'alert_window_days': 7,
    }) == []


def test_architecture_doc_declares_all_four_rules():
    """反向校验：规则是人工维护的文档，被覆盖/删掉时必须有人发现。"""
    path = os.path.join(ROOT, 'docs', 'ARCHITECTURE.md')
    with open(path, encoding='utf-8') as handle:
        content = handle.read()
    for title in RULE_TITLES:
        assert title in content, f'架构契约缺少规则: {title}'


def test_every_rule_has_an_executable_check():
    """自洽检查：4 条规则各有一个可调用的 check_* 纯函数。"""
    checks = (
        check_capability_validity,
        check_criterion_before_sort_change,
        check_source_decay,
        check_latest_state_basis,
    )
    assert len(checks) == len(RULE_TITLES)
    for fn in checks:
        assert callable(fn)
        assert fn.__name__.startswith('check_')


def test_rule_checks_are_wired_into_health_check():
    """接线断言：四个检查必须真的被产线调用，不能只是文件存在。"""
    source = Path(ROOT, 'scripts', 'check_data_health.py').read_text(encoding='utf-8')
    for name in ('check_capability_validity', 'check_criterion_before_sort_change',
                 'check_source_decay', 'check_latest_state_basis'):
        assert name in source, f'产线没有引用 {name}'
    assert 'run_rule_checks()' in source


def test_health_check_main_really_calls_rule_checks():
    """补丁命中验证：把 run_rule_checks 换成抛异常，跑 main() 必须真的炸。"""
    with mock.patch.object(cdh, 'run_rule_checks', side_effect=RuntimeError('probe')):
        with mock.patch.object(sys, 'argv', ['check_data_health.py', '--quick']):
            try:
                cdh.main()
            except RuntimeError as e:
                assert str(e) == 'probe'
            else:
                raise AssertionError('main() 没真的调用 run_rule_checks()')
