"""把源文件里的 `# >>> MOVED: X -> mod.py` 标记替换为 re-export 块。

_move.py 搬走定义后只在原文件留标记，本工具补上转发层，让外部调用方
（`from generate_html import xxx`）继续可用——这是「行为零变化」的前提。

标记按**连续块**分组（标记之间只有空行视为同组），组内再按目标模块分包。
生成双分支 import，与 fetch_news 既有写法一致：脚本既能以 scripts/ 为根
（`python scripts/x.py`）也能以仓库根为根（`python -m scripts.x`）。

用法：
    python scripts/_reexport.py --src generate_html.py [--dry]
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = re.compile(r'^# >>> MOVED: (\S+) -> (\S+)$')


def _is_filler(line):
    """标记之间允许的填充：空行，或被搬走代码区的分区 banner（# ─── 标题 ───）。"""
    s = line.strip()
    return s == '' or (s.startswith('#') and set(s) <= set('#─ 　') | set('评分因子'))


def group_marks(lines):
    """返回 [(start_idx, end_idx, [(name, mod), ...])]。

    标记之间只有空行/分区 banner 时视为同一组（banner 描述的是已搬走的代码，
    一并移除）。若期间夹着真实代码则不合并，交给人工处理——宁可多一个块，
    也不能吞掉一行有效代码。
    """
    marks = []
    for i, line in enumerate(lines):
        m = MARK.match(line.strip())
        if m:
            marks.append((i, m.group(1), m.group(2)))
    groups = []
    for pos, name, mod in marks:
        if groups and all(_is_filler(l) for l in lines[groups[-1][1] + 1:pos]):
            g = groups[-1]
            groups[-1] = (g[0], pos, g[2] + [(name, mod)])
        else:
            groups.append((pos, pos, [(name, mod)]))

    # 安全校验：组内不得夹带真实代码
    for s, e, items in groups:
        strays = [(s + k + 1, lines[i].rstrip())
                  for k, i in enumerate(range(s, e + 1))
                  if not MARK.match(lines[i].strip()) and not _is_filler(lines[i])]
        if strays:
            sys.exit(f'ERROR: 组 {s+1}-{e+1} 内夹着非标记代码，拒绝处理：{strays[:5]}')
    return groups


def render_block(items, indent=''):
    """items: [(name, mod)]；按 mod 分包，生成双分支 import。"""
    bymod = {}
    for name, mod in items:
        bymod.setdefault(mod, []).append(name)
    out = []
    for mod in sorted(bymod):
        names = bymod[mod]
        pkg = mod[:-3] if mod.endswith('.py') else mod          # score.py -> score
        full = _mod_path(pkg)
        joined = ', '.join(names)
        if len(joined) <= 68:
            a = f'    from {full} import {joined}\n'
            b = f'    from scripts.{full} import {joined}\n'
        else:
            a = f'    from {full} import (\n' + ''.join(f'        {n},\n' for n in names) + '    )\n'
            b = f'    from scripts.{full} import (\n' + ''.join(f'        {n},\n' for n in names) + '    )\n'
        out.append('try:\n' + a + 'except ImportError:\n' + b)
    return ''.join(indent + l if l.strip() else l for blk in out for l in blk.splitlines(keepends=True))


def _mod_path(pkg):
    """score -> publication.score（按实际存在的位置推导出带包前缀的模块路径）。"""
    for sub in ('', 'publication', 'content', 'sources', 'providers', 'editorial', 'reports'):
        rel = os.path.join(sub, pkg + '.py') if sub else pkg + '.py'
        if os.path.exists(os.path.join(HERE, rel)):
            return rel[:-3].replace(os.sep, '.')
    return pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='fetch_news.py')
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    path = args.src if os.path.isabs(args.src) else os.path.join(HERE, args.src)
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()

    groups = group_marks(lines)
    if not groups:
        print('没有 MOVED 标记，无需处理')
        return 0

    print(f'# 发现 {len(groups)} 个标记块，共 {sum(len(g[2]) for g in groups)} 个名字')
    for s, e, items in groups:
        mods = sorted({m for _, m in items})
        print(f'  {s+1:5d}-{e+1:5d}  {len(items):3d} 个 -> {mods}')

    if args.dry:
        return 0

    out = []
    prev = 0
    for s, e, items in groups:
        out.extend(lines[prev:s])
        out.append(render_block(items))
        prev = e + 1
    out.extend(lines[prev:])

    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(out)
    print(f'已写入 re-export 块 -> {os.path.normpath(path)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
