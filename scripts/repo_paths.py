"""仓库内路径的统一锚点。

架构契约「路径锚定仓库根」（docs/ARCHITECTURE.md）：读写 data/ 等仓库内
路径必须基于 __file__ 推导，禁止用相对路径——相对路径随调用进程 CWD
漂移，会把同一份状态写成两份（例如从 scripts/ 启动时写进 scripts/data/，
从仓库根启动时写进 data/），且往往伴随静默的 except 让故障无人察觉。

所有需要读写仓库内文件的模块都应从这里取路径，不要再各自复制
_REPO_ROOT 的推导逻辑。
"""

import os

# scripts/repo_paths.py -> scripts/ -> 仓库根
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, 'data')
DOCS_DIR = os.path.join(REPO_ROOT, 'docs')


def repo_path(*parts):
    """拼出仓库根下的绝对路径。"""
    return os.path.join(REPO_ROOT, *parts)


def data_path(*parts):
    """拼出仓库 data/ 下的绝对路径。"""
    return os.path.join(DATA_DIR, *parts)


def docs_path(*parts):
    """拼出仓库 docs/ 下的绝对路径。"""
    return os.path.join(DOCS_DIR, *parts)
