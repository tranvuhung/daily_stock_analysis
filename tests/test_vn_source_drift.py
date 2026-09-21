# -*- coding: utf-8 -*-
"""越南数据源的上游漂移探测（``@pytest.mark.network``）。

离线测试把解析逻辑钉在冻结的样本上，所以它们**发现不了上游改字段、改单位
或迁移 URL**。本文件直接打真实接口，用于捕获这类漂移。

失败语义（与 ``tests/test_tw_institutional_network.py`` 保持一致）：
- **漂移 → 大声失败**：结构变了、字段没了、单位变了
- **传输抖动 / 非交易日 → 安静跳过**：网络不可达、超时、当天无数据

本文件不参与 blocking gate（``pytest -m "not network"`` 会排除），由
``.github/workflows/network-smoke.yml`` 定时运行。
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import requests

from data_provider import ssi_fetcher, vci_fetcher
from data_provider.base import DataFetchError
from data_provider.ssi_fetcher import SsiFetcher
from data_provider.vci_fetcher import VciFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher
from src.core import markets

pytestmark = pytest.mark.network


@contextmanager
def _vn_only_markets():
    """越南股票的裸码识别要求 ``us`` 市场处于关闭状态（见 ``_is_vn_market``）。"""

    with patch.dict(os.environ, {"SUPPORTED_MARKETS": "vn"}):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


# 流动性高、长期存在的标的，避免因为个股停牌造成误报
_LIQUID_SYMBOL = "HPG"
_TRANSPORT_ERRORS = (
    requests.RequestException,
    ConnectionError,
    TimeoutError,
)


def _skip_on_transport_error(exc: Exception) -> None:
    """传输层问题安静跳过；其他异常视为漂移，向上抛出。"""

    cause = exc
    while cause is not None:
        if isinstance(cause, _TRANSPORT_ERRORS):
            pytest.skip(f"上游暂时不可达，跳过漂移检测: {cause}")
        cause = cause.__cause__ or cause.__context__
    raise exc


class VciDriftTestCase(unittest.TestCase):
    def setUp(self):
        vci_fetcher.reset_symbols_cache()
        self.fetcher = VciFetcher()

    def test_daily_response_shape_and_units(self):
        try:
            df = self.fetcher.get_daily_data(_LIQUID_SYMBOL, days=30)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return

        if df.empty:
            pytest.skip("VCI 未返回数据（可能是长假期间）")

        from data_provider.base import STANDARD_COLUMNS

        for column in STANDARD_COLUMNS:
            self.assertIn(column, df.columns, f"VCI 日线缺少列 {column}")

        last_close = float(df["close"].iloc[-1])
        # 越南股票以"盾"计价，主板个股价格量级在 1e3~1e6。如果这里掉到
        # 个位数，说明上游把单位换成了"千盾"，必须同步修改归一化。
        self.assertGreater(
            last_close, 1_000, f"VCI 收盘价 {last_close} 疑似单位漂移（千盾？）"
        )
        self.assertLess(last_close, 10_000_000, f"VCI 收盘价 {last_close} 超出合理范围")

        last_amount = float(df["amount"].iloc[-1])
        implied = last_close * float(df["volume"].iloc[-1])
        if implied > 0:
            # accumulatedValue 的单位是百万盾；单位漂移会让两者差 6 个数量级
            self.assertLess(
                abs(last_amount - implied) / implied,
                0.5,
                f"VCI 成交额 {last_amount} 与 量×价 {implied} 不匹配，疑似单位漂移",
            )

    def test_index_symbols_still_require_exact_casing(self):
        # 已知契约：HNXINDEX 返回空，HNXIndex 才有数据。若上游改成大小写不敏感，
        # 这个断言会失败并提醒我们可以简化映射表。
        try:
            df = self.fetcher.get_daily_data("HNXIndex", days=10)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return
        if df.empty:
            pytest.skip("HNXIndex 未返回数据")
        self.assertGreater(float(df["close"].iloc[-1]), 0)

    def test_symbol_table_still_exposes_board_and_vietnamese_name(self):
        records = self.fetcher.get_symbol_records()
        if not records:
            pytest.skip("代码表暂时不可用")

        self.assertGreater(len(records), 1_000, "代码表条数异常偏少")
        sample = next(
            (r for r in records if str(r.get("symbol")).upper() == _LIQUID_SYMBOL), None
        )
        self.assertIsNotNone(sample, f"代码表里找不到 {_LIQUID_SYMBOL}")
        for field in ("symbol", "board", "organName"):
            self.assertIn(field, sample, f"代码表缺少字段 {field}")


class SsiDriftTestCase(unittest.TestCase):
    def setUp(self):
        ssi_fetcher.reset_snapshot_cache()
        self.fetcher = SsiFetcher()

    def test_snapshot_covers_all_three_exchanges(self):
        try:
            snapshot = self.fetcher._load_snapshot(force=True)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return

        self.assertGreater(len(snapshot), 1_000, "全市场快照标的数异常偏少")
        self.assertIn(_LIQUID_SYMBOL, snapshot)

        exchanges = {str(r.get("exchange", "")).lower() for r in snapshot.values()}
        for exchange in ("hose", "hnx", "upcom"):
            self.assertIn(exchange, exchanges, f"快照缺少 {exchange}")

    def test_realtime_quote_fields_still_present(self):
        try:
            quote = self.fetcher.get_realtime_quote(_LIQUID_SYMBOL)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return

        if quote is None:
            pytest.skip("SSI 未返回该标的行情")

        for field in ("price", "volume", "amount", "pre_close"):
            self.assertIsNotNone(getattr(quote, field), f"实时行情缺少 {field}")
        self.assertGreater(quote.price, 1_000, "SSI 快照价格疑似单位漂移")

    def test_chart_prices_stay_in_thousand_vnd_upstream(self):
        """图表接口的原始单位必须仍然是"千盾"。

        如果上游改成"盾"，我们乘 1000 的换算就会让价格膨胀 1000 倍，而且不会
        抛任何异常 —— 只能靠这个断言发现。
        """

        try:
            df = self.fetcher.get_daily_data(_LIQUID_SYMBOL, days=20)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return

        if df.empty:
            pytest.skip("SSI 图表接口未返回数据")

        last_close = float(df["close"].iloc[-1])
        self.assertGreater(
            last_close, 1_000, f"SSI 换算后收盘价 {last_close} 偏低，上游可能改了单位"
        )
        self.assertLess(
            last_close,
            10_000_000,
            f"SSI 换算后收盘价 {last_close} 偏高，上游可能已改用'盾'，需移除 ×1000",
        )


class CrossSourceConsistencyTestCase(unittest.TestCase):
    """VCI 与 SSI 必须给出同一口径的价格。"""

    def test_vci_and_ssi_agree_on_latest_close(self):
        vci_fetcher.reset_symbols_cache()
        ssi_fetcher.reset_snapshot_cache()
        try:
            vci_df = VciFetcher().get_daily_data(_LIQUID_SYMBOL, days=20)
            ssi_df = SsiFetcher().get_daily_data(_LIQUID_SYMBOL, days=20)
        except DataFetchError as exc:
            _skip_on_transport_error(exc)
            return

        if vci_df.empty or ssi_df.empty:
            pytest.skip("两个数据源之一暂无数据")

        merged = vci_df[["date", "close"]].merge(
            ssi_df[["date", "close"]], on="date", suffixes=("_vci", "_ssi")
        )
        if merged.empty:
            pytest.skip("两个数据源没有重叠的交易日")

        row = merged.iloc[-1]
        vci_close = float(row["close_vci"])
        ssi_close = float(row["close_ssi"])
        self.assertLess(
            abs(vci_close - ssi_close) / max(vci_close, 1.0),
            0.005,
            f"{row['date']} 收盘价不一致: VCI={vci_close} SSI={ssi_close}，"
            "两者单位或复权口径可能已经漂移",
        )


if __name__ == "__main__":
    unittest.main()


class YfinanceVnDriftTestCase(unittest.TestCase):
    """Yahoo 作为越南市场的第三兜底源（仅 HOSE 日线）。

    与 VCI/SSI 那对 0.5% 的阈值不同，这里用 6%，原因见下面的注释：Yahoo 与 VCI
    的复权基准本来就不一致，这是**已知且预期内**的偏差，不是漂移。
    """

    def setUp(self):
        self.fetcher = YfinanceFetcher()

    def test_daily_response_shape_and_units(self):
        with _vn_only_markets():
            try:
                df = self.fetcher.get_daily_data(_LIQUID_SYMBOL, days=30)
            except DataFetchError as exc:
                _skip_on_transport_error(exc)
                return

        if df.empty:
            pytest.skip("Yahoo 未返回越南股票数据")

        for column in ("date", "open", "high", "low", "close", "volume"):
            self.assertIn(column, df.columns, column)

        last_close = float(df["close"].iloc[-1])
        # 单位漂移探测：越南盾全额计价，HPG 在数千到数万之间。若 Yahoo 改成
        # “千盾”，这里会立刻掉到 1000 以下。
        self.assertGreater(
            last_close, 1_000, f"Yahoo 收盘价 {last_close} 偏低，上游可能改了单位"
        )
        self.assertLess(
            last_close, 10_000_000, f"Yahoo 收盘价 {last_close} 偏高，单位可疑"
        )

    def test_hnx_symbol_still_has_no_coverage(self):
        """HNX 无覆盖是当前设计的前提，一旦 Yahoo 补齐就应该重新评估定位。

        2026-09-21 实测：HNX 与 UPCOM 各 20 个随机样本全部为空。这条用例失败
        不代表代码坏了，而是提示上游能力变了、值得把 Yahoo 的定位往上调。
        """

        with _vn_only_markets():
            try:
                df = self.fetcher.get_daily_data("SHS", days=20)
            except DataFetchError:
                return
            except Exception as exc:  # noqa: BLE001 - 传输问题安静跳过
                _skip_on_transport_error(exc)
                return

        self.assertTrue(
            df is None or df.empty,
            "Yahoo 现在能返回 HNX 数据了，请重新评估 Yahoo 在越南市场的定位",
        )

    def test_yahoo_and_vci_agree_within_adjustment_tolerance(self):
        vci_fetcher.reset_symbols_cache()
        with _vn_only_markets():
            try:
                vci_df = VciFetcher().get_daily_data(_LIQUID_SYMBOL, days=20)
                yahoo_df = self.fetcher.get_daily_data(_LIQUID_SYMBOL, days=20)
            except DataFetchError as exc:
                _skip_on_transport_error(exc)
                return

        if vci_df.empty or yahoo_df.empty:
            pytest.skip("两个数据源之一暂无数据")

        merged = vci_df[["date", "close"]].merge(
            yahoo_df[["date", "close"]], on="date", suffixes=("_vci", "_yahoo")
        )
        if merged.empty:
            pytest.skip("两个数据源没有重叠的交易日")

        row = merged.iloc[-1]
        vci_close = float(row["close_vci"])
        yahoo_close = float(row["close_yahoo"])
        # 阈值 6%，而不是 VCI/SSI 那对的 0.5%。Yahoo 与 VCI 对除权除息的处理
        # 基准不同：MBB 实测显示除权日之前的历史整体相差固定系数 1.043938，
        # 而近期交易日完全一致。所以这条用例只用来抓**单位级**错误（比如上游
        # 改用千盾，会差 1000 倍），抓不了也不该抓复权基准差异 —— 后者正是
        # Yahoo 的日线不写入 stock_daily 的原因。
        self.assertLess(
            abs(vci_close - yahoo_close) / max(vci_close, 1.0),
            0.06,
            f"{row['date']} 收盘价差异过大: VCI={vci_close} Yahoo={yahoo_close}，"
            "已超出复权基准差异的合理范围，疑似单位漂移",
        )
