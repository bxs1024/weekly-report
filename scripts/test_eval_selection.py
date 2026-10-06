"""精选门槛校准工具测试（P5）。

不访问外部服务、不写真实 data/：用临时目录跑评测逻辑。
覆盖：
- 指标计算（准确率/查准率/查全率/F1）
- 门槛扫描与最佳门槛选择
- either / 空标注被正确忽略
- 错例分类（漏选 / 误选）
- 样本骨架生成与 JSONL 往返
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eval_selection


def _row(case_id, decision, program=None, ai=None, title='标题'):
    return {
        'caseId': case_id,
        'material': {'title': title, 'overview': '', 'date': '2026-10-01', 'source': 'S', 'region': '拉美'},
        'programScore': program,
        'aiScoreAvg': ai,
        'gold': {'decision': decision},
    }


def test_metrics_basic():
    pairs = [(True, 80), (True, 70), (False, 30), (False, 40)]
    m = eval_selection._metrics(pairs, 60)
    assert m['tp'] == 2 and m['tn'] == 2 and m['fp'] == 0 and m['fn'] == 0
    assert m['accuracy'] == 1.0
    assert m['precision'] == 1.0 and m['recall'] == 1.0 and m['f1'] == 1.0


def test_metrics_mixed():
    pairs = [(True, 80), (True, 50), (False, 70), (False, 30)]
    m = eval_selection._metrics(pairs, 60)
    assert m['tp'] == 1 and m['fn'] == 1 and m['fp'] == 1 and m['tn'] == 1
    assert m['precision'] == 0.5 and m['recall'] == 0.5
    assert m['accuracy'] == 0.5


def test_metrics_skips_missing_scores():
    pairs = [(True, 80), (True, None), (False, 30)]
    m = eval_selection._metrics(pairs, 60)
    assert m['n'] == 2 and m['skipped'] == 1


def test_threshold_scan_and_best():
    pairs = [(True, 85), (True, 75), (True, 45), (False, 35), (False, 30), (False, 80)]
    table = eval_selection.scan_thresholds(pairs, low=40, high=90, step=10)
    thresholds = [r['threshold'] for r in table]
    assert thresholds == [40, 50, 60, 70, 80, 90], thresholds
    best = eval_selection.best_by_f1(table)
    assert best is not None and best['threshold'] in thresholds


def test_either_and_empty_ignored():
    rows = [
        _row('c1', 'select', program=80),
        _row('c2', 'either', program=10),
        _row('c3', '', program=20),
        _row('c4', 'reject', program=30),
    ]
    result = eval_selection.evaluate(rows, 'programScore')
    assert result['labelled'] == 2, f'either/空 不应计入: {result["labelled"]}'


def test_error_classification():
    rows = [
        _row('c1', 'select', program=30, title='该选但分数低'),
        _row('c2', 'reject', program=90, title='不该选但分数高'),
    ]
    result = eval_selection.evaluate(rows, 'programScore')
    kinds = {e['caseId']: e['kind'] for e in result['errors']}
    assert kinds.get('c1') == '漏选', kinds
    assert kinds.get('c2') == '误选', kinds


def test_both_score_fields_evaluated():
    rows = [
        _row('c1', 'select', program=80, ai=75),
        _row('c2', 'reject', program=20, ai=25),
    ]
    r1 = eval_selection.evaluate(rows, 'programScore')
    r2 = eval_selection.evaluate(rows, 'aiScoreAvg')
    assert r1['score_field'] == 'programScore'
    assert r2['score_field'] == 'aiScoreAvg'
    assert r1['best']['accuracy'] == 1.0 and r2['best']['accuracy'] == 1.0


def test_sample_skeleton_and_jsonl_roundtrip():
    events = [
        {'date': '2026-10-01', 'title': 'A 事件', 'signal_change_score': 70,
         'content_overview': '概要A', 'source': 'S1', 'region': '拉美'},
        {'date': '2026-10-02', 'title': 'B 事件', 'signal_change_score': 40,
         'content_overview': '概要B', 'source': 'S2', 'region': '东南亚'},
    ]
    rows = eval_selection.build_sample(events, size=10, days=3650, seed=1)
    assert len(rows) == 2
    assert all(r['gold']['decision'] == '' for r in rows), '骨架应留空待标注'
    assert rows[0]['programScore'] in (70, 40)

    tmpdir = tempfile.mkdtemp(prefix='eval_sel_')
    path = os.path.join(tmpdir, 'gold.jsonl')
    assert eval_selection.write_jsonl(rows, path) == 2
    back = eval_selection.read_jsonl(path)
    assert len(back) == 2
    assert back[0]['caseId'] == rows[0]['caseId']


def _run_all():
    test_metrics_basic()
    test_metrics_mixed()
    test_metrics_skips_missing_scores()
    test_threshold_scan_and_best()
    test_either_and_empty_ignored()
    test_error_classification()
    test_both_score_fields_evaluated()
    test_sample_skeleton_and_jsonl_roundtrip()
    print('eval selection tests passed')


if __name__ == '__main__':
    _run_all()
