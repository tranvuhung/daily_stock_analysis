# -*- coding: utf-8 -*-
"""Volume nến trong phiên phải quy đổi ra cả phiên trước khi so với ngày đủ phiên.

Intraday bars must project volume to a full session before comparing with full
days (regression for FPT #159: 3.25M shares at 10:10 read as "low volume 0.33").
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.core.trading_calendar import partial_bar_session_fraction, session_elapsed_fraction
from src.stock_analyzer import StockTrendAnalyzer, VolumeStatus

_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 29, hour, minute, tzinfo=_VN)


def test_session_fraction_skips_lunch_break() -> None:
    assert session_elapsed_fraction("vn", _at(8, 59)) is None
    assert session_elapsed_fraction("vn", _at(9, 0)) == 0.0
    assert abs(session_elapsed_fraction("vn", _at(10, 10)) - 70 / 255) < 1e-9
    assert abs(session_elapsed_fraction("vn", _at(12, 0)) - 150 / 255) < 1e-9
    assert session_elapsed_fraction("vn", _at(14, 45)) is None
    assert session_elapsed_fraction("unknown", _at(10, 0)) is None


def test_partial_bar_fraction_only_for_today() -> None:
    assert partial_bar_session_fraction("2026-10-29", "vn", _at(10, 10)) is not None
    assert partial_bar_session_fraction("2026-10-28", "vn", _at(10, 10)) is None
    assert partial_bar_session_fraction(None, "vn", _at(10, 10)) is None


def _bars(last_volume: float, last_close: float = 53300.0) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-10-29", periods=30)
    closes = [60000.0 - 200 * i for i in range(29)] + [last_close]
    volumes = [9_800_000.0] * 29 + [last_volume]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def test_intraday_volume_is_projected_before_classification() -> None:
    fraction = session_elapsed_fraction("vn", _at(10, 10))
    result = StockTrendAnalyzer().analyze(_bars(3_257_000.0), "FPT", session_fraction=fraction)

    assert result.volume_ratio_basis == "projected_intraday"
    # 3.257M / 0.2745 ≈ 11.9M so với 9.8M -> ~1.21, không còn "thanh khoản thấp".
    assert 1.1 < result.volume_ratio_5d < 1.3
    assert result.volume_status == VolumeStatus.NORMAL
    assert "shakeout" not in result.volume_trend


def test_too_early_in_session_is_not_classified() -> None:
    fraction = session_elapsed_fraction("vn", _at(9, 20))
    result = StockTrendAnalyzer().analyze(_bars(500_000.0), "FPT", session_fraction=fraction)

    assert result.volume_ratio_basis == "intraday_too_early"
    assert result.volume_status == VolumeStatus.NORMAL
    assert "shakeout" not in result.volume_trend


def test_full_day_bar_keeps_raw_ratio() -> None:
    result = StockTrendAnalyzer().analyze(_bars(3_257_000.0), "FPT")

    assert result.volume_ratio_basis == "full_day"
    assert result.volume_ratio_5d < 0.7
    assert result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN


def test_pipeline_fraction_uses_frozen_phase_time() -> None:
    from src.core.pipeline import StockAnalysisPipeline

    phase = {"market": "vn", "is_partial_bar": True, "market_local_time": "2026-10-29T10:10:40+07:00"}
    df = _bars(3_257_000.0)

    fraction = StockAnalysisPipeline._partial_bar_fraction(df, "vn", phase)
    assert fraction is not None and abs(fraction - 70.67 / 255) < 0.01

    closed = dict(phase, is_partial_bar=False)
    assert StockAnalysisPipeline._partial_bar_fraction(df, "vn", closed) is None
