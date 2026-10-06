"""27 家重点公司的观察范围契约。

从 entity_pool 读每家公司的 scope_industries / scope_regions / vertical，供采集侧
把契约补进信源配置。公司新闻的实际抓取走 sources/html_fallback.fetch_company_news。"""

import json
import sys

try:
    from constants import COMPANY_ALIASES, SECTOR_SCOPE_MAP
    from repo_paths import data_path
except ImportError:
    from scripts.constants import COMPANY_ALIASES, SECTOR_SCOPE_MAP
    from scripts.repo_paths import data_path


def _load_company_scope_contracts(path=None):
    """从 entity_pool 读取公司观察范围契约。

    读不到文件时**返回空字典但不改语义**：调用方（capture 层）据此退化为
    「不施加额外范围约束」，与历史行为一致。但会打一行警告——此前静默吞掉
    异常，配合相对路径 bug 会让整站范围契约悄悄失效且无人察觉。
    """
    path = path or data_path('entity_pool.json')
    try:
        with open(path, encoding='utf-8') as f:
            pool = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'⚠️ 读取 entity_pool 失败，公司范围契约退化为空: {path} ({type(exc).__name__})',
              file=sys.stderr)
        return {}
    contracts = {}
    for entity in pool.get('entities') or []:
        industries = SECTOR_SCOPE_MAP.get(entity.get('sector'), [])
        contract = {
            'scope_industries': industries,
            'scope_regions': [entity['region']] if entity.get('region') else [],
            'vertical': entity.get('sector', ''),
        }
        for name in [entity.get('name'), *(entity.get('aliases') or [])]:
            if name:
                contracts[name.lower()] = contract
    return contracts

COMPANY_SCOPE_CONTRACTS = _load_company_scope_contracts()

def _apply_company_scope_contract(cfg):
    names = [
        cfg.get('company_name'),
        cfg.get('name'),
        *(COMPANY_ALIASES.get(cfg.get('company_name'), [])),
        *(COMPANY_ALIASES.get(cfg.get('name'), [])),
    ]
    for name in names:
        contract = COMPANY_SCOPE_CONTRACTS.get((name or '').lower())
        if not contract:
            continue
        for key, value in contract.items():
            if value and not cfg.get(key):
                cfg[key] = value
        break
    return cfg
