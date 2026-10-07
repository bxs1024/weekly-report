"""AI 双评分 vs 程序分：过渡期对比报告（方案决策 3「并排 2 周再切」的数据依据）。

用法（在仓库根执行）：
    python scripts/ai_score_report.py --days 7              # 补评 + 出报告
    python scripts/ai_score_report.py --days 7 --no-backfill # 只用已有分数出报告

产出：
    - 终端摘要（一致性、相关性、分歧前 20 条）
    - .data/eval/ai-score-report-<日期>.json（完整报告，含分歧明细）

口径说明：程序分取 `signal_change_score`（0–100，与 AI 分同量纲、共用门槛 60）。
事件上的 `score` 是 1–10 的另一根轴，不同量纲，不能直接比。最近 7 天
signal_change_score 覆盖率为 100%，历史事件覆盖不全（该字段是后期加的），
所以窗口不宜拉太长。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_scoring  # noqa: E402
import ai_receipts  # noqa: E402

try:
    from repo_paths import data_path, repo_path
except ImportError:
    from scripts.repo_paths import data_path, repo_path


def load_window(days):
    """取 events.json 最近 N 天的事件（日期键 YYYY-MM-DD，字典序即时间序）。

    返回 (store, keys, events)。store 是整份事件库——--persist 要写回它。
    """
    with open(data_path('events.json'), encoding='utf-8') as f:
        store = json.load(f)
    keys = sorted(store.keys())[-days:]
    events = [e for key in keys for e in store[key]]
    return store, keys, events


def backfill(events):
    """给还没评过的事件补 AI 双评分（已有 ai_score_avg 的跳过，不重复付费）。"""
    targets = [e for e in events if e.get('ai_score_avg') is None]
    if not targets:
        return {'scored': 0, 'failed': 0, 'reused': 0, 'skipped': len(events)}
    stats = ai_scoring.score_events(targets)
    stats['skipped'] = len(events) - len(targets)
    return stats


def print_report(days, keys, events, report, score_stats):
    print(f"\n{'=' * 66}")
    print(f"AI 双评分 vs 程序分 对比报告  |  最近 {days} 天（{keys[0]} ~ {keys[-1]}）")
    print('=' * 66)
    print(f"窗口事件 {len(events)} 条；本次补评 {score_stats['scored']} 条"
          f"（回执复用 {score_stats['reused']}，失败 {score_stats['failed']}，"
          f"已评跳过 {score_stats.get('skipped', 0)}）")

    if not report['n']:
        print("\n可对比样本为 0：既没有 ai_score_avg 也没有 signal_change_score。")
        print("先跑一次不带 --no-backfill 的补评。")
        return

    n = report['n']
    agree_hi, agree_lo = report['agree_hi'], report['agree_lo']
    print(f"\n可对比样本 {n} 条（门槛 {ai_scoring.DEFAULT_THRESHOLD}）")
    print(f"  两边都过门槛（高一致）：{agree_hi} 条  {agree_hi / n * 100:5.1f}%")
    print(f"  两边都没过（低一致）  ：{agree_lo} 条  {agree_lo / n * 100:5.1f}%")
    print(f"  一致率合计            ：{(agree_hi + agree_lo) / n * 100:5.1f}%")
    print(f"  相关系数              ：{report['corr']}")

    div = report['divergent']
    print(f"\n分歧 {len(div)} 条（一方过门槛另一方没过，或差距 > 25 分），前 20 条：")
    for item in div:
        tag = '过门槛交叉' if item['kind'] == 'cross' else '分差过大'
        print(f"  [{tag}] AI={item['ai']:>3} 程序={item['program']:>3}  {item['title']}")

    stats = ai_receipts.receipt_stats()
    print(f"\n回执累计 {stats['total']} 条 {stats['by_kind']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7, help='对比窗口天数（默认 7）')
    ap.add_argument('--no-backfill', action='store_true', help='只用已有分数，不补评')
    ap.add_argument('--persist', action='store_true',
                    help='把补出来的 AI 分数写回 events.json（默认只在内存里算，不动数据）')
    ap.add_argument('--out-dir', default=None, help='报告输出目录（默认 .data/eval）')
    args = ap.parse_args()

    store, keys, events = load_window(args.days)
    if not keys:
        print('events.json 为空')
        return 1

    score_stats = {'scored': 0, 'failed': 0, 'reused': 0, 'skipped': len(events)}
    if not args.no_backfill:
        score_stats = backfill(events)

    if args.persist and score_stats['scored']:
        # 与 main() 同一写法，避免格式差异把整份文件都变成 diff
        with open(data_path('events.json'), 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        print(f'已把 {score_stats["scored"]} 条 AI 分数写回 {data_path("events.json")}')

    report = ai_scoring.compare_with_program_score(events)
    print_report(args.days, keys, events, report, score_stats)

    out_dir = args.out_dir or repo_path('.data', 'eval')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'ai-score-report-{keys[-1]}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'window_days': args.days,
            'window': [keys[0], keys[-1]],
            'events': len(events),
            'score_stats': score_stats,
            'report': report,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入：{out_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
