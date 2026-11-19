"""治理账本防「静默改小」的自动校验 + 反事实验证（2026-11-09）。

账本在 D:\\共享文件\\AI协作工作区\\02_进度同步\\，那个工作区不是 git 仓库；
基线放在本项目 data/（受 git 管控）。所以「被审计方写审计报告」不成立：
抹账本的人改不到基线，改基线会留在 git 历史里。

触发方式：本文件由 scripts/run_all_tests.sh 自动遍历，本机每次跑全量测试都会被查。
CI 不跑 run_all_tests.sh，且 CI 上没有那个工作区目录——那种情况按「查不到」
跳过，不误报。规格 C 的开工门禁仍未启用，所以这一步靠「开工跑测试」的约定触发，
不保证每次开工都跑（见 00_说明文档/人机协作治理契约.md 第九节）。
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from repo_paths import data_path  # noqa: E402

BASELINE_PATH = data_path('governance_ledger_baseline.json')
DEFAULT_LEDGER_PATH = 'D:/共享文件/AI协作工作区/02_进度同步/治理账本.md'

_BOUNDS = {
    'min_lines': ('lines', '总行数'),
    'min_unknown_mentions': ('unknown_mentions', '「不知道」出现次数'),
    'min_changelog_rows': ('changelog_rows', '留痕表行数'),
}


def ledger_path():
    return os.environ.get('GOVERNANCE_LEDGER_PATH') or DEFAULT_LEDGER_PATH


def _read_ledger_text(path):
    """返回 (正文或 None, 工作区目录是否存在)。

    两者分开：目录整个不存在 = 本机没有这个工作区（查不到）；
    目录在、文件没了 = 账本被删或改名（基线说见过 → 报警）。
    """
    target = Path(path)
    if not target.parent.is_dir():
        return None, False
    try:
        return target.read_text(encoding='utf-8'), True
    except OSError:
        return None, True


def collect_state(text):
    """从账本正文抽出可比对的结构化状态；只增不减是正常清算的方向。"""
    return {
        'lines': len(text.splitlines()),
        'unknown_mentions': text.count('不知道'),
        'changelog_rows': len(re.findall(r'^\|\s*20\d\d-', text, flags=re.M)),
        'stations': sorted(set(re.findall(r'\bW[1-5]\b', text))),
    }


def check_against_baseline(text, baseline, ledger_dir_exists=True):
    """返回 (failures, unknown)。查不到就写查不到，绝不静默当健康。"""
    failures, unknown = [], []
    if text is None:
        if not ledger_dir_exists:
            unknown.append('治理账本查不到：本机没有该工作区目录，本次未验证')
        elif baseline.get('ledger_seen'):
            failures.append('治理账本查不到：基线记录它存在过，但文件不见了（可能被删除或改名）')
        else:
            unknown.append('治理账本查不到：本机读不到，本次未验证')
        return failures, unknown

    state = collect_state(text)
    missing = [a for a in baseline.get('required_anchors', []) if a not in text]
    if missing:
        failures.append(
            f'治理账本被改小：缺少 {len(missing)} 处必需痕迹（{"、".join(missing[:3])}'
            f'{"…" if len(missing) > 3 else ""}）——上一轮结论可能被静默抹掉'
        )
    missing_stations = [
        s for s in baseline.get('required_stations', [])
        if s not in state['stations']
    ]
    if missing_stations:
        failures.append(f'治理账本被改小：工位登记缺 {"、".join(missing_stations)}')
    for key, (state_key, label) in _BOUNDS.items():
        floor = baseline.get(key)
        if floor is None:
            continue
        if state[state_key] < floor:
            failures.append(
                f'治理账本被改小：{label} {state[state_key]} < 基线 {floor}'
            )
    return failures, unknown


def recommend_baseline_update(baseline, state, reason):
    """只允许上调计数下限；required_anchors 一律原样保留。

    否则「抹掉痕迹 → 刷新基线」就能把证据一起洗白，这道校验等于没装。
    """
    updated = dict(baseline)
    for key, (state_key, _) in _BOUNDS.items():
        updated[key] = max(baseline.get(key, 0), state[state_key])
    updated['_last_update'] = {'date': date.today().isoformat(), 'reason': reason}
    return updated


def _cli(argv=None):
    parser = argparse.ArgumentParser(description='治理账本防静默改小校验')
    parser.add_argument('--update-baseline', action='store_true',
                        help='上调计数下限（required_anchors 不动），必须配 --reason')
    parser.add_argument('--reason', default='', help='本次上调的理由，会写进基线并在 git 留痕')
    parser.add_argument('--ledger', default=None, help='覆盖账本路径')
    args = parser.parse_args(argv)

    baseline = json.loads(Path(BASELINE_PATH).read_text(encoding='utf-8'))
    text, dir_exists = _read_ledger_text(args.ledger or ledger_path())

    if args.update_baseline:
        if not args.reason or text is None:
            print('ERROR: 上调基线必须给 --reason，且账本要读得到')
            return 2
        updated = recommend_baseline_update(baseline, collect_state(text), args.reason)
        Path(BASELINE_PATH).write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        print('OK | 计数下限已上调；required_anchors 原样保留。请在 git commit 里写明理由。')
        return 0

    failures, unknown = check_against_baseline(text, baseline, dir_exists)
    for item in unknown:
        print(f'UNKNOWN: {item}')
    for item in failures:
        print(f'WARNING: {item}')
    if not failures and not unknown:
        print('OK | 治理账本与基线一致')
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# 反事实验证
# ---------------------------------------------------------------------------

def _baseline():
    return json.loads(Path(BASELINE_PATH).read_text(encoding='utf-8'))


def _real_ledger():
    return _read_ledger_text(ledger_path())


def test_current_ledger_matches_baseline():
    text, dir_exists = _real_ledger()
    failures, unknown = check_against_baseline(text, _baseline(), dir_exists)
    if unknown:
        return  # 本机没有工作区目录：查不到，不下结论
    assert failures == [], failures


def test_counterfactual_erasing_unknown_items_is_detected():
    """11-09 故障注入的真故障态：抹掉 3 处「不知道」，必须报。"""
    text, _ = _real_ledger()
    if text is None:
        return
    erased = text
    for anchor in (
        'W4 那份快照当初为谁留的',
        '点名之后人工那一笔怎么自动留痕',
        '开工门禁在这套工具里能做到多硬',
    ):
        erased = erased.replace(anchor, '')
    failures, _ = check_against_baseline(erased, _baseline())
    assert failures, '故障注入失效：抹掉 3 处「不知道」仍然放行'
    assert any('缺少' in x or '被改小' in x for x in failures), failures


def test_counterfactual_deleting_changelog_row_is_detected():
    text, _ = _real_ledger()
    if text is None:
        return
    lines = text.splitlines()
    kept = [line for line in lines if not re.match(r'^\|\s*2026-11-09 09:08', line)]
    assert len(kept) < len(lines)
    failures, _ = check_against_baseline('\n'.join(kept), _baseline())
    assert any('留痕表行数' in x or '缺少' in x for x in failures), failures


def test_missing_ledger_is_detected_when_baseline_says_it_existed():
    failures, unknown = check_against_baseline(None, _baseline(), ledger_dir_exists=True)
    assert any('文件不见了' in x for x in failures), failures
    assert unknown == []


def test_absent_workspace_is_unknown_not_failure():
    failures, unknown = check_against_baseline(None, _baseline(), ledger_dir_exists=False)
    assert failures == []
    assert unknown and '查不到' in unknown[0]


def test_baseline_update_cannot_wash_away_anchors():
    """防止「抹掉痕迹 → 刷新基线」把证据一起洗白。"""
    text, _ = _real_ledger()
    baseline = _baseline()
    if text is None:
        text = '占位文本'
    erased = text.replace('W4 那份快照当初为谁留的', '')
    updated = recommend_baseline_update(baseline, collect_state(erased), '测试')
    assert updated['required_anchors'] == baseline['required_anchors']
    assert check_against_baseline(erased, updated)[0], '刷新基线后抹痕仍必须报'


def test_baseline_file_is_in_repo_and_not_empty():
    assert Path(BASELINE_PATH).exists()
    baseline = _baseline()
    assert baseline.get('required_anchors')
    assert baseline.get('ledger_seen') is True


def test_patch_target_is_live():
    """补丁命中验证：patch 落空时测试仍会 PASS，但实际在跑真实实现。"""
    with mock.patch.object(sys.modules[__name__], 'collect_state',
                           side_effect=RuntimeError('probe')):
        try:
            check_against_baseline('任意正文', _baseline())
        except RuntimeError as e:
            assert str(e) == 'probe'
        else:
            raise AssertionError('补丁没命中：check_against_baseline 没调用 collect_state')


if __name__ == '__main__':
    sys.exit(_cli())
