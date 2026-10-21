# -*- coding: utf-8 -*-
"""Prompt: dữ liệu phiên Việt Nam và sự kiện doanh nghiệp (nguồn FiinTrade).

Các dòng mới chỉ xuất hiện khi có dữ liệu, để đường SSI (không có các trường này)
không bị thêm N/A vào prompt.
New rows appear only when the data exists, so the SSI path gains no N/A noise.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from src.analyzer import GeminiAnalyzer

_FAKE_CFG = SimpleNamespace(news_max_age_days=30, news_strategy_profile="medium")


def _analyzer() -> GeminiAnalyzer:
    with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
        return GeminiAnalyzer()


def _prompt(context) -> str:
    analyzer = _analyzer()
    with patch("src.analyzer.get_config", return_value=_FAKE_CFG):
        return analyzer._format_prompt(context, "FPT", news_context="news")


class VnSessionPromptTestCase(unittest.TestCase):
    def test_session_rows_render_when_fiintrade_fields_present(self) -> None:
        context = {
            "code": "FPT",
            "stock_name": "FPT",
            "date": "2026-10-21",
            "today": {},
            "realtime": {
                "price": 57400.0,
                "pe_ratio": 10.78,
                "ref_price": 57400.0,
                "ceiling": 61400.0,
                "floor": 53400.0,
                "eps_ttm": 5323.8,
                "roe": 26.47,
                "beta": 0.67,
                "foreign_buy_value": 78.9e9,
                "foreign_sell_value": 252.06e9,
                "foreign_net_value": -173.16e9,
                "foreign_ownership_pct": 27.92,
                "foreign_room_remaining": 359826705.0,
                "put_through_volume": 1366085.0,
                "put_through_value": 83.04e9,
                "bid_ask_imbalance": 0.6,
                "avg_volume_1m": 6066277,
            },
        }
        prompt = _prompt(context)

        self.assertIn("### Vietnam Session Data", prompt)
        self.assertIn("| Ceiling / Floor | 61400.0 / 53400.0", prompt)
        self.assertIn("6.97% to ceiling", prompt)
        self.assertIn("| EPS (TTM) | 5324", prompt)
        self.assertIn("Foreign net (session) | -173.16", prompt)
        self.assertIn("Put-through (negotiated)", prompt)
        self.assertIn("| Top-3 bid share | 60.0%", prompt)

    def test_session_section_absent_for_ssi_only_quote(self) -> None:
        context = {
            "code": "FPT",
            "stock_name": "FPT",
            "date": "2026-10-21",
            "today": {},
            "realtime": {"price": 57400.0},
        }
        prompt = _prompt(context)

        self.assertIn("### Realtime Quote Extras", prompt)
        self.assertNotIn("### Vietnam Session Data", prompt)
        self.assertNotIn("### Corporate Events", prompt)

    def test_corporate_events_section(self) -> None:
        context = {
            "code": "FPT",
            "stock_name": "FPT",
            "date": "2026-10-21",
            "today": {},
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "dividend": {"ttm_cash_dividend_per_share": 2000.0, "ttm_event_count": 2},
                        "share_issuances": [
                            {"method_en": "Bonus Issue", "ex_date": "2026-09-18", "volume": 171401226.0, "ratio": 0.1}
                        ],
                        "earnings_releases": [{"year": 2026, "quarter": 2, "public_date": "2026-08-22"}],
                    }
                },
                "institution": {
                    "data": {
                        "insider_window_days": 90,
                        "insider_transaction_count": 1,
                        "insider_net_shares": -2000.0,
                        "insider_transactions": [
                            {"public_date": "2026-10-10", "name": "Nguyễn Văn A", "action": "sell",
                             "shares_executed": 2000.0, "shares_registered": 5000.0}
                        ],
                    }
                },
            },
        }
        prompt = _prompt(context)

        self.assertIn("### Corporate Events", prompt)
        self.assertIn("1 disclosure(s); net executed shares -2,000", prompt)
        self.assertIn("Nguyễn Văn A sell executed 2,000 / registered 5,000", prompt)
        self.assertIn("Share issuance: Bonus Issue (ex-date 2026-09-18, 171,401,226 shares, ratio 10.00%)", prompt)
        self.assertIn("Latest earnings release: Q2/2026 published 2026-08-22", prompt)


if __name__ == "__main__":
    unittest.main()
