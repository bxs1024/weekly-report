"""AI 付费回执：每次模型调用先记回执，重试复用已付过钱的结果。

对应 AIHOT 的 `providers/receipts.ts`（docs/ARCHITECTURE.md 的「付费请求有回执」）。
解决的问题：采集/生成任务会重试、进程会重启、脚本会被重复运行。
没有回执时，同一份输入会被反复送进模型，重复付费且浪费额度。

设计：
- 回执键 = sha256(prompt 版本 + 模型 + 归一化输入)。同键即同输入同标准，
  直接复用已有结果。
- 只缓存**成功**结果；失败不写回执（否则会永久卡住一条数据）。
- 存储 `<仓库根>/data/ai_receipts.json`，与 `editorial_cache.json` 同目录同风格。
- 提供按 prompt 版本裁剪：提示词改版后旧回执不再匹配（键已含版本），
  但仍可显式清理。

用法：
    from ai_receipts import receipt_key, receipt_get, receipt_put
    key = receipt_key('score', model_name, payload)
    hit = receipt_get(key)
    if hit is None:
        result = call_model(...)
        receipt_put(key, result, prompt_kind='score', model=model_name)
"""

import hashlib
import json
import os
import time

try:
    from prompt_loader import prompt_version
except ImportError:
    from scripts.prompt_loader import prompt_version

_MAX_ENTRIES = 20000

# 仓库根下的 data/。基于 __file__ 定位，不依赖调用进程 CWD——
# 与项目既有约定一致（fetch_aihot_hot.py / fetch_model_leaderboard.py 同款写法）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECEIPTS_PATH = os.path.join(_REPO_ROOT, 'data', 'ai_receipts.json')

# 进程内缓存，避免同一批处理里反复读盘
_MEMO = {}


def _path():
    return RECEIPTS_PATH


def _load():
    if _MEMO.get('_loaded'):
        return _MEMO['_store']
    store = {'version': 1, 'receipts': {}}
    try:
        with open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get('receipts'), dict):
            store = data
    except (OSError, ValueError):
        pass
    _MEMO['_store'] = store
    _MEMO['_loaded'] = True
    return store


def _save(store):
    try:
        os.makedirs(os.path.dirname(_path()) or '.', exist_ok=True)
        with open(_path(), 'w', encoding='utf-8') as handle:
            json.dump(store, handle, ensure_ascii=False, separators=(',', ':'))
    except OSError:
        pass


def _normalize_payload(payload):
    """把输入归一化成稳定字符串：dict/list 排序键，避免字典顺序导致的假不命中。"""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def receipt_key(prompt_kind, model, payload):
    """回执键。含提示词版本，改提示词自动失效。"""
    try:
        version = prompt_version(prompt_kind)
    except Exception:
        version = 'unknown'
    material = f'{prompt_kind}:{version}:{model}:{_normalize_payload(payload)}'
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]


def receipt_get(key):
    """取已付过钱的结果；未命中或结果为空返回 None。"""
    entry = (_load().get('receipts') or {}).get(key)
    if not entry:
        return None
    return entry.get('result')


def receipt_put(key, result, prompt_kind='', model=''):
    """写入回执。只写成功结果（result 为 None 时不写）。"""
    if result is None:
        return
    store = _load()
    receipts = store.setdefault('receipts', {})
    receipts[key] = {
        'result': result,
        'prompt_kind': prompt_kind,
        'model': model,
        'at': int(time.time()),
    }
    _trim(receipts)
    _save(store)


def _trim(receipts):
    """超出上限时按时间裁掉最旧的回执，避免文件无限增长。"""
    if len(receipts) <= _MAX_ENTRIES:
        return
    ordered = sorted(receipts.items(), key=lambda kv: kv[1].get('at', 0))
    for key, _ in ordered[:len(receipts) - _MAX_ENTRIES]:
        receipts.pop(key, None)


def receipt_stats():
    """回执统计，用于 run_metrics 与运维观察。"""
    receipts = _load().get('receipts') or {}
    by_kind = {}
    for entry in receipts.values():
        kind = entry.get('prompt_kind') or 'unknown'
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {'total': len(receipts), 'by_kind': by_kind}


def receipt_purge(prompt_kind=None):
    """清理回执：指定 prompt_kind 只清该类，不指定清全部。返回清理条数。"""
    store = _load()
    receipts = store.get('receipts') or {}
    if prompt_kind is None:
        count = len(receipts)
        store['receipts'] = {}
    else:
        keys = [k for k, v in receipts.items() if (v.get('prompt_kind') or '') == prompt_kind]
        for key in keys:
            receipts.pop(key, None)
        count = len(keys)
    _save(store)
    return count


def reset_memo():
    """测试用：清进程内缓存，强制下次重新读盘。"""
    _MEMO.clear()
