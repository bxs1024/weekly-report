"""搬迁辅助：分析 fetch_news.py 里各顶层定义之间的全局名字依赖。

背景：Python 函数搬到新模块后，其引用的模块级名字会在**新模块**的命名空间里
查找。因此搬迁一段代码时，必须同时带上（或导入）它引用的所有模块级名字，
否则只在走到那条代码路径时才抛 NameError——测试未必覆盖得到。

本脚本用 ast 静态分析：给定行区间，列出该区间内所有顶层定义「自由引用」到的
模块级名字，并标注这些名字定义在文件的哪一行。据此可判断搬迁需要哪些配套。

用法：
    python scripts/_move_analyze.py 1705 1832          # 分析某区间
    python scripts/_move_analyze.py --list             # 列出所有顶层定义
    python scripts/_move_analyze.py --cycles           # 列出跨区间的反向依赖
"""

import argparse
import ast
import builtins
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch_news.py')


def load():
    with open(SRC, encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src)
    return src, tree


def top_defs(tree):
    """返回 [(name, node, start, end)]，仅顶层函数/类/赋值。"""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.name, node, node.lineno, node.end_lineno))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, node, node.lineno, node.end_lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, node, node.lineno, node.end_lineno))
    return out


def collect_module_names(tree):
    """所有顶层名字 -> 定义行号。"""
    names = {}
    for name, node, start, _end in top_defs(tree):
        names.setdefault(name, start)
    # import 进来的名字也算模块级可用
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.setdefault((a.asname or a.name).split('.')[0], node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.setdefault(a.asname or a.name, node.lineno)
    return names


def free_names(node):
    """收集节点内被引用但未在节点内部绑定的名字。"""
    bound = set()
    used = set()

    class V(ast.NodeVisitor):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            else:
                used.add(n.id)

        def visit_FunctionDef(self, n):
            # 函数名本身在外部绑定；内部参数与局部名不算自由
            for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs:
                bound.add(a.arg)
            if n.args.vararg:
                bound.add(n.args.vararg.arg)
            if n.args.kwarg:
                bound.add(n.args.kwarg.arg)
            for sub in n.body:
                self.visit(sub)
            for d in n.decorator_list:
                self.visit(d)
            if n.returns:
                self.visit(n.returns)

        def visit_AsyncFunctionDef(self, n):
            self.visit_FunctionDef(n)

        def visit_Lambda(self, n):
            for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs:
                bound.add(a.arg)
            self.visit(n.body)

        def visit_ClassDef(self, n):
            for sub in n.body:
                self.visit(sub)
            for d in n.decorator_list:
                self.visit(d)

        def visit_comprehension(self, n):
            self.visit(n.iter)
            for sub in n.ifs:
                self.visit(sub)
            self.visit(n.target)

        def visit_ExceptHandler(self, n):
            if n.type:
                self.visit(n.type)
            if n.name:
                bound.add(n.name)
            for sub in n.body:
                self.visit(sub)

    V().visit(node)
    return used - bound


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('start', nargs='?', type=int)
    ap.add_argument('end', nargs='?', type=int)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--cycles', action='store_true')
    ap.add_argument('--blocks', action='store_true')
    args = ap.parse_args()

    _src, tree = load()
    defs = top_defs(tree)
    mod_names = collect_module_names(tree)

    if args.list:
        for name, _n, s, e in sorted(defs, key=lambda x: x[2]):
            print(f'{s:5d}-{e:5d}  {name}')
        return 0

    if args.cycles:
        # 找出「A 区间引用 B 区间定义」的跨块依赖
        blocks = [
            ('util', 97, 117), ('meta', 119, 171), ('company_watch', 172, 490),
            ('classify', 491, 1279), ('dedupe', 1280, 1620), ('util2', 1621, 1704),
            ('collect', 1705, 1832), ('html_fallback', 1833, 2408),
            ('filter', 2409, 2537), ('llm', 2538, 2882),
            ('agents', 2883, 3649), ('og_image', 3650, 3690), ('main', 3691, 4221),
        ]

        def block_of(line):
            for n, a, b in blocks:
                if a <= line <= b:
                    return n
            return 'header'

        # 定义名 -> 所属块
        name_block = {}
        for name, _n, s, _e in defs:
            name_block.setdefault(name, block_of(s))

        # 块间依赖矩阵
        edge = {}
        for name, node, s, _e in defs:
            mine = block_of(s)
            for f in free_names(node):
                if f in name_block and name_block[f] != mine:
                    edge.setdefault((mine, name_block[f]), set()).add(f)

        print('# 块间依赖（谁 -> 依赖谁）')
        for (a, b), names in sorted(edge.items(), key=lambda x: (-len(x[1]), x[0])):
            print(f'  {a:14s} -> {b:14s} ({len(names):3d})  {sorted(names)[:8]}')
        return 0

    if args.blocks:
        # 按文件里的 banner 注释（# ===\n# 标题\n# ===）自动切块，列出每块的定义
        with open(SRC, encoding='utf-8') as f:
            raw = f.readlines()
        marks = []
        for i, ln in enumerate(raw):
            if ln.startswith('# ===') and i + 2 < len(raw) and raw[i + 2].startswith('# ==='):
                title = raw[i + 1].lstrip('#').strip()
                marks.append((i + 1, title))  # 1-based 行号（标题行）
        for idx, (line, title) in enumerate(marks):
            end = marks[idx + 1][0] - 3 if idx + 1 < len(marks) else 10 ** 9
            names = [(n, s) for n, _nd, s, _e in defs if line <= s <= end]
            print(f'\n## [{line}] {title}  ({len(names)} 个定义)')
            for n, s in names:
                print(f'   {s:5d}  {n}')
        return 0

    if args.start is None:
        ap.error('need start end, or --list/--cycles/--blocks')

    end = args.end or args.start
    inside = [d for d in defs if args.start <= d[2] <= end]
    print(f'# 区间 {args.start}-{end}：{len(inside)} 个顶层定义')
    needed = {}
    for name, node, s, e in inside:
        for f in free_names(node):
            if f in mod_names and not (args.start <= mod_names[f] <= end):
                needed.setdefault(f, set()).add(name)
    print(f'# 需要外部提供的模块级名字：{len(needed)}')
    for f, users in sorted(needed.items(), key=lambda x: mod_names[x[0]]):
        print(f'  {f:32s} 定义于 {mod_names[f]:5d}  被 {len(users)} 处引用')
    return 0


if __name__ == '__main__':
    sys.exit(main())
