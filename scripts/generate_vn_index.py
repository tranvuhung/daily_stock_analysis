#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 VCI 全量代码表生成越南股票自动补全索引，并合并进现有索引文件。

Output: apps/dsa-web/public/stocks.index.json

设计要点：

- **合并而不是替换**。现有文件里还有 CN/US/HK/JP/KR 的三万多条记录，
  ``SUPPORTED_MARKETS=all`` 回滚时仍然要用。本脚本只重建 ``market == "VN"``
  的行，其余原样保留。
- 复用 ``VciFetcher.get_symbol_records()``，不再实现一遍抓取与缓存逻辑。
- 压缩行格式与 ``scripts/generate_index_from_csv.py`` 的 ``compress_index``
  保持一致：
  ``[canonicalCode, displayCode, nameZh, pinyinFull, pinyinAbbr,
     aliases, market, assetType, active, popularity]``
- 越南语没有拼音，但 ``pinyinFull`` / ``pinyinAbbr`` 两列正好可以放**去声调的
  搜索键**（"Hòa Phát" -> "hoaphat" / "hp"），让现有前端搜索逻辑不改代码就能
  按越南语名字检索。

用法：

    python scripts/generate_vn_index.py            # 生成并写入
    python scripts/generate_vn_index.py --test     # 只校验不写入
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

# VCI 代码表里 board 的取值；DELISTED / BOND 不纳入分析范围
_TRADABLE_BOARDS = {"HSX", "HNX", "UPCOM"}
# 权证（CW）、期货（FU）、债券（BOND）当前不参与分析
_TRADABLE_TYPES = {"STOCK", "ETF", "UNIT_TRUST"}

# 指数条目手工维护：VCI 代码表里不含指数，且 provider 侧大小写敏感
_VN_INDEX_ROWS = [
    ("VNINDEX", "Chỉ số VN-Index", ["VNINDEX", "VN-Index"]),
    ("VN30", "Chỉ số VN30", ["VN30"]),
    ("VN100", "Chỉ số VN100", ["VN100"]),
    ("HNXIndex", "Chỉ số HNX-Index", ["HNXINDEX", "HNX-Index"]),
    ("HNX30", "Chỉ số HNX30", ["HNX30"]),
    ("HNXUpcomIndex", "Chỉ số UPCoM-Index", ["UPCOM", "UPCOMINDEX"]),
]


def strip_diacritics(text: str) -> str:
    """去掉越南语声调符号，得到可用于检索的 ASCII 键。

    ``đ``/``Đ`` 不是组合字符，NFD 分解拿不到，需要单独替换。
    """

    normalized = unicodedata.normalize("NFD", str(text or ""))
    without_marks = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    return without_marks.replace("đ", "d").replace("Đ", "D")


def search_keys(name: str) -> tuple[str, str]:
    """返回 (全拼等价键, 首字母缩写键)。"""

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
                name,              # nameZh（此处存越南语名称）
                full or None,      # pinyinFull -> 去声调全名键
                abbr or None,      # pinyinAbbr -> 首字母键
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
    """原子写入，格式与 ``scripts/generate_index_from_csv.py`` 完全一致。

    每行一条记录（而不是整个数组压成一行）：该文件有三万多条记录，压成一行会
    让每次改动都产生一个无法 review 的全文件 diff。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    # 保留原文件权限：临时文件默认可能是 0600，直接 replace 会把本来 0644 的
    # 静态资源变成只有属主可读，容器里以非 root 用户跑时会 404。
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

    print("\n[3/4] 与现有索引合并（保留其他市场）...")
    existing: List[List[Any]] = []
    if OUTPUT_PATH.exists():
        existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    kept = [row for row in existing if len(row) > 6 and row[6] != MARKET]
    dropped = len(existing) - len(kept)
    print(f"      保留其他市场 {len(kept)} 条，替换旧越南记录 {dropped} 条")

    # 保留其他市场记录的原始顺序，越南记录追加在末尾。重排整个文件会让 diff
    # 覆盖全部三万多行，无法审阅，也和 generate_index_from_csv.py 的既有做法
    # （"Preserves all non-index tuples in their original order"）不一致。
    merged = kept + vn_rows

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
