# -*- coding: utf-8 -*-
"""YfinanceFetcher: cổ phiếu Việt Nam (dự phòng cấp 3, chỉ nến ngày sàn HOSE).

Mock chỉ đặt ở biên thư viện (``yfinance.download``); chuẩn hoá, làm sạch và tính
chỉ báo vẫn chạy đường code thật, tránh để bài kiểm thử đi vòng qua đúng tầng có
rủi ro.

Bối cảnh thực đo (2026-09-21), quyết định phạm vi của nhánh VN trong fetcher này:

- HOSE phủ 20/20 mã mẫu ngẫu nhiên, HNX 0/20, UPCOM 0/20.
- Không có lịch sử chỉ số: ``^VNINDEX.VN`` xin cả năm chỉ trả đúng 1 nến, còn
  ``^VN30.VN`` / ``^HNXINDEX.VN`` / ``^UPCOM.VN`` rỗng hoàn toàn.
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd

from data_provider.base import (
    DataFetchError,
    STANDARD_COLUMNS,
    is_daily_source_persistable,
    should_persist_daily_data,
)
from data_provider.yfinance_fetcher import YfinanceFetcher
from src.core import markets


@contextmanager
def supported_markets(value):
    with patch.dict(os.environ, {"SUPPORTED_MARKETS": value}):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


# Lấy từ phản hồi thật của ``yf.download("HPG.VN", auto_adjust=True)``.
# Cột là MultiIndex (Price, Ticker), chỉ số là ``Date`` không mang timezone.
# Giá là VND đầy đủ (21200), không phải nghìn đồng như endpoint chart của SSI.
def _real_frame(ticker="HPG.VN"):
    index = pd.DatetimeIndex(
        ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"], name="Date"
    )
    data = {
        ("Close", ticker): [21200.0, 20950.0, 21200.0, 21550.0],
        ("High", ticker): [21200.0, 21250.0, 21400.0, 21550.0],
        ("Low", ticker): [21000.0, 20950.0, 20750.0, 21300.0],
        ("Open", ticker): [21000.0, 21200.0, 20800.0, 21450.0],
        ("Volume", ticker): [11937200, 12277500, 17574100, 31124900],
    }
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["Price", "Ticker"])
    return frame


class VnSymbolConversionTestCase(unittest.TestCase):
    """Nhánh VN phải đứng trước nhánh mã Mỹ trong ``_convert_stock_code``."""

    def setUp(self):
        self.fetcher = YfinanceFetcher()

    def test_vn_codes_get_vn_suffix(self):
        with supported_markets("vn"):
            for raw in ("HPG", "HPG.VN", "vnHPG", "hpg.vn"):
                self.assertEqual(self.fetcher._convert_stock_code(raw), "HPG.VN", raw)

    def test_explicit_vn_forms_win_even_when_us_enabled(self):
        # Dạng tường minh không phụ thuộc trạng thái cờ thị trường.
        with supported_markets("vn,us"):
            self.assertEqual(self.fetcher._convert_stock_code("HPG.VN"), "HPG.VN")
            self.assertEqual(self.fetcher._convert_stock_code("vnHPG"), "HPG.VN")

    def test_non_vn_codes_are_refused(self):
        # Yahoo chỉ còn là nguồn dự phòng cho VN. / Yahoo only serves VN daily fallback.
        from data_provider.base import DataFetchError

        for raw in ("AAPL", "600519", "hk00700"):
            with self.subTest(raw=raw), self.assertRaises(DataFetchError):
                self.fetcher._convert_stock_code(raw)

    def test_vn_index_codes_are_refused_without_any_request(self):
        """Chỉ số VN phải hỏng sạch, không được phát request.

        ``^VNINDEX.VN`` trả 1 nến rác chứ không rỗng, nên nếu để lọt sẽ thành dữ
        liệu sai thay vì một lỗi mà manager có thể xử lý.
        """

        with supported_markets("vn"):
            with patch(
                "yfinance.download", side_effect=AssertionError("không được phát request")
            ):
                for code in ("VNINDEX", "VN30", "VN100", "HNXIndex", "HNX30", "UPCOM"):
                    with self.assertRaises(DataFetchError, msg=code):
                        self.fetcher._convert_stock_code(code)


class VnDailyDataTestCase(unittest.TestCase):
    def setUp(self):
        self.fetcher = YfinanceFetcher()

    def test_daily_frame_is_normalized_to_standard_columns(self):
        with supported_markets("vn"):
            with patch("yfinance.download", return_value=_real_frame()) as download:
                df = self.fetcher.get_daily_data("HPG", days=10)

        self.assertEqual(download.call_args.kwargs["tickers"], "HPG.VN")
        for column in STANDARD_COLUMNS:
            self.assertIn(column, df.columns, column)
        self.assertEqual(len(df), 4)
        self.assertEqual(list(df["close"]), [21200.0, 20950.0, 21200.0, 21550.0])
        # Chỉ báo do BaseFetcher tính, chứng tỏ không đi vòng qua đường code thật.
        for column in ("ma5", "ma10", "ma20", "volume_ratio"):
            self.assertIn(column, df.columns, column)

    def test_price_unit_is_full_vnd(self):
        # Bẫy đơn vị: SSI trả nghìn đồng, VCI và Yahoo trả VND đầy đủ. Nhầm chỗ này
        # là hỏng toàn bộ MA và các chỉ báo mà không có lỗi nào phát ra.
        with supported_markets("vn"):
            with patch("yfinance.download", return_value=_real_frame()):
                df = self.fetcher.get_daily_data("HPG", days=10)
        self.assertGreater(df["close"].min(), 1_000)

    def test_empty_frame_raises_so_manager_can_fall_back(self):
        # Mã HNX/UPCOM rơi vào đây: Yahoo không có dữ liệu -> phải hỏng sạch để
        # manager xử lý như một nguồn hỏng bình thường.
        with supported_markets("vn"):
            with patch("yfinance.download", return_value=pd.DataFrame()):
                with self.assertRaises(DataFetchError):
                    self.fetcher.get_daily_data("SHS", days=10)


class VnPersistenceGateTestCase(unittest.TestCase):
    """Nến Yahoo cho VN không được ghi vào ``stock_daily``.

    ``save_daily_data`` UPSERT theo ``(code, date)``. Đo thực tế trên mã MBB cho
    thấy phần lịch sử trước ngày chốt quyền lệch đúng một hệ số cố định 1,043938
    so với VCI, trong khi các phiên gần đây khớp tuyệt đối — trộn hai nguồn sẽ tạo
    một bậc thang giả ~4,4% ngay tại ngày đó.
    """

    def test_yfinance_vn_is_not_persistable(self):
        self.assertFalse(is_daily_source_persistable("YfinanceFetcher", "vn"))

    def test_vn_primary_sources_are_persistable(self):
        for source in ("VciFetcher", "SsiFetcher", "VnstockFetcher"):
            self.assertTrue(is_daily_source_persistable(source, "vn"), source)

    def test_unknown_source_defaults_to_persistable(self):
        self.assertTrue(is_daily_source_persistable("SomeNewFetcher", "vn"))

    def test_gate_derives_market_from_stock_code(self):
        with supported_markets("vn"):
            self.assertFalse(should_persist_daily_data("YfinanceFetcher", "HPG"))
            self.assertFalse(should_persist_daily_data("YfinanceFetcher", "HPG.VN"))
            self.assertTrue(should_persist_daily_data("VciFetcher", "HPG"))


if __name__ == "__main__":
    unittest.main()
