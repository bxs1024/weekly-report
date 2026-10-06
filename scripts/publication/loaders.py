"""站点数据源加载：站点更新、实体观察账本、模型榜单、AIHOT 热点。"""

import json
import os

try:
    from repo_paths import data_path
    from content.util import _cn_today
except ImportError:
    from scripts.repo_paths import data_path
    from scripts.content.util import _cn_today

def load_site_updates():
    """读取网站更新日志。"""
    path = data_path('site_updates.json')
    fallback = [{
        'date': _cn_today(),
        'version': 'V0.1',
        'type': '系统',
        'status': '已上线',
        'title': '网站初始化',
        'summary': '全球互联网百晓生开始自动生成情报简报。',
        'changes': ['自动采集事件', '生成静态情报页面'],
    }]
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, 'r', encoding='utf-8') as f:
            updates = json.load(f)
    except (json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(updates, list):
        return fallback
    cleaned = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        changes = item.get('changes') if isinstance(item.get('changes'), list) else []
        date_value = item.get('date') or ''
        cleaned.append({
            'date': date_value,
            'version': item.get('version') or '',
            'type': item.get('type') or '更新',
            'status': item.get('status') or '已记录',
            'title': item.get('title') or '未命名更新',
            'summary': item.get('summary') or '',
            'changes': [str(c) for c in changes if str(c).strip()],
        })
    return sorted(cleaned or fallback, key=lambda x: x.get('date', ''), reverse=True)

def load_entity_observation_ledger():
    path = data_path('entity_observation_ledger.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def load_model_leaderboard():
    """读取 AIHOT 模型榜数据（由 scripts/fetch_model_leaderboard.py 生成）。"""
    path = data_path('model_leaderboard.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None

def load_aihot_hot():
    """读取 AIHOT 热点榜数据（由 scripts/fetch_aihot_hot.py 生成）。"""
    path = data_path('aihot_hot.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not data.get('items'):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None

def _clean_hot_title(item, max_len=60):
    """AIHOT 标题去时间戳/序号残留，取干净的主标题。"""
    title = (item.get('title') or item.get('list_title') or '').strip()
    if not title:
        return ''
    if len(title) > max_len:
        title = title[:max_len] + '…'
    return title

CHINESE_WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
