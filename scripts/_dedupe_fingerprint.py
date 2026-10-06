"""捕获/比对展示层去重结果，用于性能优化的等价性验证。

去重结果由 build_display_context() 的 all_events_for_list 体现。本工具把它
归一成一个稳定指纹（按 url/title/date 排序后的元组列表），存下来比对，
证明优化「只提速、不改变合并结果」。

用法：
    python scripts/_dedupe_fingerprint.py --save
    python scripts/_dedupe_fingerprint.py --check
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(os.path.dirname(HERE), '.data', 'p4_dedupe_baseline.json')


def compute():
    import fetch_news
import providers.llm
    import generate_html as G
    providers.llm._chat_api_candidates = lambda: []  # AI 通道已搬到 providers.llm；打 fetch_news 转发层是值绑定，会静默失效
    G._editorial_cache_get = lambda *a, **k: (None, None)
    G._editorial_cache_put = lambda *a, **k: None
    ctx = G.build_display_context()
    rows = []
    for ev in ctx['all_events_for_list']:
        rows.append((
            (ev.get('url') or '')[:200],
            (ev.get('title') or '')[:120],
            (ev.get('date') or '')[:10],
            len(ev.get('merged_from') or []),
        ))
    rows.sort()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()

    rows = compute()
    blob = json.dumps(rows, ensure_ascii=False)
    digest = hashlib.sha256(blob.encode('utf-8')).hexdigest()
    print(f'dedupe rows={len(rows)} sha256={digest[:16]}')

    if args.save:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
            f.write(blob)
        print(f'saved -> {os.path.normpath(OUT)}')
        return 0

    if args.check:
        with open(OUT, encoding='utf-8') as f:
            base = f.read()
        if base == blob:
            print('MATCH: 去重结果与基线逐字节一致')
            return 0
        old = json.loads(base)
        oldset = {tuple(r) for r in old}
        newset = {tuple(r) for r in rows}
        print(f'MISMATCH: 旧 {len(oldset)} 新 {len(newset)}', file=sys.stderr)
        for r in sorted(oldset - newset)[:10]:
            print('  - ', r, file=sys.stderr)
        for r in sorted(newset - oldset)[:10]:
            print('  + ', r, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
