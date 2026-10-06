"""提示词加载器：把全部 AI 提示词从代码里搬到 scripts/prompts/*.md。

设计要点（对齐 AIHOT industry/prompts/ 的做法）：
- 提示词改标准不改代码：改 .md 即可，不需要动 Python。
- 版本 = 归一化后内容的 sha256 前 12 位；写进缓存键，改提示词自动让旧缓存
  失效，不再需要手工递增 EDITORIAL_PROMPT_VERSION。
- 换行统一按 LF 处理（CRLF/CR 都归一），版本号只反映内容变化，不反映编辑器
  或平台造成的换行符风格差异。
- 支持 `{{> 文件名}}` 引用另一份提示词（如 group-pair 引用 group-definitions）。
- 缺失文件时报错并给出明确路径，不静默返回空串。

用法：
    from prompt_loader import load_prompt, prompt_version
    text = load_prompt('selection-score')
    ver = prompt_version('selection-score')
    cache_key = f'{period}:{input_hash}:{ver}'
"""

import hashlib
import os
import re

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prompts')

_INCLUDE_RE = re.compile(r'\{\{>\s*([A-Za-z0-9_\-]+)\s*\}\}')
_MAX_INCLUDE_DEPTH = 5


def prompts_dir():
    return PROMPTS_DIR


def prompt_path(name):
    """提示词名 → 绝对路径。名称不带 .md 后缀。"""
    clean = name[:-3] if name.endswith('.md') else name
    if not clean or '/' in clean or '\\' in clean:
        raise ValueError(f'非法提示词名: {name!r}')
    return os.path.join(PROMPTS_DIR, f'{clean}.md')


def _read_prompt_text(name):
    """读取提示词正文并归一化换行。

    以二进制读入后手工归一，而不是用文本模式——因为：
    - 文本模式下 Python 会把 CRLF 转成 LF，但 prompt_version 走二进制读，
      两者口径不一致，会让「版本号」和「实际喂给模型的内容」脱钩。
    - 统一在这里把 CRLF/CR 归一成 LF，保证任何平台的检出结果都一致，
      版本号只反映内容变化，不反映换行符风格。
    """
    path = prompt_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f'提示词文件不存在: {path}（提示词统一放在 scripts/prompts/ 下，后缀 .md）'
        )
    with open(path, 'rb') as handle:
        raw = handle.read()
    text = raw.decode('utf-8')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def load_prompt(name, _depth=0):
    """读取提示词原文，并展开 `{{> 其他提示词}}` 引用。

    展开是"文本拼接"，不做变量替换；站名等行业变量由调用方传入的
    上下文决定（与 AIHOT 的 {{siteName}} 由渲染层填充一致）。

    只去掉**尾部**换行（文件末尾多一个空行不应影响提示词）。不做完整
    strip——前导换行是内容的一部分，历史上 AI_EXAMPLES 就以换行开头，
    调用方靠它和拼接符组成段落间隔。
    """
    if _depth > _MAX_INCLUDE_DEPTH:
        raise RuntimeError(f'提示词引用层级过深（>{_MAX_INCLUDE_DEPTH}）: {name}')

    content = _read_prompt_text(name)

    def _replace(match):
        return load_prompt(match.group(1), _depth + 1)

    return _INCLUDE_RE.sub(_replace, content).rstrip('\n')


def prompt_hashes():
    """返回 {提示词名: sha256 前 12 位}，用于版本与审计。

    哈希的是 load_prompt 归一化后的文本（LF 换行、尾部无空行），
    即「实际喂给模型的内容」——换行符风格变化不会造成版本跳动。
    """
    if not os.path.isdir(PROMPTS_DIR):
        return {}
    result = {}
    for name in sorted(os.listdir(PROMPTS_DIR)):
        if not name.endswith('.md'):
            continue
        try:
            content = load_prompt(name[:-3])
        except (OSError, ValueError):
            continue
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]
        result[name[:-3]] = digest
    return result


def prompt_version(name):
    """单个提示词的版本号（内容哈希前 12 位）。

    与 load_prompt 同口径：哈希归一化后的文本，保证「改了会失效、
    没改不会失效」。换行符风格差异不产生新版本。
    """
    content = load_prompt(name)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]


def prompts_bundle_version():
    """全部提示词的合成版本：任一提示词变更都会改变它。

    用于"整站级"缓存键（例如 run_metrics 记录本次运行基于哪一版提示词）。
    """
    hashes = prompt_hashes()
    if not hashes:
        return 'none'
    joined = '|'.join(f'{k}:{v}' for k, v in sorted(hashes.items()))
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()[:12]


def render_prompt(name, variables=None):
    """加载提示词并替换 `{{变量}}` 占位符。

    未提供的变量保持原样（便于发现漏填），不静默清空。
    """
    text = load_prompt(name)
    if not variables:
        return text
    for key, value in variables.items():
        text = text.replace('{{' + key + '}}', str(value))
    return text
