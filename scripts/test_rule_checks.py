"""阶段 0 四条不变规则的可执行规格（2026-11-05）。

这不是普通回归测试：它把 docs/ARCHITECTURE.md 末尾新增的 4 条规则，翻译成
4 个纯函数 + 正例/反事实验证。刻意放在 ``test_*.py`` 里，原因有三：

1. 阶段 0 只定规则与规格，**不接 CI、不改 workflow、不重渲染页面**；
2. run_all_tests.sh 会跑本文件，规则因此「现在就有牙齿」，不是纸面条款；
3. 阶段 3 把 4 个 ``check_*`` 函数搬进 check_data_health.py 接产线即可复用。

反事实验证的含义：不只是看「现在没报警」，而是把输入换回故障态（部分用例
直接用 2026-11-05 线上真实指标），确认检查**真的会报**。绿灯本身不算证据。

四个函数都是纯函数：输入字典，返回告警字符串列表，不读文件、不联网、不写状态。
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MIN_HIT_RATE = 0.01
DEFAULT_DECAY_FAILURES = 7
DEFAULT_MAX_STALE_DAYS = 14

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
#   不等于「接全了」，聚合口径必须写死在代码里（见 collect_source_runs）。
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


# ---------------------------------------------------------------------------
# 规则 1：自动能力必须配有效性校验
# ---------------------------------------------------------------------------

def check_capability_validity(capabilities, min_hit_rate=MIN_HIT_RATE):
    """检测能力空转：全零 / 全同 / 命中率近零 / 声明启用但没跑。

    capabilities: {name: {enabled, calls, hits, values}}
      enabled — 能力是否声明启用（明确关停的不算空转）
      calls   — 本周期真的发起的判定次数（None 表示没埋点）
      hits    — 其中真正产出了变化的次数
      values  — 该能力写进数据的字段取值样本（用于查全零/全同）
    """
    issues = []
    for name, cap in sorted(capabilities.items()):
        if not cap.get('enabled', True):
            continue
        calls = cap.get('calls')
        hits = cap.get('hits')
        values = cap.get('values')

        if calls is None:
            issues.append(f'{name}: 缺少调用计数，无法判断「没跑」还是「跑了没结果」（先补埋点）')
            continue
        if calls == 0:
            issues.append(f'{name}: 声明启用但 0 次调用（这是「没跑」，先查总闸/密钥，别去调提示词）')
            continue
        if hits is not None and hits > calls:
            issues.append(f'{name}: 命中数 {hits} 大于调用数 {calls}，统计口径坏了，结论不可用')
        if values is not None:
            if not values:
                issues.append(f'{name}: 字段取值样本为空，有效性无从验证')
            elif all(v == 0 for v in values):
                issues.append(f'{name}: 字段取值全为 0（写了但没产出）')
            else:
                # 只认「全零 / 全同」会漏掉最常见的真实故障态：92.5% 为零、
                # 7.5% 有值。它既不是全零也不是全同，但同样几乎没有区分度。
                counts = {}
                for v in values:
                    key = repr(v)
                    counts[key] = counts.get(key, 0) + 1
                top_value, top_count = max(counts.items(), key=lambda kv: kv[1])
                ratio = top_count / len(values)
                if top_count == len(values):
                    issues.append(f'{name}: 字段取值全同（{top_value}），无区分度')
                elif ratio >= 0.9 and len(values) >= 20:
                    issues.append(
                        f'{name}: {ratio:.1%} 的取值都是 {top_value}（{top_count}/{len(values)}），'
                        '几乎无区分度'
                    )
        if hits is not None:
            rate = hits / calls
            if rate < min_hit_rate:
                issues.append(
                    f'{name}: 命中率 {rate:.2%} 低于 {min_hit_rate:.2%}（{hits}/{calls}）——'
                    '先验证该能力解决的痛点是否还存在，不要直接调阈值'
                )
    return issues


# ---------------------------------------------------------------------------
# 规则 2：「准」必须先定义再优化
# ---------------------------------------------------------------------------

def check_criterion_before_sort_change(state):
    """改排序 / 筛选轴之前，必须先有可观测判据与回测结论，且禁止拍死权重。

    state: {sort_axis_changed, criterion_defined, backtest_conclusion,
            weights_fixed, axis}
    """
    issues = []
    if not state.get('sort_axis_changed'):
        return issues
    axis = state.get('axis') or '排序轴'
    if state.get('weights_fixed'):
        issues.append(f'{axis}: 禁止设死权重（把「选哪套分」换成「拍比例」，仍是未验证判断）')
    if not state.get('criterion_defined'):
        issues.append(f'{axis}: 改排序轴前必须先定义「什么叫准」的可观测判据')
    if not state.get('backtest_conclusion'):
        issues.append(f'{axis}: 改排序轴前必须有离线回测结论，否则改完分不清「变好」和「变了」')
    return issues


# ---------------------------------------------------------------------------
# 规则 3：外部依赖配健康度衰减
# ---------------------------------------------------------------------------

def build_source_state(runs, consecutive_failures=DEFAULT_DECAY_FAILURES):
    """run_metrics 的多次运行 → check_source_decay 的输入。

    聚合口径必须写死在这里，不能交给调用方「记得喂哪些字段」：信源分布在
    rss / html / company 三个 section，只喂 rss 会漏掉全部 html 源。
    2026-11-05 阶段 0 做真实数据验证时踩过一次——只统计 rss 得出 3 个死源，
    一度误判体检报告的「7 个」是统计错误；合并 rss + html 后复现为 7 个
    （3 rss + 4 html）。**检查接上了，不等于接全了。**

    status 是三态：ok（抓到）/ empty（页面正常但无新条目）/ failed（真失败）。
    **只有 failed 算失败**——把 empty 一并当失败，会把大量正常但暂时没更新的
    页面误判成死源（阶段 0 实测报出 33 个，其中 26 个是 empty）。
    「空结果按失败处理」那条规则针对的是「抓取/解析返回 0 条」的自检，
    与这里「页面可访问但当天没有新稿」是两回事，不要混用。

    本次运行未出现的源不记入该次，序列可能短于运行次数；check_source_decay
    只在末尾序列够长时才判连续失败，不会把「没跑」误报成「连续失败」。
    """
    by_source = {}
    for run in runs:
        for section in ('rss', 'html', 'company'):
            stats = ((run.get(section) or {}).get('source_stats') or {})
            for name, stat in stats.items():
                key = f'{section}:{name}'
                by_source.setdefault(key, []).append(stat.get('status') != 'failed')
    return {
        key: {'recent': seq[-consecutive_failures:], 'active': True, 'stale_days': 0}
        for key, seq in by_source.items()
    }


def check_source_decay(sources, consecutive_failures=DEFAULT_DECAY_FAILURES,
                       max_stale_days=DEFAULT_MAX_STALE_DAYS):
    """连续失败且仍在活跃列表 → 应降级；长期不刷新 → 疑似沿用旧值。

    sources: {name: {recent: [bool] (最新在末尾), active: bool, stale_days: int}}
    """
    issues = []
    for name, src in sorted(sources.items()):
        recent = src.get('recent') or []
        tail = recent[-consecutive_failures:] if consecutive_failures > 0 else []
        if len(tail) == consecutive_failures and not any(tail):
            if src.get('active', True):
                issues.append(
                    f'{name}: 最近 {consecutive_failures} 次连续失败且仍在活跃列表，应自动降级并移出'
                )
        stale = src.get('stale_days')
        if stale is not None and stale > max_stale_days:
            issues.append(
                f'{name}: 数据已 {stale} 天未刷新（上限 {max_stale_days} 天）——'
                '「保留旧值不覆盖」只防清空，不防长期静默停更'
            )
    return issues


# ---------------------------------------------------------------------------
# 规则 4：自动提交与告警判据基于最新状态
# ---------------------------------------------------------------------------

def check_latest_state_basis(state):
    """提交必须现取远端状态并可重试；告警判据必须带时间窗。

    state: {collect_syncs_latest, commit_retry_on_reject,
            alert_match, alert_window_days}
    """
    issues = []
    if not state.get('collect_syncs_latest'):
        issues.append('collect: 提交前未同步最新 main，并发推送时采集结果会被非快进拒绝丢弃')
    if not state.get('commit_retry_on_reject'):
        issues.append('collect: 推送被拒后没有 rebase 重试，当天数据可能整批丢失')
    if state.get('alert_match') != 'recent_window':
        issues.append(
            f'告警: 判据为 {state.get("alert_match")!r}，用「是否存在任意同名告警」'
            '会被历史僵尸 Issue 永久压住，必须改为「只认最近 N 天」'
        )
    window = state.get('alert_window_days')
    if not isinstance(window, int) or window <= 0 or window > 30:
        issues.append(f'告警: 时间窗缺失或非法（{window!r}），应为一个 1–30 天的正整数')
    return issues


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
    所以 collect_source_runs() 把口径写死，见下一条测试。
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
