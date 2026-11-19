"""更新日志整块对账的反事实验证（2026-11-09）。

旧版 check_updates_log_sync() 只查「最新版本号字符串在不在 index.html」，
故障注入抓到两个真实漏报：
  ① 页面更新日志整块删光、别处残留 V6.5 字样 → 不报；
  ② site_updates.json 回滚成旧版、页面仍是新版 → 不报。
这里把两种故障态重新喂进来，确认新版**真的会报**；绿灯本身不算证据。
所有用例都写临时文件，不碰生产 data/ 与 docs/。
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_data_health as cdh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _logs(rows):
    return [{'date': d, 'version': v, 'type': '质量收敛', 'status': '已完成',
             'title': 't', 'summary': '', 'changes': []} for v, d in rows]


def _page(rows, count=None, keep_panel=True, stray_version=''):
    cards = ''.join(
        '<div class="u-card"><button class="u-head" type="button">'
        '<span class="u-copy"><h3>t</h3>'
        f'<small>{d} · 质量收敛 · 已完成</small></span>'
        f'<span class="u-ver">{v}<span class="u-caret">›</span></span>'
        '</button></div>'
        for v, d in rows
    )
    panel = (
        '<section class="proto-panel" data-panel="updates">'
        f'<span class="m-head-meta">{count if count is not None else len(rows)} 条记录</span>'
        f'<div class="u-list">{cards}</div>'
        '</section>'
    ) if keep_panel else '<div>更新日志整块被删了</div>'
    return (
        '<html><body><section class="proto-panel" data-panel="today">'
        f'{stray_version}</section>{panel}'
        '<section class="proto-panel" data-panel="models">models</section>'
        '</body></html>'
    )


def _write_pair(dirname, rows, **page_kwargs):
    updates = os.path.join(dirname, 'site_updates.json')
    html = os.path.join(dirname, 'index.html')
    with open(updates, 'w', encoding='utf-8') as f:
        json.dump(_logs(rows), f, ensure_ascii=False)
    with open(html, 'w', encoding='utf-8') as f:
        f.write(_page(rows, **page_kwargs))
    return updates, html


def test_consistent_pair_passes():
    with tempfile.TemporaryDirectory() as d:
        updates, html = _write_pair(d, [('V6.6', '2026-11-09'), ('V6.5', '2026-11-05')])
        assert cdh.check_updates_log_sync(updates, html) == []


def test_counterfactual_1_deleted_panel_with_stray_version_string():
    """哑点①：日志整块删光但别处残留 V6.6 字样，旧版会放行，新版必须报。"""
    with tempfile.TemporaryDirectory() as d:
        updates, html = _write_pair(
            d, [('V6.6', '2026-11-09'), ('V6.5', '2026-11-05')],
            keep_panel=False, stray_version='V6.6',
        )
        issues = cdh.check_updates_log_sync(updates, html)
        assert issues, '哑点①复发：整块删光仍放行'
        assert any('找不到更新日志板块' in x for x in issues), issues


def test_counterfactual_2_log_rolled_back_but_page_still_new():
    """哑点②：清单回滚成旧版、页面仍是新版，旧版会放行，新版必须报。"""
    with tempfile.TemporaryDirectory() as d:
        updates, html = _write_pair(d, [('V6.6', '2026-11-09'), ('V6.5', '2026-11-05')])
        with open(updates, 'w', encoding='utf-8') as f:
            json.dump(_logs([('V6.5', '2026-11-05')]), f, ensure_ascii=False)
        issues = cdh.check_updates_log_sync(updates, html)
        assert issues, '哑点②复发：清单回滚仍放行'
        assert any('页面比清单新' in x for x in issues), issues


def test_wrong_record_count_and_wrong_date_are_detected():
    with tempfile.TemporaryDirectory() as d:
        rows = [('V6.6', '2026-11-09'), ('V6.5', '2026-11-05')]
        updates, html = _write_pair(d, rows, count=99)
        assert any('页面标称 99 条' in x for x in cdh.check_updates_log_sync(updates, html))
        with open(updates, 'w', encoding='utf-8') as f:
            json.dump(_logs([('V6.6', '2026-11-08'), ('V6.5', '2026-11-05')]), f, ensure_ascii=False)
        assert any('日期序列' in x for x in cdh.check_updates_log_sync(updates, html))


def test_only_cards_deleted_still_fails():
    """板块骨架还在、卡片全删，且版本号字样残留在别处 → 也不能放行。"""
    with tempfile.TemporaryDirectory() as d:
        rows = [('V6.6', '2026-11-09'), ('V6.5', '2026-11-05')]
        updates, html = _write_pair(d, rows, stray_version='V6.6')
        with open(html, 'w', encoding='utf-8') as f:
            f.write(_page([], count=len(rows), stray_version='V6.6'))
        issues = cdh.check_updates_log_sync(updates, html)
        assert issues
        assert any('一条记录卡片都没有' in x for x in issues), issues


def test_real_production_pair_is_in_sync():
    """真实仓库：清单 61 条，页面 61 条，双向一致。"""
    assert cdh.check_updates_log_sync() == []


def test_check_is_wired_into_main():
    """接线断言：造好没插电是本项目踩过的坑。"""
    source = Path(ROOT, 'scripts', 'check_data_health.py').read_text(encoding='utf-8')
    assert 'failures += check_updates_log_sync()' in source


def test_patch_target_is_live():
    """补丁命中验证：把截板函数改成抛异常，调用必须真的抛。

    只搬文件不改逻辑时 mock.patch 旧模块会静默落空，测试仍 PASS 但实际在调真实实现。
    """
    with tempfile.TemporaryDirectory() as d:
        updates, html = _write_pair(d, [('V6.6', '2026-11-09')])
        with mock.patch.object(cdh, '_updates_panel', side_effect=RuntimeError('probe')):
            issues = cdh.check_updates_log_sync(updates, html)
        assert any('probe' in x for x in issues), (
            '补丁没命中：调用的不是 cdh._updates_panel，实际走的是真实实现'
        )
