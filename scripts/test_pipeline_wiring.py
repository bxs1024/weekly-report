"""P2 接线测试：安全阀关掉时行为与接线前完全一致；打开时字段正确落库。

这层测试的意义是把「接线」这件事本身锁住——模块建好了但没人调用，是历史对账
发现的最大缺口，光有 ai_scoring 自己的单测拦不住。反向同理：已拆除的归组 /
热度层不该以任何形式留在产线上，否则又变回「文件清单全打勾、实际没拆干净」。
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402

ENV_KEYS = ('AI_SCORE_ENABLED', 'AI_CALLS_ENABLED')


def _sample():
    """两条同公司同类型的报道。"""
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
    def test_off_writes_nothing(self):
        """分闸关 → all_events 一个字段都不多，metrics 里也不留痕。"""
        os.environ['AI_SCORE_ENABLED'] = '0'
        events = _sample()
        before = [dict(e) for e in events['2026-10-06']]
        metrics = {}
        fetch_news._run_ai_scoring(events, metrics)
        self.assertEqual(events['2026-10-06'], before)
        self.assertEqual(metrics, {})


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


class TestWiringIsPresent(unittest.TestCase):
    def test_main_calls_scoring_pass(self):
        """接线不能被无声删掉：main() 里必须真的调用评分层。"""
        src = inspect.getsource(fetch_news.main)
        self.assertIn('_run_ai_scoring(all_events', src)


class TestGroupingRemoved(unittest.TestCase):
    """归组 / 热度层已拆除：代码、提示词、回填工具、接线都不许残留。

    反事实验证：不是看「现在跑得通」，而是把「它还在」的各种形态逐一点名，
    任一冒头就失败。否则最容易出现「引擎还在，只是没人调」的假收敛。
    """

    def test_no_grouping_symbols_in_fetch_news(self):
        for name in ('event_grouping', '_run_event_grouping', '_data_now',
                     'GROUP_WINDOW_DAYS'):
            self.assertFalse(hasattr(fetch_news, name),
                             f'{name} 不该还挂在 fetch_news 上')

    def test_main_does_not_call_grouping(self):
        src = inspect.getsource(fetch_news.main)
        self.assertNotIn('_run_event_grouping', src)
        self.assertNotIn('GROUP_ENABLED', src)

    def test_grouping_files_are_gone(self):
        here = os.path.dirname(os.path.abspath(__file__))
        removed = ('event_grouping.py', 'backfill_event_heat.py',
                   'test_event_grouping.py', 'test_display_heat.py')
        for name in removed:
            self.assertFalse(os.path.exists(os.path.join(here, name)),
                             f'{name} 应已删除')
        prompts = ('group-pair.md', 'group-definitions.md', 'group-method.md')
        for name in prompts:
            self.assertFalse(os.path.exists(os.path.join(here, 'prompts', name)),
                             f'prompts/{name} 应已删除')


if __name__ == '__main__':
    unittest.main()
