"""P2/P3 接线测试：安全阀关掉时行为与接线前完全一致；打开时字段正确落库。

这层测试的意义是把「接线」这件事本身锁住——模块建好了但没人调用，是本次
对账发现的最大缺口，光有 ai_scoring / event_grouping 自己的单测拦不住。
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402

ENV_KEYS = ('AI_SCORE_ENABLED', 'GROUP_ENABLED', 'AI_CALLS_ENABLED')


def _sample():
    """两条同公司同类型的报道，规则层可判为同一事件。"""
    return {'2026-10-06': [
        {'event_id': 'e1', 'title': 'Acme Launches Pay Widget', 'source': 'TechCrunch',
         'date': '2026-10-06', 'score': 6, 'canonical_company': 'Acme',
         'canonical_key': 'pay widget', 'event_types': ['strategy']},
        {'event_id': 'e2', 'title': 'Acme Pay Widget Arrives On Partner Platform',
         'source': 'OtherMedia', 'date': '2026-10-06', 'score': 5,
         'canonical_company': 'Acme', 'canonical_key': 'pay widget',
         'event_types': ['strategy']},
    ]}


class _EnvIsolated(unittest.TestCase):
    """安全阀都是读环境变量的，测试必须自己隔离，否则随外部环境飘。"""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestValvesOff(_EnvIsolated):
    def test_both_off_writes_nothing(self):
        """两个分闸都关 → all_events 一个字段都不多，metrics 里也不留痕。"""
        os.environ['AI_SCORE_ENABLED'] = '0'
        os.environ['GROUP_ENABLED'] = '0'
        events = _sample()
        before = [dict(e) for e in events['2026-10-06']]
        metrics = {}
        fetch_news._run_ai_scoring(events, metrics)
        fetch_news._run_event_grouping(events, metrics)
        self.assertEqual(events['2026-10-06'], before)
        self.assertEqual(metrics, {})


class TestGroupingPass(_EnvIsolated):
    def test_assigns_group_and_heat_without_ai(self):
        """总闸关（不发 AI）时规则层仍要给出 group_id 与热度——归组不依赖模型。"""
        os.environ['AI_CALLS_ENABLED'] = '0'
        events = _sample()
        metrics = {}
        fetch_news._run_event_grouping(events, metrics)
        for event in events['2026-10-06']:
            self.assertIn('group_id', event)
            self.assertIn('group_role', event)
            self.assertIn('group_method', event)
            self.assertIn('event_heat', event)
        self.assertIn('event_grouping', metrics)
        self.assertEqual(metrics['event_grouping']['ai_calls'], 0, '总闸关着不该发 AI')

    def test_only_touches_window(self):
        """窗口外的事件不能被碰——否则每天都要重扫全部历史。"""
        os.environ['AI_CALLS_ENABLED'] = '0'
        days = [f'2026-{m:02d}-{d:02d}' for m in (1, 2) for d in range(1, 29)]
        events = {day: [] for day in days}
        events['2026-01-01'] = [{'event_id': 'ancient', 'title': 'Ancient news',
                                 'source': 'X', 'date': '2026-01-01'}]
        events['2026-02-28'] = _sample()['2026-10-06']
        self.assertGreater(len(days), fetch_news.GROUP_WINDOW_DAYS)
        fetch_news._run_event_grouping(events, {})
        self.assertNotIn('group_id', events['2026-01-01'][0], '窗口外事件不该被归组')
        self.assertIn('group_id', events['2026-02-28'][0], '窗口内事件应被归组')


class TestScoringPass(_EnvIsolated):
    def test_off_writes_nothing(self):
        os.environ['AI_SCORE_ENABLED'] = '0'
        events = _sample()
        before = [dict(e) for e in events['2026-10-06']]
        fetch_news._run_ai_scoring(events, {})
        self.assertEqual(events['2026-10-06'], before)

    def test_skips_already_scored(self):
        """已有 ai_score_avg 的不再重评——幂等，重跑不重复付费。"""
        events = _sample()
        for event in events['2026-10-06']:
            event['ai_score_avg'] = 70
        before = [dict(e) for e in events['2026-10-06']]
        os.environ['AI_CALLS_ENABLED'] = '0'
        fetch_news._run_ai_scoring(events, {})
        self.assertEqual(events['2026-10-06'], before)

    def test_no_channel_writes_no_partial_fields(self):
        """AI 通道不可用时不许写半截字段，更不许把「没评上」写成 0 分。"""
        os.environ['AI_CALLS_ENABLED'] = '0'
        events = _sample()
        metrics = {}
        fetch_news._run_ai_scoring(events, metrics)
        for event in events['2026-10-06']:
            self.assertNotIn('ai_score_avg', event)
            self.assertNotIn('ai_score_1', event)
            self.assertNotIn('ai_score_2', event)
        self.assertEqual(metrics['ai_scoring']['scored'], 0)


class TestHeatAnchor(unittest.TestCase):
    """热度的时间锚点必须来自数据，不能来自机器时钟。"""

    def test_anchor_is_newest_event_date(self):
        anchor = fetch_news._data_now([{'date': '2026-09-01'}, {'date': '2026-09-02'}])
        self.assertEqual(anchor.strftime('%Y-%m-%d'), '2026-09-02')

    def test_anchor_falls_back_to_published_at(self):
        anchor = fetch_news._data_now([{'published_at': '2026-08-30T10:00:00Z'}])
        self.assertEqual(anchor.strftime('%Y-%m-%d'), '2026-08-30')

    def test_anchor_none_without_dates(self):
        self.assertIsNone(fetch_news._data_now([{'title': 'no date'}]))

    def test_anchor_ignores_machine_clock(self):
        """数据停更后锚点仍停在数据日——否则来源全部超出 48h 窗口，热度集体归零。"""
        anchor = fetch_news._data_now([{'date': '2020-01-01'}])
        self.assertEqual(anchor.year, 2020)


class TestWiringIsPresent(unittest.TestCase):
    def test_main_calls_both_passes(self):
        """接线不能被无声删掉：main() 里必须真的调用这两层。"""
        src = inspect.getsource(fetch_news.main)
        self.assertIn('_run_ai_scoring(all_events', src)
        self.assertIn('_run_event_grouping(all_events', src)

    def test_grouping_anchors_heat_to_data(self):
        """热度必须传锚点；退回 datetime.now() 会让热度随机器时钟漂、且不可复现。"""
        src = inspect.getsource(fetch_news._run_event_grouping)
        self.assertIn('now=_data_now(', src)


if __name__ == '__main__':
    unittest.main()
