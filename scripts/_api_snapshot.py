"""搬迁安全网：对 fetch_news 的公开 API 做快照并比对。

P4 拆包的不变量是「只搬家、不改动」。本工具在搬迁前把每个顶层名字的
源码/取值哈希存下来，搬迁后逐项比对，确保：
  1. 每个名字仍然能从 fetch_news 取到（re-export 层完整）；
  2. 其源码/取值与搬迁前**逐字节一致**（没有顺手改动逻辑）。

同时提供静态检查：给定新模块，验证其顶层函数引用的所有模块级名字都能在
该模块命名空间解析（防止「搬完才在特定代码路径抛 NameError」）。

用法：
    python scripts/_api_snapshot.py --save           # 搬迁前：存快照
    python scripts/_api_snapshot.py --verify         # 搬迁后：比对
    python scripts/_api_snapshot.py --checkmod MOD   # 静态检查某模块的全局解析

--mod 指定目标模块（默认 fetch_news）；快照按模块分文件存放，互不覆盖。
"""

import argparse
import ast
import hashlib
import inspect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, '..', '.data')

# 快照按模块分文件：fetch_news 那份是 P4 前半段的基线，不能被后半段的
# generate_html 覆盖。缺省仍为 fetch_news，保持既有用法不变。
DEFAULT_MOD = 'fetch_news'


def _snapshot_path(mod):
    # fetch_news 沿用既有文件名，避免既有基线失效。
    if mod == DEFAULT_MOD:
        return os.path.join(SNAP_DIR, 'p4_api_snapshot.json')
    return os.path.join(SNAP_DIR, f'p4_api_snapshot.{mod}.json')


sys.path.insert(0, HERE)


def _canonical(obj):
    """把取值转成稳定字符串：set 需排序（repr 顺序受哈希随机化影响）。"""
    if isinstance(obj, (set, frozenset)):
        try:
            return 'set{' + ','.join(sorted(repr(x) for x in obj)) + '}'
        except TypeError:
            return 'set{' + ','.join(sorted(map(str, obj))) + '}'
    if isinstance(obj, dict):
        return 'dict{' + ','.join(f'{k!r}:{_canonical(v)}' for k, v in sorted(obj.items(), key=lambda kv: repr(kv[0]))) + '}'
    if isinstance(obj, (list, tuple)):
        return type(obj).__name__ + '[' + ','.join(_canonical(x) for x in obj) + ']'
    return repr(obj)


def _digest(obj):
    try:
        src = inspect.getsource(obj)
    except (OSError, TypeError):
        # 无源码（常量/实例）。只对可稳定序列化的数据做摘要，
        # 其余（如 requests.Session）跳过，避免对象地址造成误报。
        if isinstance(obj, (set, frozenset, dict, list, tuple, str, int, float, bool, type(None))):
            src = _canonical(obj)
        else:
            return None
    return hashlib.sha256(src.encode('utf-8')).hexdigest()[:16]


def snapshot_module(mod):
    out = {}
    for name in dir(mod):
        if name.startswith('__'):
            continue
        obj = getattr(mod, name)
        if inspect.ismodule(obj):
            continue
        d = _digest(obj)
        if d is not None:
            out[name] = d
    return out


def cmd_save(mod=DEFAULT_MOD):
    import importlib
    m = importlib.import_module(mod)
    data = snapshot_module(m)
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(_snapshot_path(mod), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f'snapshot saved: {len(data)} names -> {os.path.normpath(_snapshot_path(mod))}')
    return 0


