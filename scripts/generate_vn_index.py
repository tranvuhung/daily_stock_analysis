#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate the Vietnam stock autocomplete index from the full VCI symbol list.

Output: apps/dsa-web/public/stocks.index.json

Design notes:

- **VN only**. Hệ thống chỉ còn hỗ trợ thị trường Việt Nam, nên file đầu ra chỉ chứa các dòng
  ``market == "VN"``; các dòng thị trường khác (CN/US/HK/JP/KR) còn sót trong file cũ sẽ bị loại bỏ.
  The system now supports Vietnam only, so legacy non-VN rows in the existing file are dropped.
- Reuses ``VciFetcher.get_symbol_records()`` instead of reimplementing fetching and caching.
- The compressed row format (consumed by the Web autocomplete) is:
  ``[canonicalCode, displayCode, nameZh, pinyinFull, pinyinAbbr,
     aliases, market, assetType, active, popularity]``
- Vietnamese has no pinyin, but the ``pinyinFull`` / ``pinyinAbbr`` columns can hold **diacritic-free
  search keys** ("Hòa Phát" -> "hoaphat" / "hp"), so the existing frontend search logic can
  search Vietnamese names without code changes.

Usage:

    python scripts/generate_vn_index.py            # generate and write
    python scripts/generate_vn_index.py --test     # validate only, do not write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

OUTPUT_PATH = REPO_ROOT / "apps" / "dsa-web" / "public" / "stocks.index.json"

MARKET = "VN"

# board values in the VCI symbol list; DELISTED / BOND are outside the analysis scope
_TRADABLE_BOARDS = {"HSX", "HNX", "UPCOM"}
# Covered warrants (CW), futures (FU) and bonds (BOND) are currently not analyzed
_TRADABLE_TYPES = {"STOCK", "ETF", "UNIT_TRUST"}

# Index entries are maintained by hand: the VCI symbol list has no indices, and the provider side is case-sensitive
_VN_INDEX_ROWS = [
    ("VNINDEX", "Chỉ số VN-Index", ["VNINDEX", "VN-Index"]),
    ("VN30", "Chỉ số VN30", ["VN30"]),
    ("VN100", "Chỉ số VN100", ["VN100"]),
    ("HNXIndex", "Chỉ số HNX-Index", ["HNXINDEX", "HNX-Index"]),
    ("HNX30", "Chỉ số HNX30", ["HNX30"]),
    ("HNXUpcomIndex", "Chỉ số UPCoM-Index", ["UPCOM", "UPCOMINDEX"]),
]


def strip_diacritics(text: str) -> str:
    """Strip Vietnamese diacritics to get an ASCII key usable for search.

    ``đ``/``Đ`` are not combining characters and are not split by NFD, so they need separate replacement.
    """

    normalized = unicodedata.normalize("NFD", str(text or ""))
    without_marks = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    return without_marks.replace("đ", "d").replace("Đ", "D")


def search_keys(name: str) -> tuple[str, str]:
    """Return (full-name key, initials key)."""

    ascii_name = strip_diacritics(name).lower()
    words = [word for word in "".join(
        ch if ch.isalnum() else " " for ch in ascii_name
    ).split() if word]
    full = "".join(words)
    abbr = "".join(word[0] for word in words)
    return full, abbr


def build_rows(records: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    seen: set[str] = set()

    for record in records:
        symbol = str(record.get("symbol") or "").strip().upper()
        board = str(record.get("board") or "").strip().upper()
        asset = str(record.get("type") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        if board not in _TRADABLE_BOARDS or asset not in _TRADABLE_TYPES:
            continue

        name = (
            record.get("organShortName")
            or record.get("organName")
            or record.get("enOrganShortName")
            or record.get("enOrganName")
            or symbol
        )
        name = str(name).strip()
        full, abbr = search_keys(name)

        aliases: List[str] = []
        en_name = str(record.get("enOrganShortName") or "").strip()
        if en_name and en_name != name:
            aliases.append(en_name)

        seen.add(symbol)
        rows.append(
            [
                symbol,            # canonicalCode
                symbol,            # displayCode
                name,              # nameZh (stores the Vietnamese name here)
                full or None,      # pinyinFull -> diacritic-free full-name key
                abbr or None,      # pinyinAbbr -> initials key
                aliases,
                MARKET,
                "etf" if asset in {"ETF", "UNIT_TRUST"} else "stock",
                True,
                0,
            ]
        )

    for symbol, name, aliases in _VN_INDEX_ROWS:
        if symbol in seen:
            continue
        full, abbr = search_keys(name)
        seen.add(symbol)
        rows.append(
            [symbol, symbol, name, full or None, abbr or None, list(aliases),
             MARKET, "index", True, 0]
        )

    rows.sort(key=lambda row: str(row[0]))
    return rows


def _atomic_write_json(path: Path, rows: List[List[Any]]) -> None:
    """Atomic write of the compressed index.

    One record per line (rather than the whole array on one line), so every change stays a reviewable diff.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the original file permissions: a temp file may default to 0600, and a plain replace would turn a 0644
    # static asset into owner-only readable, which 404s when the container runs as a non-root user.
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write("[\n")
            for index, row in enumerate(rows):
                json.dump(row, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write(",\n" if index < len(rows) - 1 else "\n")
            handle.write("]\n")
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="生成越南股票自动补全索引")
    parser.add_argument("--test", "-t", action="store_true", help="只校验不写入")
    args = parser.parse_args()

    from data_provider.vci_fetcher import VciFetcher

    print("=" * 60)
    print("越南股票索引生成工具（数据源：VCI 全量代码表）")
    print("=" * 60)

    print("\n[1/4] 拉取 VCI 代码表...")
    records = VciFetcher().get_symbol_records()
    if not records:
        print("[Error] 代码表为空或不可用，已中止（不会写入文件）")
        return 1
    print(f"      共 {len(records)} 条原始记录")

    print("\n[2/4] 过滤并构建越南索引行...")
    vn_rows = build_rows(records)
    stock_count = sum(1 for row in vn_rows if row[7] == "stock")
    etf_count = sum(1 for row in vn_rows if row[7] == "etf")
    index_count = sum(1 for row in vn_rows if row[7] == "index")
    print(f"      股票 {stock_count} / ETF {etf_count} / 指数 {index_count}")
    if stock_count < 500:
        print(f"[Error] 股票条数 {stock_count} 异常偏少，疑似上游返回不完整，已中止")
        return 1

    print("\n[3/4] So sánh với index hiện có (chỉ giữ thị trường VN)...")
    existing: List[List[Any]] = []
    if OUTPUT_PATH.exists():
        existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    # Loại bỏ các dòng thị trường khác còn sót từ phiên bản đa thị trường cũ
    # Drop legacy non-VN rows left over from the former multi-market index
    legacy_non_vn = sum(1 for row in existing if len(row) > 6 and row[6] != MARKET)
    print(f"      Loại bỏ {legacy_non_vn} dòng ngoài VN, thay {len(existing) - legacy_non_vn} dòng VN cũ")
    merged = vn_rows

    if args.test:
        print("\n[4/4] 测试模式：跳过写入")
        print(f"      合并后共 {len(merged)} 条")
        return 0

    print(f"\n[4/4] 写入 {OUTPUT_PATH}")
    _atomic_write_json(OUTPUT_PATH, merged)
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"      合并后共 {len(merged)} 条，文件大小 {size_kb:.2f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
