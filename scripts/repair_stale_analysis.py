"""一次性工具：为「有问题的」历史事件重新做 AI 分析（不是全量重跑）。

只处理 reason 为空 或 含「暂不可用」/「AI 分析暂不可用」的事件，
用 DeepSeek 重新分析，更新 reason / content_overview / insight_label / trend_topic。
其余存量事件一律不动。

用法:
    python scripts/repair_stale_analysis.py                     # 处理全部历史中的问题事件
    python scripts/repair_stale_analysis.py --days 30           # 只处理最近 30 天
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.chdir(ROOT)  # fetch_news 的 load_dotenv() 按当前工作目录找 .env

from fetch_news import analyze_events_ark, analyze_events_deepseek  # noqa: E402

EVENTS_PATH = ROOT / 'data' / 'events.json'
STALE_MARKERS = ('暂不可用', 'AI 分析暂不可用')


def norm_url(u):
    return (u or '').strip().rstrip('/')


def is_stale(ev):
    reason = (ev.get('reason') or '').strip()
    if not reason:
        return True
    return any(m in reason for m in STALE_MARKERS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=None, help='只处理最近 N 天；默认全部历史')
    ap.add_argument('--batch', type=int, default=10)
    args = ap.parse_args()

    data = json.load(open(EVENTS_PATH, encoding='utf-8'))

    targets = []
    for dt, evs in data.items():
        if args.days:
            try:
                if date.fromisoformat(dt) < date.today() - timedelta(days=args.days):
                    continue
            except ValueError:
                continue
        for ev in evs:
            if is_stale(ev):
                targets.append(ev)

    print(f'问题事件: {len(targets)} 条（将被 AI 重新分析）', flush=True)
    if not targets:
        print('没有需要修复的事件，退出', flush=True)
        return

    # 备份
    backup_dir = Path(os.environ.get('TEMP', str(ROOT))) / 'weekly-report-backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f'events.repair-{date.today().isoformat()}.json'
    json.dump(data, open(backup, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'backup -> {backup}', flush=True)

    by_url = {norm_url(ev.get('url')): ev for ev in targets}
    items = [{'title': ev.get('title', ''), 'url': ev.get('url', ''),
              'source': ev.get('source', ''), 'region': ev.get('region', '')} for ev in targets]

    updated = 0
    total_batches = (len(items) + args.batch - 1) // args.batch
    ark_key = os.environ.get('ARK_API_KEY', '')
    analyze = analyze_events_ark if ark_key and len(ark_key) >= 10 else analyze_events_deepseek
    for i in range(0, len(items), args.batch):
        chunk = items[i:i + args.batch]
        result = analyze(chunk)
        if not result:
            print(f'  [{(i // args.batch) + 1}/{total_batches}] AI 失败，跳过本批', flush=True)
            continue
        for r in result:
            ev = by_url.get(norm_url(r.get('url')))
            if ev is None:
                continue
            reason = (r.get('reason') or '').strip()
            if not reason:
                continue  # AI 没给 reason 视为失败
            ev['reason'] = reason
            if (r.get('content_overview') or '').strip():
                ev['content_overview'] = r['content_overview'].strip()
            if (r.get('summary_short') or '').strip():
                ev['summary_short'] = r['summary_short'].strip()
            if (r.get('insight_label') or '').strip():
                ev['insight_label'] = r['insight_label'].strip()
            if (r.get('trend_topic') or '').strip():
                ev['trend_topic'] = r['trend_topic'].strip()
            if '暂不可用' in (ev.get('reason') or ''):
                continue  # 修复后仍有暂不可用 = 没修好，不计入
            updated += 1
        print(f'  [{(i // args.batch) + 1}/{total_batches}] 已修复 {updated}', flush=True)

    json.dump(data, open(EVENTS_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'done: 修复 {updated} 条（共 {len(targets)} 条待修）', flush=True)


if __name__ == '__main__':
    main()
