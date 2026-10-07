"""把历史事件的归组与热度补齐并写回 events.json（方案 P3 的存量回填）。

用法（在仓库根执行）：
    python scripts/backfill_event_heat.py                # 默认最近 30 天，写回
    python scripts/backfill_event_heat.py --days 60      # 换窗口
    python scripts/backfill_event_heat.py --dry-run      # 只统计，不写文件

为什么要单独一个工具：P3 的归组与热度只在每日流水线里对当天窗口跑一次，
存量历史事件身上没有 group_* / event_heat / event_source_count /
event_report_count 这些字段，首页与公司索引就永远看不到热度角标。
本工具负责把历史补上，之后每天由流水线自己维护。

刻意复用 fetch_news._run_event_grouping，而不是另写一份逻辑：
回填与生产必须是同一条代码路径，否则两边口径迟早漂移，
而「回填出来的数」与「线上跑出来的数」不一致时最难查。

成本：规则层不调模型。若 AI 通道可用且 GROUP_ENABLED 未关，归组层会对
候选对发起真实付费请求——想零成本跑，先设 AI_CALLS_ENABLED=0。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402

try:
    from repo_paths import data_path
except ImportError:
    from scripts.repo_paths import data_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=fetch_news.GROUP_WINDOW_DAYS,
                    help=f'回填窗口天数（默认 {fetch_news.GROUP_WINDOW_DAYS}）')
    ap.add_argument('--dry-run', action='store_true', help='只统计，不写 events.json')
    args = ap.parse_args()

    path = data_path('events.json')
    with open(path, encoding='utf-8') as f:
        store = json.load(f)

    keys = sorted(store.keys())[-args.days:]
    if not keys:
        print('events.json 为空，无事可做')
        return 1

    print(f'窗口 {keys[0]} ~ {keys[-1]}（{len(keys)} 天）')

    metrics = {}
    fetch_news._run_event_grouping(store, metrics, days=args.days)

    heat_rows = [e for key in keys for e in store[key] if e.get('event_heat') is not None]
    hot = [e for e in heat_rows if (e.get('event_source_count') or 0) > 1]
    multi_report = [e for e in heat_rows if (e.get('event_report_count') or 0) > 1]
    print(f'已写热度字段 {len(heat_rows)} 条：'
          f'多来源 {len(hot)} 条，多篇报道 {len(multi_report)} 条')

    if args.dry_run:
        print('--dry-run：未写文件')
        return 0

    # 与 main() 同一写法，避免格式差异把整份文件都变成 diff
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    print(f'已写回 {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
