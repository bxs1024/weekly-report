"""运行灯（红 / 黄）的判定与接线测试。

重点不是「现在没报警」，而是把故障态造出来，确认它真的会报。
两个反事实样本都来自真实事故：
- 11-13 方舟欠费：入库 35、打分 0 —— 必须只是黄灯，不能阻断（否则误伤降级保护）
- 采集有货但入库 0 —— 必须红灯，且 --strict 下退出码为 1
"""
import sys
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_data_health as cdh


def _run(raw, unique, added, scored=0, failed=0, day='2026-11-13'):
    return {
        'date': day,
        'collection': {'raw_count': raw, 'unique_count': unique},
        'storage': {'added_count': added},
        'ai_scoring': {'scored': scored, 'failed': failed},
    }


def test_healthy_run_has_no_lights():
    reds, yellows = cdh.build_run_lights([_run(156, 154, 21, 21, 0, '2026-11-28')],
                                         today=date(2026, 11, 28))
    assert reds == [], reds
    assert yellows == [], yellows


def test_counterfactual_collected_but_nothing_stored_is_red():
    """采集有货、入库 0：这是硬故障，必须红灯。"""
    reds, yellows = cdh.build_run_lights([_run(191, 187, 0, 0, 0, '2026-11-28')],
                                         today=date(2026, 11, 28))
    assert len(reds) == 1, reds
    assert '有货没出货' in reds[0], reds
    assert not any('样本缺失' in y for y in yellows), yellows


def test_eleven_thirteen_pattern_is_yellow_only():
    """11-13 真实数字：入库 35、打分 0 条全失败。只准黄灯。"""
    reds, yellows = cdh.build_run_lights([_run(191, 187, 35, 0, 34, '2026-11-13')],
                                         today=date(2026, 11, 13))
    assert reds == [], reds
    assert any('样本缺失' in y for y in yellows), yellows
    assert any('34 条失败' in y for y in yellows), yellows


def test_stale_run_is_yellow_only():
    reds, yellows = cdh.build_run_lights([_run(207, 200, 26, 26, 0, '2026-11-05')],
                                         today=date(2026, 11, 28))
    assert reds == [], reds
    assert any('采集可能停了' in y for y in yellows), yellows


def test_empty_runs_is_red():
    reds, _ = cdh.build_run_lights([], today=date(2026, 11, 28))
    assert len(reds) == 1 and '运行记录为空' in reds[0], reds


def test_bad_date_is_red():
    bad = _run(10, 10, 3, 3, 0, 'unknown')
    reds, _ = cdh.build_run_lights([bad], today=date(2026, 11, 28))
    assert any('无法解析' in r for r in reds), reds


def _main_with(reds, yellows):
    """把其它失败源全部清空，只看运行灯是否影响退出码。"""
    with mock.patch.object(cdh, 'check_run_lights', return_value=(reds, yellows)), \
         mock.patch.object(cdh, 'check_updates_log_sync', return_value=[]), \
         mock.patch.object(cdh, 'check_updates_log_freshness', return_value=[]), \
         mock.patch.object(cdh, 'run_rule_checks', return_value=([], [])), \
         mock.patch.object(cdh, 'collect_quick_failures', return_value=[]), \
         mock.patch.object(sys, 'argv', ['check_data_health.py', '--quick', '--strict']):
        return cdh.main()


def test_red_light_blocks_strict_exit():
    assert _main_with(['运行灯红灯：有货没出货'], []) == 1


def test_yellow_light_does_not_block_strict_exit():
    assert _main_with([], ['运行灯黄灯：样本缺失']) == 0


def test_real_metrics_have_no_red():
    """生产数据现状：不该有红灯（黄灯可以有，比如本地数据停滞）。"""
    reds, _ = cdh.check_run_lights()
    assert reds == [], reds


def test_patch_target_is_live():
    """确认 build_run_lights 真的被 check_run_lights 调用，补丁不会静默落空。"""
    with mock.patch.object(cdh, 'build_run_lights', side_effect=RuntimeError('probe')):
        try:
            cdh.check_run_lights()
        except RuntimeError as exc:
            assert str(exc) == 'probe'
        else:
            raise AssertionError('check_run_lights 没有调用 build_run_lights')


def _run_all():
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_') or not callable(fn):
            continue
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed += 1
            print(f'FAIL {name}: {exc}')
    print(f'{passed} passed, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
