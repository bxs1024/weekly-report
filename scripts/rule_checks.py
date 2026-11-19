"""阶段 0 四条不变规则的判定函数（生产模块，2026-11-09 接进产线）。

原先只活在 scripts/test_rule_checks.py 里：CI 只跑
``check_data_health.py --quick --strict``，从不调用它们。归组层空转 28 天
无人察觉的根因是「报警器没接线」，不是没响。现在四个纯函数搬到这里，
由 check_data_health.py 在 quick / full 两条路径之后无条件调用。

职责边界：本模块只做判定（输入字典 → 告警字符串列表），不读文件、不联网、
不写状态；证据采集在 check_data_health.collect_rule_evidence()。
采不到的证据一律记为「查不到」，不猜、不静默当作健康。
"""

MIN_HIT_RATE = 0.01
DEFAULT_DECAY_FAILURES = 7
DEFAULT_MAX_STALE_DAYS = 14


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
