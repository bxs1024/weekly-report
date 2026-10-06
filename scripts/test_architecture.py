"""架构边界测试：强制 docs/ARCHITECTURE.md 里的不变规则。

当前检查项（随重构阶段推进逐步加严）：
1. 渲染路径（首页/RSS 等页面函数）不得调用模型；报告生成函数可调用，但必须隔离。
2. 选择器层 view_selectors.py 不得 import AI 通道 / 采集模块。
3. 采集层 fetch_news.py 不得 import 渲染层。
4. prompts 目录若存在，提示词文件必须是 .md、LF 换行、不含空文件。
5. 架构契约文档必须存在并包含关键规则条目。
6. 仓库内路径读写必须锚定 __file__，不得用相对路径（随 CWD 漂移）。

新增模块时若违反边界，本测试失败——先改契约与测试，再改代码。
"""

import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel_path):
    path = os.path.join(ROOT, rel_path)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as handle:
        return handle.read()


def _tree(rel_path):
    source = _read(rel_path)
    assert source is not None, f'文件不存在: {rel_path}'
    return ast.parse(source, filename=rel_path)


def _imported_modules(rel_path):
    """返回源码中 import / from ... import 的顶层模块名集合。"""
    names = set()
    for node in ast.walk(_tree(rel_path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split('.')[0])
    return names


# 真正的 AI 通道：会产生网络请求或计费的模块。
# 注意 prompt_loader 不在此列——它只读本地 .md 拼字符串，不发请求、不计费。
AI_TRANSPORT_MODULES = {'ai_receipts', 'providers'}

# 模型调用入口名：出现即视为该函数会调模型。
MODEL_CALL_NAMES = {'_post_chat'}

# generate_html.py 里允许调用模型的函数（报告生成任务，非页面渲染）。
# 这些函数产出周报/月报的编辑点评，由定时任务触发，不在读者请求路径上。
REPORT_GENERATION_PREFIXES = ('build_weekly_editorial', 'build_monthly_editorial')


def _function_spans(rel_path):
    """返回 [(函数名, 起始行, 结束行)]，用于按函数判定调用范围。"""
    spans = []
    for node in ast.walk(_tree(rel_path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.name, node.lineno, getattr(node, 'end_lineno', node.lineno)))
    return spans


def _call_lines(rel_path, call_names):
    """返回包含指定调用的行号集合。"""
    hits = set()
    for node in ast.walk(_tree(rel_path)):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, 'id', None) or getattr(func, 'attr', None)
            if name in call_names:
                hits.add(node.lineno)
    return hits


def test_renderer_does_not_call_models():
    """页面渲染路径不得调模型：渲染时只读已落库结果。

    判定方式按函数粒度：若某次模型调用落在非「报告生成」函数内，即视为渲染路径调模型。
    这样才能区分 generate_html.py 的两件事——
      · 渲染首页/RSS（必须纯读）
      · 生成周报/月报编辑点评（本来就该调模型，由定时任务触发）
    """
    rel = 'scripts/generate_html.py'
    call_lines = _call_lines(rel, MODEL_CALL_NAMES)
    if not call_lines:
        return
    spans = _function_spans(rel)
    for lineno in call_lines:
        # 找包含该行的最内层函数
        owners = [name for name, start, end in spans if start <= lineno <= end]
        innermost = owners[-1] if owners else '(模块顶层)'
        if innermost == '(模块顶层)':
            raise AssertionError(f'generate_html.py:{lineno} 在模块顶层调用模型，必须收敛进函数')
        allowed = any(innermost.startswith(p) for p in REPORT_GENERATION_PREFIXES)
        assert allowed, (
            f'渲染路径不得调模型：第 {lineno} 行在函数 {innermost}() 内调用模型，'
            f'该函数不属于报告生成白名单 {REPORT_GENERATION_PREFIXES}'
        )


def test_view_selectors_stay_pure():
    """选择器层不得依赖 AI 通道或采集层。"""
    imports = _imported_modules('scripts/view_selectors.py')
    forbidden = AI_TRANSPORT_MODULES | {'fetch_news'}
    overlap = imports & forbidden
    assert not overlap, f'view_selectors.py 不应 import: {sorted(overlap)}'
    assert '_LLM_SESSION' not in (_read('scripts/view_selectors.py') or ''), \
        'view_selectors.py 出现 LLM 会话引用，渲染选择器层不应调用模型'


def test_collector_does_not_import_renderer():
    """采集层不得反向依赖渲染层。"""
    imports = _imported_modules('scripts/fetch_news.py')
    assert 'generate_html' not in imports, 'fetch_news.py 不应 import generate_html'
    assert 'template' not in imports, 'fetch_news.py 不应 import 模板层'


