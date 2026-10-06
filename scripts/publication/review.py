"""复盘事件：主事件质量筛选与复盘视图构建。"""

import re

try:
    from view_selectors import select_main_list_events, select_review_events, signal_sort_key
    from publication.display_dedupe import dedupe_display_events
except ImportError:
    from scripts.view_selectors import select_main_list_events, select_review_events, signal_sort_key
    from scripts.publication.display_dedupe import dedupe_display_events

def _quality_main_events(main_events):
    """Build the quality-filtered main batch used as a fallback display list."""
    seen_titles = set()
    deduped = []
    for e in main_events:
        norm = re.sub(r'[^\w]', '', e.get('title', '').lower())
        if norm in seen_titles or len(norm) <= 10:
            continue
        seen_titles.add(norm)

        if not select_main_list_events([e]):
            continue

        deduped.append(e)

    deduped.sort(key=signal_sort_key, reverse=True)
    return deduped

def build_review_events(today_events, limit=12):
    """Build a deduped review list from the same display batch as high-value events."""
    review_events = select_review_events(today_events, limit=None)
    review_events = dedupe_display_events(review_events)
    review_events.sort(key=signal_sort_key, reverse=True)
    return review_events[:limit]
