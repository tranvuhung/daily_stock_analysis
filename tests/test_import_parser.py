# -*- coding: utf-8 -*-
"""Kiểm thử import_parser (mã Việt Nam).

Tests for import_parser.

Covers:
- CSV/Excel/text parsing
- Column mapping (code, name aliases)
- Encoding (utf-8, Windows-1258)
- Name-to-code resolution (mocked)
- No-header mode (col 0 = code, col 1 = name)
- File size and text size limits
"""

import io
import pytest
from unittest.mock import patch

from src.services.import_parser import (
    parse_import_from_bytes,
    parse_import_from_text,
    MAX_FILE_BYTES,
    MAX_TEXT_BYTES,
)


# ---------------------------------------------------------------------------
# parse_import_from_bytes - CSV
# ---------------------------------------------------------------------------

class TestParseImportFromBytesCsv:
    def test_parses_csv_with_header(self):
        data = "code,name\nHPG,Hòa Phát\nVCB,Vietcombank".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert len(result) == 2
        assert result[0] == ("HPG", "Hòa Phát", "medium")
        assert result[1] == ("VCB", "Vietcombank", "medium")

    def test_parses_csv_vietnamese_column_names(self):
        data = "mã cổ phiếu,tên cổ phiếu\nHPG,Hòa Phát".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert result[0] == ("HPG", "Hòa Phát", "medium")

    def test_parses_utf8_bom_csv_header(self):
        data = "\ufeffcode,name\nHPG,".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert result == [("HPG", None, "medium")]

    def test_parses_csv_no_header(self):
        data = "HPG,Hòa Phát\nFPT,FPT Corp".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert len(result) == 2
        assert result[0] == ("HPG", "Hòa Phát", "medium")
        assert result[1] == ("FPT", "FPT Corp", "medium")

    def test_skips_empty_rows(self):
        data = "code,name\nHPG,Hòa Phát\n\nVCB,Vietcombank".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert len(result) == 2

    def test_tab_separated(self):
        data = "code\tname\nHPG\tHòa Phát".encode("utf-8")
        result = parse_import_from_bytes(data, "paste.txt")
        assert result[0] == ("HPG", "Hòa Phát", "medium")

    @patch("src.services.import_parser.resolve_name_to_code")
    def test_resolves_name_when_code_empty(self, mock_resolve):
        mock_resolve.return_value = "HPG"
        # code column empty, name column has value
        data = "code,name\n,Hòa Phát".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert result[0] == ("HPG", "Hòa Phát", "medium")
        mock_resolve.assert_called_with("Hòa Phát")

    @patch("src.services.import_parser.resolve_name_to_code")
    def test_returns_none_code_when_resolution_fails(self, mock_resolve):
        mock_resolve.return_value = None
        data = "code,name\n,Cổ phiếu không tồn tại".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert result[0] == (None, "Cổ phiếu không tồn tại", "medium")


# ---------------------------------------------------------------------------
# parse_import_from_bytes - Excel
# ---------------------------------------------------------------------------

class TestParseImportFromBytesExcel:
    def test_parses_xlsx(self):
        try:
            import openpyxl
        except ImportError:
            pytest.skip("openpyxl not installed")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["code", "name"])
        ws.append(["HPG", "Hòa Phát"])
        ws.append(["FPT", "FPT Corp"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        data = buf.read()
        result = parse_import_from_bytes(data, "a.xlsx")
        assert len(result) == 2
        assert result[0] == ("HPG", "Hòa Phát", "medium")
        assert result[1] == ("FPT", "FPT Corp", "medium")

    def test_parses_xlsx_without_header(self):
        """Header-less Excel: first data row must NOT be consumed as column names."""
        try:
            import openpyxl
        except ImportError:
            pytest.skip("openpyxl not installed")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["HPG", "Hòa Phát"])
        ws.append(["VCB", "Vietcombank"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        data = buf.read()
        result = parse_import_from_bytes(data, "noheader.xlsx")
        assert len(result) == 2, f"Expected 2 rows, got {len(result)} — first row may have been eaten as header"
        codes = [r[0] for r in result]
        assert "HPG" in codes
        assert "VCB" in codes

    def test_rejects_xls(self):
        data = b"dummy"
        with pytest.raises(ValueError, match="Chỉ hỗ trợ định dạng .xlsx"):
            parse_import_from_bytes(data, "a.xls")

    def test_excel_error_includes_actionable_hint(self):
        """Excel parse failure should include hints for common causes."""
        # Invalid/corrupt xlsx (zip magic but bad content)
        data = b"PK\x03\x04" + b"x" * 100
        with pytest.raises(ValueError) as exc_info:
            parse_import_from_bytes(data, "a.xlsx")
        msg = str(exc_info.value)
        assert "Hãy kiểm tra" in msg or ".xlsx" in msg


# ---------------------------------------------------------------------------
# Limits and encoding
# ---------------------------------------------------------------------------

class TestParseImportLimits:
    def test_rejects_file_over_limit(self):
        data = b"x" * (MAX_FILE_BYTES + 1)
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            parse_import_from_bytes(data, "a.csv")

    def test_rejects_text_over_limit(self):
        text = "x" * (MAX_TEXT_BYTES + 1)
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            parse_import_from_text(text)

    def test_csv_parser_error_raises_helpful_message(self):
        """Malformed CSV (e.g. unclosed quote) should raise with actionable hint."""
        data = 'code,name\nHPG,"Hòa Phát'.encode("utf-8")
        with pytest.raises(ValueError) as exc_info:
            parse_import_from_bytes(data, "a.csv")
        msg = str(exc_info.value)
        assert "Phân tích CSV thất bại" in msg
        assert "dấu phân cách" in msg or "dấu ngoặc kép" in msg

    def test_accepts_windows_1258_encoded_csv(self):
        # CSV tiếng Việt xuất từ Excel cũ (Windows-1258). / Legacy Vietnamese Excel export.
        data = "code,name\nPOM,Thép Pomina".encode("cp1258")
        result = parse_import_from_bytes(data, "a.csv")
        assert len(result) == 1
        assert result[0][0] == "POM"
        assert result[0][1] == "Thép Pomina"


# ---------------------------------------------------------------------------
# parse_import_from_text
# ---------------------------------------------------------------------------

class TestParseImportFromText:
    def test_parses_pasted_text(self):
        text = "HPG,Hòa Phát\nFPT,FPT Corp"
        result = parse_import_from_text(text)
        assert len(result) == 2
        assert result[0] == ("HPG", "Hòa Phát", "medium")

    def test_parses_single_column_codes(self):
        text = "VCB\nHPG"
        result = parse_import_from_text(text)
        assert len(result) == 2
        assert result[0] == ("VCB", None, "medium")
        assert result[1] == ("HPG", None, "medium")

    def test_parses_single_column_with_header(self):
        text = "code\nVCB"
        result = parse_import_from_text(text)
        assert len(result) == 1
        assert result[0] == ("VCB", None, "medium")

    def test_parses_space_separated_code_name_lines(self):
        text = "HPG Hòa Phát\nVCB Vietcombank"
        result = parse_import_from_text(text)
        assert len(result) == 2
        assert result[0] == ("HPG", "Hòa Phát", "medium")
        assert result[1] == ("VCB", "Vietcombank", "medium")

    def test_preserves_name_when_code_is_dirty(self):
        data = "code,name\nINVALID,Hòa Phát".encode("utf-8")
        result = parse_import_from_bytes(data, "a.csv")
        assert len(result) == 1
        assert result[0] == ("HPG", "Hòa Phát", "medium")
