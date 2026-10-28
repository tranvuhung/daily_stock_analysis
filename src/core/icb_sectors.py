# -*- coding: utf-8 -*-
"""
Phân ngành ICB cấp 2 (supersector) cho cổ phiếu Việt Nam.

Nguồn dữ liệu (FiinTrade) trả mã ICB cấp 4 (vd. VCB -> ``8355``); hai chữ số đầu xác định
ngành cấp 2. Dùng cấp 2 để cảnh báo tập trung: cấp 1 gộp Ngân hàng, Chứng khoán và Bất động
sản vào "Tài chính", còn cấp 4 thì quá vụn.

ICB level-2 (supersector) names for Vietnamese stocks, derived from level-4 ICB codes.
"""

from typing import Dict, Optional

ICB_SUPERSECTORS: Dict[str, str] = {
    "0500": "Dầu khí",
    "1300": "Hóa chất",
    "1700": "Tài nguyên cơ bản",
    "2300": "Xây dựng và Vật liệu",
    "2700": "Hàng & Dịch vụ công nghiệp",
    "3300": "Ô tô và phụ tùng",
    "3500": "Thực phẩm và đồ uống",
    "3700": "Hàng cá nhân & Gia dụng",
    "4500": "Y tế",
    "5300": "Bán lẻ",
    "5500": "Truyền thông",
    "5700": "Du lịch và Giải trí",
    "6500": "Viễn thông",
    "7500": "Điện, nước & xăng dầu khí đốt",
    "8300": "Ngân hàng",
    "8500": "Bảo hiểm",
    "8600": "Bất động sản",
    "8700": "Dịch vụ tài chính",
    # ICB 89xx (Investment Instruments): ETF, chứng chỉ quỹ, REIT; tách riêng vì là rổ đa ngành.
    # ICB 89xx investment instruments (ETFs, fund certificates), kept apart as diversified baskets.
    "8900": "Chứng chỉ quỹ",
    "9500": "Công nghệ Thông tin",
}


def icb_supersector_name(icb_code: Optional[str]) -> Optional[str]:
    """Tên ngành ICB cấp 2 từ mã ICB bất kỳ cấp; không nhận ra thì ``None``.

    Name of the level-2 ICB supersector for an ICB code; ``None`` when unknown.
    """

    code = str(icb_code or "").strip()
    if len(code) != 4 or not code.isdigit():
        return None
    return ICB_SUPERSECTORS.get(code[:2] + "00")
