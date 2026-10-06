"""P4 拆包验收 harness：离线、确定性地渲染页面并比对生成物。

为什么需要它：`generate_html.py` 的编辑层会调 AI（多通道重试、超时 60s），
输出不确定且依赖网络，无法作为「生成物 diff 为空」的验收基准。本 harness
把 AI 与编辑缓存都关成确定性空值，使渲染结果只由 data/*.json 决定。

用法（在仓库根执行）：
    python scripts/verify_render_output.py --baseline   # 存基线
    python scripts/verify_render_output.py --check      # 与基线逐字节比对

退出码：0 一致 / 1 不一致（打印首个差异行）。
"""

import argparse
import difflib
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402
import generate_html as G  # noqa: E402

BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '.data', 'p4_baseline_preview.html',
)


def _neutralize_ai():
    """关闭一切非确定性来源：AI 通道 + 编辑缓存。"""
    # 编辑层：_chat_api_candidates() 返回空 → build_*_editorial 立刻返回 None → 走模板
    fetch_news._chat_api_candidates = lambda: []
    # 缓存：强制 miss，避免缓存文件内容随时间变化影响输出
    G._editorial_cache_get = lambda *a, **k: (None, None)
    G._editorial_cache_put = lambda *a, **k: None


def _render():
    _neutralize_ai()
    G.generate_html(force=True, preview_mode=True)
    out = G.docs_path('preview.html')
    with open(out, 'r', encoding='utf-8') as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', action='store_true', help='保存当前渲染结果为基线')
    ap.add_argument('--check', action='store_true', help='与基线比对')
    args = ap.parse_args()

    html = _render()
    digest = hashlib.sha256(html.encode('utf-8')).hexdigest()
    print(f'rendered preview.html | {len(html)} chars | sha256={digest[:16]}')

    if args.baseline:
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        with open(BASELINE_PATH, 'w', encoding='utf-8', newline='') as f:
            f.write(html)
        print(f'baseline saved -> {BASELINE_PATH}')
        return 0

    if args.check:
        if not os.path.exists(BASELINE_PATH):
            print(f'ERROR: baseline not found: {BASELINE_PATH}', file=sys.stderr)
            return 2
        with open(BASELINE_PATH, 'r', encoding='utf-8', newline='') as f:
            base = f.read()
        if base == html:
            print('MATCH: 生成物与基线逐字节一致')
            return 0
        diff = list(difflib.unified_diff(
            base.splitlines(), html.splitlines(),
            fromfile='baseline', tofile='current', lineterm='', n=1,
        ))
        print(f'MISMATCH: 生成物有差异（{len(diff)} 行 diff），前 40 行：', file=sys.stderr)
        for line in diff[:40]:
            print(line, file=sys.stderr)
        return 1

    print('nothing to do: pass --baseline or --check')
    return 0


if __name__ == '__main__':
    sys.exit(main())
