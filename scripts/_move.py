"""搬迁工具：把 fetch_news.py 里的顶层定义按名字**原文搬运**到新模块。

为什么不手工复制：4221 行的文件里手工搬代码极易抄错。本工具按 ast 定位
定义（含装饰器与紧邻的说明注释），把源码文本原样搬走，并在原文件留下
`# >>> MOVED ...` 标记，保证「只搬家、不改动」。

用法：
    # 预览：只报告将搬运的行区间，不写文件
    python scripts/_move.py --names HEADERS RSS_SOURCES --out scripts/source_config.py --dry

    # 实际搬运
    python scripts/_move.py --names HEADERS RSS_SOURCES --out scripts/source_config.py \
        --header "信源与常量表（纯数据，无逻辑）"
"""

import argparse
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'fetch_news.py')


def parse(path):
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    return lines, ast.parse(''.join(lines))


def node_span(node, lines):
    """返回 (start_idx, end_idx) 0-based，含装饰器与紧邻说明注释（不跨空行、跳过 banner）。"""
    start = node.lineno - 1
    if getattr(node, 'decorator_list', None):
        start = min(start, min(d.lineno for d in node.decorator_list) - 1)
    # 向上吃紧邻注释（遇到空行停；banner 行 # ==== 不吃）
    i = start - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped == '':
            break
        if stripped.startswith('#') and not stripped.lstrip('#').strip().startswith('==='):
            start = i
            i -= 1
            continue
        break
    return start, node.end_lineno - 1


def find_defs(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', nargs='+', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--header', default='')
    ap.add_argument('--src', default=SRC, help='源文件（默认 fetch_news.py；拆 generate_html 时显式传入）')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--keep-comments', action='store_true',
                    help='原文件里保留注释（默认只留 MOVED 标记）')
    args = ap.parse_args()

    src_path = args.src if os.path.isabs(args.src) else os.path.join(HERE, args.src)
    lines, tree = parse(src_path)
    defs = find_defs(tree)

    missing = [n for n in args.names if n not in defs]
    if missing:
        print(f'ERROR: 未找到定义: {missing}', file=sys.stderr)
        return 2

    spans = []
    for n in args.names:
        s, e = node_span(defs[n], lines)
        spans.append((s, e, n))
    spans.sort()

    # 重叠检查
    for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
        if s2 <= e1:
            print(f'ERROR: 区间重叠 {n1}({s1+1}-{e1+1}) 与 {n2}({s2+1}-{e2+1})', file=sys.stderr)
            return 2

    print(f'# 将搬运 {len(spans)} 个定义 -> {args.out}')
    for s, e, n in spans:
        print(f'  {s+1:5d}-{e+1:5d}  {n}')

    if args.dry:
        return 0

    # 生成新模块
    body = ''.join(''.join(lines[s:e + 1]).rstrip('\n') + '\n\n' for s, e, _n in spans)
    hdr = f'"""{args.header}"""\n\n' if args.header else ''
    out_path = os.path.join(os.path.dirname(HERE), args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(hdr + body.rstrip('\n') + '\n')
    print(f'\n写入 {out_path}')

    # 原文件替换为标记
    out_lines = list(lines)
    for s, e, n in reversed(spans):
        marker = f'# >>> MOVED: {n} -> {os.path.basename(out_path)}\n'
        out_lines[s:e + 1] = [marker] if not args.keep_comments else [marker]
    with open(src_path, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(out_lines)
    print(f'原文件已就地替换为 {len(spans)} 个 MOVED 标记')
    return 0


if __name__ == '__main__':
    sys.exit(main())