def cmd_verify(mod=DEFAULT_MOD):
    import importlib
    m = importlib.import_module(mod)
    with open(_snapshot_path(mod), encoding='utf-8') as f:
        base = json.load(f)
    now = snapshot_module(m)

    # 有意改动的白名单：{名字: 原因}。与基线一并存，verify 时豁免并回显原因，
    # 避免「已知必要改动」每轮都报红、把真正的意外改动淹没在噪音里。
    known = base.pop('_known_changes', {})

    missing = sorted(set(base) - set(now))
    changed = sorted(n for n in set(base) & set(now) if base[n] != now[n])
    added = sorted(set(now) - set(base))
    exempt = [n for n in changed if n in known]
    changed = [n for n in changed if n not in known]
    if exempt:
        print('[info] 白名单内（有意改动）:')
        for n in exempt:
            print(f'   ~ {n}: {known[n]}')

    print(f'baseline={len(base)}  now={len(now)}')
    if missing:
        print(f'\n[FAIL] 缺失 {len(missing)} 个名字（re-export 不完整）:')
        for n in missing:
            print('   -', n)
    if changed:
        print(f'\n[FAIL] {len(changed)} 个名字的源码/取值发生变化（疑似改动逻辑）:')
        for n in changed:
            print(f'   ~ {n}: {base[n]} -> {now[n]}')
    if added:
        print(f'\n[info] 新增 {len(added)} 个名字: {added[:20]}')
    if not missing and not changed:
        print(f'\n[OK] API 快照完全一致（{mod}）：只搬家、未改动')
        return 0
    return 1


def _toplevel_stmts(body):
    """展开模块级语句，递归进入 try/if/with 等容器（import 常写在 try 里）。"""
    for node in body:
        yield node
        if isinstance(node, ast.Try):
            yield from _toplevel_stmts(node.body)
            for h in node.handlers:
                yield from _toplevel_stmts(h.body)
            yield from _toplevel_stmts(node.orelse)
            yield from _toplevel_stmts(node.finalbody)
        elif isinstance(node, (ast.If, ast.With)):
            yield from _toplevel_stmts(node.body)
            yield from _toplevel_stmts(getattr(node, 'orelse', []) or [])


def cmd_checkmod(modname):
    """静态检查：模块内顶层函数引用的全局名是否都能在模块命名空间解析。"""
    import ast
    import importlib

    path = os.path.join(HERE, modname.replace('.', os.sep) + '.py')
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())

    try:
        mod = importlib.import_module(modname)
        ns = set(dir(mod))
    except Exception as exc:  # 补 import 阶段模块尚未可导入，静态检查仍要做
        print(f'[warn] {modname} 当前不可导入（{type(exc).__name__}: {exc}），仅做静态检查')
        ns = set()

    # 模块级绑定：函数/类/赋值/import（含 try 内的）
    local = set()
    for node in _toplevel_stmts(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            local.add(node.target.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                local.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                local.add(a.asname or a.name)

    import builtins as _b
    known = local | set(dir(_b))

    problems = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound, used = set(), set()

        class V(ast.NodeVisitor):
            def visit_Name(self, n):
                (bound if isinstance(n.ctx, ast.Store) else used).add(n.id)

            def visit_FunctionDef(self, n):
                bound.add(n.name)
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

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Lambda(self, n):
                for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs:
                    bound.add(a.arg)
                self.visit(n.body)

            def visit_ClassDef(self, n):
                for sub in n.body:
                    self.visit(sub)

            def visit_Import(self, n):
                for a in n.names:
                    bound.add((a.asname or a.name).split('.')[0])

            def visit_ImportFrom(self, n):
                for a in n.names:
                    bound.add(a.asname or a.name)

            def visit_ExceptHandler(self, n):
                if n.type:
                    self.visit(n.type)
                if n.name:
                    bound.add(n.name)
                for sub in n.body:
                    self.visit(sub)

        V().visit(node)
        unresolved = sorted((used - bound) - known)
        if unresolved:
            problems.append((node.name, unresolved))

    if problems:
        print(f'[FAIL] {modname}: {len(problems)} 个函数引用了无法解析的全局名')
        for fn, names in problems:
            print(f'   {fn}: {names}')
        return 1
    print(f'[OK] {modname}: 顶层函数引用的全局名全部可解析（ns={len(ns)}）')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--checkmod')
    ap.add_argument('--mod', default=DEFAULT_MOD)
    args = ap.parse_args()
    if args.save:
        return cmd_save(args.mod)
    if args.verify:
        return cmd_verify(args.mod)
    if args.checkmod:
        return cmd_checkmod(args.checkmod)
    ap.error('pass --save / --verify / --checkmod MOD')
    return 2


if __name__ == '__main__':
    sys.exit(main())
