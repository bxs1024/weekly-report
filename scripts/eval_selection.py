"""精选门槛校准工具（SelectBench 的本地形态）。

对应 AIHOT 的 `scripts/eval-selection.ts` + 后台 SelectBench：
拿一批人工标注过「该选 / 不该选」的样本，跑一遍评分，看不同门槛下的
准确率、查准率、查全率，并列出判错的条目供回去改评分标准。

与 AIHOT 的差异：
- 本站评分为程序分（signal_change_score）+ AI 双评分（ai_score_avg）并存。
  本工具对两者分别扫描门槛，便于过渡期判断该以谁为主排序轴。
- 样本格式简化为 JSONL，从 events.json 直接抽样生成骨架，
  人工只需补 `gold.decision` 字段。

用法：
    # 1) 从最近事件抽样，生成待标注骨架
    python scripts/eval_selection.py --sample 120 --days 30 --out .data/gold.jsonl

    # 2) 人工补 gold.decision（select / reject / either）后跑评测
    python scripts/eval_selection.py --gold .data/gold.jsonl --label "第一版"

报告写到 .data/eval/selection-<label>-<时间>.json，并打印摘要表。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 路径一律锚定仓库根（不依赖调用进程 CWD，见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
try:
    from repo_paths import REPO_ROOT as _REPO_ROOT
except ImportError:
    from scripts.repo_paths import REPO_ROOT as _REPO_ROOT
GOLD_DEFAULT = os.path.join(_REPO_ROOT, '.data', 'gold.jsonl')
EVAL_DIR = os.path.join(_REPO_ROOT, '.data', 'eval')
EVENTS_DEFAULT = os.path.join(_REPO_ROOT, 'data', 'events.json')


def load_events(path=None, prepare=True):
    """加载事件库。

    注意：signal_change_score / attention_score 等是**派生字段**，在 events.json
    里往往是 0 或缺失（由 render 时的 prepare_event_contract 现算）。直接读原始
    字段会得到空分数，因此默认走一遍契约层拿真实分数。
    """
    path = path or EVENTS_DEFAULT
    with open(path, 'r', encoding='utf-8') as handle:
        buckets = json.load(handle)
    events = []
    for date, items in (buckets or {}).items():
        for event in items or []:
            event.setdefault('date', date)
            events.append(event)

    if prepare:
        try:
            from event_contract import prepare_event_contract
        except ImportError:
            from scripts.event_contract import prepare_event_contract
        for event in events:
            try:
                prepare_event_contract(event)
            except Exception:
                pass
    return events


def build_sample(events, size=120, days=30, seed=7):
    """按时间窗抽样，生成待标注骨架。人工只需补 gold.decision。"""
    import random

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    pool = [e for e in events if (e.get('date') or '') >= cutoff]
    if not pool:
        pool = events
    random.Random(seed).shuffle(pool)

    rows = []
    for index, event in enumerate(pool[:size]):
        rows.append({
            'caseId': f'case-{index + 1:04d}',
            'material': {
                'title': event.get('title') or '',
                'overview': event.get('content_overview') or event.get('summary_short') or '',
                'date': event.get('date') or '',
                'source': event.get('source') or '',
                'region': event.get('region') or '',
            },
            'programScore': event.get('signal_change_score'),
            'aiScoreAvg': event.get('ai_score_avg'),
            'gold': {'decision': ''},   # 待人工填写
        })
    return rows


def write_jsonl(rows, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return len(rows)


def read_jsonl(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _metrics(pairs, threshold):
    """pairs: [(should_select: bool, score: float|None)]。"""
    tp = fp = fn = tn = 0
    skipped = 0
    for should, score in pairs:
        if score is None:
            skipped += 1
            continue
        predicted = score >= threshold
        if should and predicted:
            tp += 1
        elif should and not predicted:
            fn += 1
        elif not should and predicted:
            fp += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    if not total:
        return None
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        'threshold': threshold,
        'n': total,
        'skipped': skipped,
        'accuracy': round((tp + tn) / total, 4),
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'f1': round(f1, 4),
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
    }


def scan_thresholds(pairs, low=40, high=92, step=2):
    table = []
    for threshold in range(low, high + 1, step):
        row = _metrics(pairs, threshold)
        if row:
            table.append(row)
    return table


def best_by_f1(table):
    if not table:
        return None
    return max(table, key=lambda r: (r['f1'], r['accuracy']))


def evaluate(rows, score_field):
    """对某一评分字段做门槛扫描。ignored 的标注（either / 空）不计入。"""
    pairs = []
    errors = []
    for row in rows:
        decision = ((row.get('gold') or {}).get('decision') or '').strip().lower()
        if decision not in ('select', 'reject'):
            continue
        should = decision == 'select'
        score = row.get(score_field)
        pairs.append((should, score))
        if score is None:
            continue
        # 用默认门槛 60 记录错例，供人工回看
        if should != (score >= 60):
            errors.append({
                'caseId': row.get('caseId'),
                'title': (row.get('material') or {}).get('title', '')[:70],
                'gold': decision,
                'score': score,
                'kind': '漏选' if should else '误选',
            })
    table = scan_thresholds(pairs)
    return {
        'score_field': score_field,
        'labelled': len(pairs),
        'table': table,
        'best': best_by_f1(table),
        'errors': errors[:30],
    }


def print_report(result):
    print(f"\n评分字段：{result['score_field']}（有效标注 {result['labelled']} 条）")
    print(f"{'门槛':>6} {'准确率':>8} {'查准率':>8} {'查全率':>8} {'F1':>8}")
    for row in result['table']:
        mark = ' ←最佳' if result['best'] and row['threshold'] == result['best']['threshold'] else ''
        print(f"{row['threshold']:>6} {row['accuracy']:>8.3f} {row['precision']:>8.3f} "
              f"{row['recall']:>8.3f} {row['f1']:>8.3f}{mark}")
    if result['errors']:
        print(f"\n默认门槛 60 下的错例（前 {len(result['errors'])} 条）：")
        for item in result['errors']:
            print(f"  [{item['kind']}] {item['score']}分 {item['caseId']} {item['title']}")


def main():
    parser = argparse.ArgumentParser(description='精选门槛校准')
    parser.add_argument('--sample', type=int, default=0, help='抽样条数，生成待标注骨架')
    parser.add_argument('--days', type=int, default=30, help='抽样时间窗（天）')
    parser.add_argument('--out', default=GOLD_DEFAULT, help='骨架输出路径')
    parser.add_argument('--gold', default=GOLD_DEFAULT, help='标注样本路径')
    parser.add_argument('--label', default='', help='本次评测标签')
    parser.add_argument('--events', default=EVENTS_DEFAULT, help='事件库路径')
    args = parser.parse_args()

    if args.sample:
        events = load_events(args.events)
        rows = build_sample(events, size=args.sample, days=args.days)
        count = write_jsonl(rows, args.out)
        print(f'已生成待标注样本 {count} 条 → {args.out}')
        print('请补全每行的 gold.decision（select / reject / either），再跑 --gold 评测。')
        return

    if not os.path.exists(args.gold):
        print(f'样本文件不存在：{args.gold}')
        print('先跑 --sample 120 生成骨架，人工标注后再评测。')
        return

    rows = read_jsonl(args.gold)
    results = [evaluate(rows, field) for field in ('programScore', 'aiScoreAvg')]
    for result in results:
        print_report(result)

    os.makedirs(EVAL_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    label = args.label or 'run'
    out_path = os.path.join(EVAL_DIR, f'selection-{label}-{stamp}.json')
    with open(out_path, 'w', encoding='utf-8') as handle:
        json.dump({'label': label, 'generated_at': stamp, 'results': results},
                  handle, ensure_ascii=False, indent=2)
    print(f'\n报告已写入：{out_path}')


if __name__ == '__main__':
    main()
