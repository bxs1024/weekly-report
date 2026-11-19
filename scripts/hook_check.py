#!/usr/bin/env python
"""PostToolUse 检查：只检查本次改动的那个文件。

为什么要有这个文件（2026-11-19）：
原来的 hook 写死了 `ast.parse(open('scripts/fetch_news.py'))`——改任何别的文件，
它检查的都是 fetch_news.py，永远绿。这就是「检查存在」不等于「检查生效」。
第二个 hook 每次编辑都跑 `generate_html.py`（30s），会把 docs/index.html 写脏，
本地数据日期与当前日期不齐时还会裁掉往期卡片（11-09 实测少 249 行）。

现在只做一件事：拿到本次改动的文件，是 .py 就做语法解析。
渲染结果对不对交给全量测试和 CI，不在编辑时做。

用法（.claude/settings.json 的 PostToolUse）：python scripts/hook_check.py
从 stdin 读 hook 的 JSON；退出码非 0 会让 Agent 看到失败。
"""
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _target_path(payload):
    """从 hook 输入里取被改动的文件路径。取不到就返回空串（不误拦）。"""
    tool_input = payload.get('tool_input') or {}
    for key in ('file_path', 'path', 'notebook_path'):
        value = tool_input.get(key)
        if value:
            return value
    return ''


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    target = _target_path(payload)
    if not target:
        return 0

    path = Path(target)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        return 0

    # 只查 Python 语法；其它文件类型交给测试与 CI，不在编辑时猜。
    if path.suffix != '.py':
        return 0

    try:
        ast.parse(path.read_text(encoding='utf-8'))
    except SyntaxError as exc:
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:
            shown = path
        print(f'语法错误 {shown}:{exc.lineno}: {exc.msg}', file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f'无法按 UTF-8 读取 {path}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
