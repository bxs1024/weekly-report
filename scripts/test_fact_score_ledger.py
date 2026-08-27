"""Tests for fact-score ledger: fingerprint-based score anchoring + boundary capping."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402


class TestFactLedgerKey(unittest.TestCase):
    def test_key_built_from_fingerprint(self):
        ev = {'canonical_company': 'Fasset', 'canonical_key': '68m', 'event_types': ['funding']}
        key = fetch_news._fact_ledger_key(ev)
        self.assertTrue(key)
        self.assertIn('fasset', key.lower())
        self.assertIn('funding', key)

    def test_no_anchor_not_in_ledger(self):
        ev = {'canonical_company': 'X', 'canonical_key': '', 'event_types': ['strategy']}
        self.assertIsNone(fetch_news._fact_ledger_key(ev))
        ev2 = {'canonical_company': '', 'canonical_key': '5m', 'event_types': ['funding']}
        self.assertIsNone(fetch_news._fact_ledger_key(ev2))


class TestFactScoreRules(unittest.TestCase):
    def setUp(self):
        fetch_news._fact_ledger = {}

    def test_boundary_outside_caps_score(self):
        ev = {
            'title': 'Defense tech startup raises $500M for military drones',
            'reason': '国防和军工融资',
            'event_types': ['funding'],
            'score': 9,
            'canonical_company': 'ExampleDefense',
            'canonical_key': '500m',
        }
        fetch_news._apply_fact_score_rules(ev)
        self.assertEqual(ev['score'], 4)
        self.assertTrue(ev.get('boundary_capped'))

    def test_mainline_event_not_capped(self):
        ev = {
            'title': 'Nubank Q1 revenue up 34% to $2.8B',
            'reason': '拉美数字银行高增长',
            'event_types': ['earnings'],
            'score': 6,
            'canonical_company': 'Nubank',
            'canonical_key': '2.8b',
        }
        fetch_news._apply_fact_score_rules(ev)
        self.assertEqual(ev['score'], 6)
        self.assertFalse(ev.get('boundary_capped'))

    def test_same_fact_reuses_first_score(self):
        ev1 = {
            'title': 'Fasset hits $1 billion valuation after $68 million Series C',
            'reason': '中东数字资产获资本认可',
            'event_types': ['funding'],
            'score': 10,
            'canonical_company': 'Fasset',
            'canonical_key': '68m',
        }
        fetch_news._apply_fact_score_rules(ev1)
        self.assertFalse(ev1.get('score_reused'))
        self.assertEqual(len(fetch_news._fact_ledger), 1)

        ev2 = {
            'title': 'Stablecoin platform Fasset hits $1bn valuation',
            'reason': '稳定币平台融资',
            'event_types': ['funding'],
            'score': 6,
            'canonical_company': 'Fasset',
            'canonical_key': '68m',
        }
        fetch_news._apply_fact_score_rules(ev2)
        self.assertTrue(ev2.get('score_reused'))
        self.assertEqual(ev2['score'], 10)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            fetch_news._FACT_LEDGER_PATH = Path(td) / 'ledger.json'
            ev = {
                'title': 'SK hynix plans $28.6b buyback',
                'reason': '存储芯片回购',
                'event_types': ['strategy'],
                'score': 8,
                'canonical_company': 'SK hynix',
                'canonical_key': '28.6b',
            }
            fetch_news._apply_fact_score_rules(ev)
            fetch_news._save_fact_ledger()
            fetch_news._fact_ledger = {}
            fetch_news._load_fact_ledger()
            self.assertEqual(len(fetch_news._fact_ledger), 1)
        fetch_news._FACT_LEDGER_PATH = Path('data/fact_score_ledger.json')

    def test_missing_ledger_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as td:
            fetch_news._FACT_LEDGER_PATH = Path(td) / 'nope.json'
            fetch_news._load_fact_ledger()
            self.assertEqual(fetch_news._fact_ledger, {})
        fetch_news._FACT_LEDGER_PATH = Path('data/fact_score_ledger.json')


if __name__ == '__main__':
    unittest.main()