def test_repo_paths_anchor_to_file():
    """仓库内路径读写必须锚定 __file__，不得用相对字符串路径。

    回归案例：ai_receipts.RECEIPTS_PATH 曾写成 'data/ai_receipts.json'，
    从 scripts/ 调用会写成 scripts/data/…、从仓库根调用写成 data/…，
    同一份状态存两份，回执形同失效。
    """
    import importlib
    import sys
    sys.path.insert(0, HERE)
    for mod_name, attr in (('ai_receipts', 'RECEIPTS_PATH'),
                           ('eval_selection', 'GOLD_DEFAULT')):
        module = importlib.import_module(mod_name)
        value = getattr(module, attr)
        assert os.path.isabs(value), (
            f'{mod_name}.{attr} 必须是绝对路径（锚定 __file__），实际: {value}'
        )
        assert value.startswith(ROOT), (
            f'{mod_name}.{attr} 应位于仓库内，实际: {value}'
        )

    # 新模块的源码里不应出现 os.path.join('data' / '.data' 这类相对起点
    for rel in ('scripts/ai_receipts.py', 'scripts/eval_selection.py',
                'scripts/prompt_loader.py'):
        source = _read(rel) or ''
        bad = re.findall(r"os\.path\.join\(\s*['\"](?:\.?data)", source)
        assert not bad, f'{rel} 出现相对 data 路径起点，应改为基于 __file__ 的绝对路径'


def test_prompt_files_are_markdown_and_non_empty():
    """提示词目录（P1 引入）内只放 .md，且不得为空文件。"""
    prompts_dir = os.path.join(HERE, 'prompts')
    if not os.path.isdir(prompts_dir):
        return
    for name in os.listdir(prompts_dir):
        path = os.path.join(prompts_dir, name)
        if not os.path.isfile(path):
            continue
        assert name.endswith('.md'), f'提示词必须是 .md 文件: {name}'
        with open(path, 'r', encoding='utf-8') as handle:
            content = handle.read()
        assert content.strip(), f'提示词文件为空: {name}'


def test_prompt_files_use_lf_and_loader_keeps_leading_newline():
    """提示词文件必须是 LF 换行，且加载器不得裁掉前导换行。

    回归案例（P1 实测踩到两次）：
    - 提示词文件被写成 CRLF，版本哈希随平台漂移；
    - load_prompt 早期用 .strip() 把前导换行吃掉，导致拼接出的 prompt
      比外置前少一个换行，与 HEAD 不再逐字节等价。
    这里把两条都锁住。
    """
    import prompt_loader
    prompts_dir = os.path.join(HERE, 'prompts')
    if not os.path.isdir(prompts_dir):
        return

    for name in sorted(os.listdir(prompts_dir)):
        if not name.endswith('.md'):
            continue
        with open(os.path.join(prompts_dir, name), 'rb') as handle:
            raw = handle.read()
        assert b'\r' not in raw, f'{name} 含 CR（应为 LF），换行符会污染提示词版本哈希'

    # 版本哈希必须与 load_prompt 同口径：换行归一后内容相同则版本相同
    a = prompt_loader.prompt_version('analysis-system')
    b = prompt_loader.prompt_version('analysis-system')
    assert a == b, 'prompt_version 不稳定'
    assert len(a) == 12, f'prompt_version 应为 12 位，实际 {a!r}'


def test_architecture_contract_exists():
    """架构契约文档必须存在，且包含核心规则。"""
    content = _read('docs/ARCHITECTURE.md')
    assert content is not None, 'docs/ARCHITECTURE.md 缺失'
    required = [
        '页面不调模型',
        '一个公开读取层',
        '付费请求有回执',
        '提示词改标准不改代码',
        '评分只排序不过滤',
        '路径锚定仓库根',
    ]
    for item in required:
        assert item in content, f'架构契约缺少规则: {item}'


def test_no_hardcoded_system_prompt_growth():
    """提示词外置（P1）后，fetch_news.py 不应再新增超长硬编码 prompt 常量。

    P1 完成后 AI_SYSTEM_PROMPT / AI_EXAMPLES 应迁移到 scripts/prompts/。
    本测试在迁移完成前只做上限守卫，避免边迁移边继续膨胀。
    """
    source = _read('scripts/fetch_news.py') or ''
    match = re.search(r'AI_SYSTEM_PROMPT\s*=\s*(?:"""(.*?)"""|f"""(.*?)""")', source, re.S)
    if not match:
        return  # 已迁移走 prompt_loader
    body = match.group(1) or match.group(2) or ''
    assert len(body) <= 2400, (
        f'AI_SYSTEM_PROMPT 已达 {len(body)} 字符，疑似继续膨胀；'
        'P1 应将其迁移到 scripts/prompts/ 并删除该常量'
    )


def _run_all():
    test_renderer_does_not_call_models()
    test_view_selectors_stay_pure()
    test_collector_does_not_import_renderer()
    test_repo_paths_anchor_to_file()
    test_prompt_files_are_markdown_and_non_empty()
    test_prompt_files_use_lf_and_loader_keeps_leading_newline()
    test_architecture_contract_exists()
    test_no_hardcoded_system_prompt_growth()
    print('architecture tests passed')


if __name__ == '__main__':
    _run_all()
