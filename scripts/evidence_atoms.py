"""Collapse duplicate reporting into independent evidence atoms."""

import re
from datetime import datetime
from functools import lru_cache


def _event_date(event):
    return (event.get('date') or '')[:10]


def _event_type(event):
    types = event.get('event_types') or ['other']
    return types[0] if types else 'other'


def _event_companies(event):
    companies = []
    if event.get('company_name'):
        companies.append(event['company_name'])
    for company in event.get('companies') or []:
        if company and company not in companies:
            companies.append(company)
    return companies


def _event_topic(event):
    topic = event.get('trend_topic') or event.get('topic') or ''
    if topic:
        return topic
    label = event.get('insight_label') or ''
    if label:
        return label
    title = event.get('display_title') or event.get('summary_short') or event.get('title') or ''
    return ' '.join(title.split()[:4])


def _source_family(event):
    return event.get('source_tier') or event.get('source_role') or event.get('source') or ''


def atom_key(event):
    """Return a product-level evidence key.

    The key describes the fact, not the publisher. Source independence is
    measured after duplicate articles have been collapsed.
    """
    return (
        _event_type(event),
        tuple(sorted(name.lower() for name in _event_companies(event))),
        _event_topic(event),
    )


FACT_STOP_WORDS = {
    'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on', 'and', 'with', 'from',
    'raises', 'raised', 'funding', 'acquires', 'acquire', 'acquisition',
    'launches', 'launch', 'announces', 'new', 'company', 'startup', 'report',
}


def _fact_tokens(event):
    title = event.get('title') or event.get('display_title') or event.get('summary_short') or ''
    tokens = re.findall(r'[a-z0-9][a-z0-9.+-]*|[\u4e00-\u9fff]{2,}', title.lower())
    return {token for token in tokens if token not in FACT_STOP_WORDS and len(token) > 1}


@lru_cache(maxsize=16384)
def _parse_date(value):
    """缓存日期解析。_dates_close 被 events_describe_same_fact 以 O(n²) 调用，
    每次两次 datetime.strptime，实测 78 万次、占全量生成约 30s。"""
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None


def _dates_close(left, right, max_days=2):
    a = _parse_date(_event_date(left))
    b = _parse_date(_event_date(right))
    if a is None or b is None:
        return True
    return abs((a - b).days) <= max_days


def events_describe_same_fact(left, right):
    if _event_type(left) != _event_type(right) or not _dates_close(left, right):
        return False
    left_companies = {name.lower() for name in _event_companies(left)}
    right_companies = {name.lower() for name in _event_companies(right)}
    if left_companies and right_companies:
        return bool(left_companies & right_companies)
    left_tokens = _fact_tokens(left)
    right_tokens = _fact_tokens(right)
    overlap = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(overlap) >= 3 and len(overlap) / max(len(union), 1) >= 0.35


def build_evidence_atoms(events):
    atoms = []
    for event in events or []:
        key = atom_key(event)
        atom = next(
            (
                existing for existing in atoms
                if events_describe_same_fact(existing['events'][0], event)
            ),
            None,
        )
        if atom is None:
            atom = {
                'key': key,
                'region': event.get('region') or '',
                'event_type': _event_type(event),
                'topic': _event_topic(event),
                'source_families': set(),
                'sources': set(),
                'companies': set(),
                'dates': set(),
                'events': [],
            }
            atoms.append(atom)
        atom['source_families'].add(_source_family(event))
        atom['sources'].add(event.get('source') or event.get('display_source') or '')
        atom['companies'].update(_event_companies(event))
        if _event_date(event):
            atom['dates'].add(_event_date(event))
        atom['events'].append(event)

    for atom in atoms:
        atom['source_families'] = sorted(item for item in atom['source_families'] if item)
        atom['sources'] = sorted(item for item in atom['sources'] if item)
        atom['companies'] = sorted(item for item in atom['companies'] if item)
        atom['dates'] = sorted(item for item in atom['dates'] if item)
    return atoms


def evidence_independence(atoms):
    atoms = list(atoms or [])
    source_families = {source for atom in atoms for source in atom.get('source_families') or []}
    sources = {source for atom in atoms for source in atom.get('sources') or []}
    companies = {company for atom in atoms for company in atom.get('companies') or []}
    dates = {date for atom in atoms for date in atom.get('dates') or []}
    event_types = {atom.get('event_type') for atom in atoms if atom.get('event_type')}
    topics = {atom.get('topic') for atom in atoms if atom.get('topic')}
    total_events = sum(len(atom.get('events') or []) for atom in atoms)
    largest_source_count = 0
    source_counts = {}
    for atom in atoms:
        for event in atom.get('events') or []:
            source = event.get('source') or event.get('display_source') or ''
            if not source:
                continue
            source_counts[source] = source_counts.get(source, 0) + 1
            largest_source_count = max(largest_source_count, source_counts[source])
    source_concentration = largest_source_count / total_events if total_events else 0
    return {
        'atom_count': len(atoms),
        'source_family_count': len(source_families),
        'source_count': len(sources),
        'company_count': len(companies),
        'date_count': len(dates),
        'event_type_count': len(event_types),
        'topic_count': len(topics),
        'source_concentration': source_concentration,
    }


def can_promote_to_narrative(atoms):
    stats = evidence_independence(atoms)
    if stats['atom_count'] < 2:
        return False
    if (
        stats['source_concentration'] > 0.75
        and stats['source_count'] < 2
        and stats['company_count'] < 2
        and stats['date_count'] < 2
    ):
        return False
    return (
        stats['company_count'] >= 2
        or stats['source_family_count'] >= 2
        or stats['event_type_count'] >= 2
        or stats['topic_count'] >= 2
        or stats['date_count'] >= 2
    )
