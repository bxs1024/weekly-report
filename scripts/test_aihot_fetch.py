#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIHOT 抓取适配回归测试（不联网，全部用构造 HTML / mock）。

覆盖 2026-10 源站改版后新增的关键行为：
- 热点榜：前 3 名 article 与 4-10 名 li 的文档顺序与名次不一致 → 必须按 rank 排序
- 热点榜：标题只取链接文本，不含兄弟节点里的状态标签（"发酵中"/"爆"/"新"）
- 抓 0 条 → 非零退出且不覆盖已有数据（空结果按失败处理）
- 编码：源站不再声明 charset 时必须按 utf-8 解码（否则中文乱码）
- 模型榜：新版标准表格解析，价格 3 列，字段名保持展示层兼容
- 隔离：aihot.yml 不得提交 docs/（外部源故障不得外溢）

风格遵循 run_tests.py：模块级 test_* 函数 + assert。
"""

import os
import unittest.mock as mock

from bs4 import BeautifulSoup

import fetch_aihot_hot
from fetch_aihot_hot import _decode_response, _extract_rank, _parse_items
from fetch_model_leaderboard import _parse_ranking

HOT_HTML = """
<html><body><section>
  <article class="card card-hover">
    <span class="mono text-rank-1">NO.01</span>
    <h2><a href="/story/aaa">第一条热点</a></h2>
    <p class="line-clamp-3">摘要一</p>
  </article>
  <article class="card card-hover">
    <span class="mono text-rank-2">NO.02</span>
    <h2><a href="/story/bbb">第二条热点</a></h2>
    <p>摘要二</p>
  </article>
  <ol class="card">
    <li class="group relative grid">
      <span aria-label="热度排名第 4 位" class="mono">04</span>
      <div><h3>
        <a href="/story/ddd">第四条热点</a>
        <span class="bg-amber-soft" title="讨论仍在增加">发酵中</span>
      </h3>
      <p class="mt-0.5 line-clamp-2">摘要四</p></div>
    </li>
  </ol>
  <article class="card card-hover">
    <span class="mono text-rank-3">NO.03</span>
    <h2><a href="/story/ccc">第三条热点</a></h2>
    <p>摘要三</p>
  </article>
</section></body></html>
"""

LB_HTML = """
<html><body><table>
  <tr><th>名次</th><th>模型</th><th>上线日期</th><th>评测证据</th>
      <th>缓存价格</th><th>输入价格</th><th>输出价格</th><th>AIHOT 评分</th></tr>
  <tr>
    <td><span class="mono">01</span></td>
    <td><span><span><a href="/leaderboard/claude-opus-5-5">
        <strong class="text-[15px]">Claude Opus 5.5</strong></a></span>
      <small class="hidden lg:block">Anthropic</small>
      <small class="block lg:hidden">Anthropic · 9月22日上线</small></span></td>
    <td>2026-09-22</td>
    <td><span class="num block">33 项评测</span><span class="block">覆盖 96%</span></td>
    <td>¥1.34</td><td>¥26.82</td><td>¥134.09</td>
    <td><span><strong class="mono">73.9</strong></span></td>
  </tr>
  <tr>
    <td><span class="mono">02</span></td>
    <td><span><a href="/leaderboard/gemini-4-argon">
        <strong>Gemini 4 Argon</strong></a>
      <small class="hidden lg:block">Google</small></span></td>
    <td>2026-09-30</td>
    <td><span class="num block">21 项评测</span><span class="block">覆盖 68%</span></td>
    <td>待核验</td><td>待核验</td><td>待核验</td>
    <td><span><strong class="mono">73.6</strong></span></td>
  </tr>
