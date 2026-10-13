# -*- coding: utf-8 -*-
"""
Ngữ cảnh thị trường cho prompt LLM (chỉ thị trường Việt Nam).

Trả về mô tả vai trò và hướng dẫn phân tích theo bối cảnh thị trường Việt Nam để prompt
không phải viết cứng. Thân prompt luôn bằng tiếng Anh; ngôn ngữ đầu ra do phần
"Output language requirements" của prompt quyết định.

Market context for LLM prompts (Vietnam only).
"""

from typing import Optional

_VN_MARKET = "vn"

_VN_ROLE = "Vietnam stock"

_VN_GUIDELINES = (
    "- This analysis covers a **Vietnamese stock** (listed on HOSE / HNX / UPCoM, priced in VND).\n"
    "- Use Vietnam-market context: daily price limits (HOSE ±7%, HNX ±10%, UPCoM ±15%), T+2.5 settlement, "
    "foreign ownership limits and foreign net buying/selling, and State Bank of Vietnam (SBV) rate and FX policy."
)


def detect_market(stock_code: Optional[str]) -> str:
    """Thị trường của mã cổ phiếu; hệ thống chỉ hỗ trợ Việt Nam nên luôn trả ``vn``.

    Always returns ``"vn"``: the system only supports the Vietnam market.
    """
    return _VN_MARKET


def get_market_role(stock_code: Optional[str], lang: str = "vi") -> str:
    """Mô tả vai trò thị trường cho prompt LLM. / Market role description for the prompt."""
    return _VN_ROLE


def get_market_guidelines(stock_code: Optional[str], lang: str = "vi") -> str:
    """Hướng dẫn phân tích theo thị trường Việt Nam cho prompt LLM.

    Vietnam-market analysis guidelines for the prompt.
    """
    return _VN_GUIDELINES