</table></body></html>
"""


def _hot_items():
    return _parse_items(BeautifulSoup(HOT_HTML, 'html.parser'))


# ─────────────────────────── 热点榜解析 ───────────────────────────

def test_hot_sorts_by_rank_despite_document_order():
    """article 第 3 名在文档里排在 li 第 4 名之后，结果仍须按名次排序。"""
    items = _hot_items()
    assert [i['rank'] for i in items] == [1, 2, 3, 4], [i['rank'] for i in items]
    assert items[2]['title'] == '第三条热点'
    assert items[3]['title'] == '第四条热点'


def test_hot_title_excludes_status_tag():
    """标题只取链接文本，不含兄弟节点里的状态标签。"""
    items = _hot_items()
    assert items[3]['title'] == '第四条热点'
    assert '发酵中' not in items[3]['title']


def test_hot_extracts_summary_and_story_url():
    items = _hot_items()
    assert items[0]['summary'] == '摘要一'
    assert items[0]['story_url'] == 'https://aihot.virxact.com/story/aaa'


def test_hot_rank_from_aria_label():
    """4-10 名用 aria-label="热度排名第 N 位" 取名次。"""
    li = BeautifulSoup(HOT_HTML, 'html.parser').find('li')
    assert _extract_rank(li, 99) == 4


def test_hot_new_fields_present():
    """新版字段：heat 为 None、heat_change 存在、sources 为空列表。"""
    items = _hot_items()
    assert items[0]['heat'] is None
    assert items[0]['heat_change'] == ''
    assert items[0]['sources'] == []


# ─────────────────────────── 失败可见性 ───────────────────────────

def test_hot_empty_result_exits_nonzero():
    """抓 0 条（选择器与 AI 兜底都失败）→ SystemExit(2)，不静默通过。"""
    code = None
    with mock.patch.object(fetch_aihot_hot, '_fetch', return_value='<html></html>'):
        with mock.patch.object(fetch_aihot_hot, '_ai_extract', return_value=[]):
            with mock.patch.object(fetch_aihot_hot, '_session', return_value=mock.Mock()):
                try:
                    fetch_aihot_hot.main()
                except SystemExit as exc:
                    code = exc.code
    assert code == 2, f'期望退出码 2，实际 {code}'


def test_hot_empty_result_does_not_write_files():
    """失败时不得写出数据文件（不覆盖已有数据）。"""
    opened = []
    real_open = open

    def tracking_open(path, *a, **kw):
        opened.append(str(path))
        return real_open(path, *a, **kw)

    with mock.patch.object(fetch_aihot_hot, '_fetch', return_value='<html></html>'):
        with mock.patch.object(fetch_aihot_hot, '_ai_extract', return_value=[]):
            with mock.patch.object(fetch_aihot_hot, '_session', return_value=mock.Mock()):
                with mock.patch('builtins.open', side_effect=tracking_open):
                    try:
                        fetch_aihot_hot.main()
                    except SystemExit:
                        pass
    assert [p for p in opened if p.endswith('aihot_hot.json')] == [], opened


# ─────────────────────────── 编码 ───────────────────────────

def test_decode_uses_utf8_when_charset_missing():
    """源站 header 只写 text/html 时，必须按 utf-8 解码（否则中文乱码）。"""
    resp = mock.Mock()
    resp.headers = {'content-type': 'text/html'}
    resp.content = '中文标题'.encode('utf-8')
    assert _decode_response(resp) == '中文标题'


def test_decode_respects_declared_charset():
    resp = mock.Mock()
    resp.headers = {'content-type': 'text/html; charset=utf-8'}
    resp.text = '中文标题'
    assert _decode_response(resp) == '中文标题'


# ─────────────────────────── 模型榜解析 ───────────────────────────

def test_lb_parses_table_rows():
    rows = _parse_ranking(LB_HTML)
    assert len(rows) == 2, len(rows)


def test_lb_field_names_stay_display_compatible():
    """展示层依赖的字段名必须保持。"""
    row = _parse_ranking(LB_HTML)[0]
    assert row['rank'] == 1
    assert row['name'] == 'Claude Opus 5.5'
    assert row['provider'] == 'Anthropic'
    assert row['release_date'] == '2026-09-22'
    assert '33' in row['completeness']
    assert row['input_price'] == '¥26.82'
    assert row['output_price'] == '¥134.09'
    assert row['cache_price'] == '¥1.34'
    assert row['consensus_score'] == 73.9
    assert row['detail_url'] == 'https://aihot.virxact.com/leaderboard/claude-opus-5-5'


def test_lb_handles_pending_price():
    """「待核验」价格原样保留，不当成异常丢弃整行。"""
    row = _parse_ranking(LB_HTML)[1]
    assert row['input_price'] == '待核验'
    assert row['consensus_score'] == 73.6


# ─────────────────────────── 热度字段接线 ───────────────────────────

def test_items_to_list_carries_heat_change():
    from reports.period import _aihot_items_to_list
    data = {'fetched_date': '2026-10-08', 'items': [
        {'rank': 1, 'title': 'T', 'heat': None, 'heat_change': '↑ 46 %'},
    ]}
    items = _aihot_items_to_list(data)
    assert items[0]['heat_change'] == '↑ 46 %'
    assert items[0]['heat'] is None


# ─────────────────────────── 隔离 ───────────────────────────

def _aihot_workflow():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, '.github', 'workflows', 'aihot.yml'),
              'r', encoding='utf-8') as f:
        return f.read()


def test_isolation_never_stages_docs():
    """aihot.yml 的任何 git add 都不得包含 docs/（不得与主流程争抢页面）。"""
    for line in _aihot_workflow().splitlines():
        if 'git add' in line:
            assert 'docs/' not in line, f'aihot.yml 不应提交 docs/：{line.strip()}'


def test_isolation_commits_only_own_data_files():
    content = _aihot_workflow()
    assert 'data/aihot_hot.json' in content
    assert 'data/model_leaderboard.json' in content


def test_isolation_no_silent_error_swallow():
    """抓取步骤不得用 `|| echo` 吞掉失败。"""
    assert '|| echo' not in _aihot_workflow()
