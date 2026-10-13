# -*- coding: utf-8 -*-
"""Khung chiến lược cho báo cáo điểm lại thị trường Việt Nam. / Vietnam market recap strategy blueprint."""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StrategyDimension:
    """Single strategy dimension used by market recap prompts."""

    name: str
    objective: str
    checkpoints: List[str]


@dataclass(frozen=True)
class MarketStrategyBlueprint:
    """Region specific market strategy blueprint."""

    region: str
    title: str
    positioning: str
    principles: List[str]
    dimensions: List[StrategyDimension]
    action_framework: List[str]

    def to_prompt_block(self) -> str:
        """Render blueprint as prompt instructions."""
        principles_text = "\n".join([f"- {item}" for item in self.principles])
        action_text = "\n".join([f"- {item}" for item in self.action_framework])

        dims = []
        for dim in self.dimensions:
            checkpoints = "\n".join([f"  - {cp}" for cp in dim.checkpoints])
            dims.append(f"- {dim.name}: {dim.objective}\n{checkpoints}")
        dimensions_text = "\n".join(dims)

        return (
            f"## Strategy Blueprint: {self.title}\n"
            f"{self.positioning}\n\n"
            f"### Strategy Principles\n{principles_text}\n\n"
            f"### Analysis Dimensions\n{dimensions_text}\n\n"
            f"### Action Framework\n{action_text}"
        )

    def to_markdown_block(self) -> str:
        """Render blueprint as markdown section for template fallback report."""
        dims = "\n".join([f"- **{dim.name}**: {dim.objective}" for dim in self.dimensions])
        section_title = "### VI. Khung chiến lược"
        return f"{section_title}\n{dims}\n"


VN_BLUEPRINT = MarketStrategyBlueprint(
    region="vn",
    title="Chiến lược điểm lại thị trường Việt Nam ba bước",
    positioning="Tập trung vào xu hướng VN-Index/VN30, thanh khoản, độ rộng thị trường và dòng tiền để lập kế hoạch cho phiên kế tiếp.",
    principles=[
        "Xem hướng đi của VN-Index và VN30 trước, sau đó đối chiếu HNX-Index, UPCoM-Index để đánh giá mức độ đồng thuận.",
        "Thanh khoản và độ rộng (số mã tăng/giảm, trần/sàn) quyết định chất lượng của nhịp tăng hoặc giảm.",
        "Chỉ dựa trên dữ liệu chỉ số, độ rộng, tin tức có sẵn; không tự bịa số liệu ngành hay dòng tiền khối ngoại.",
    ],
    dimensions=[
        StrategyDimension(
            name="Cấu trúc xu hướng",
            objective="Xác định thị trường đang ở pha tăng, tích lũy hay phòng thủ.",
            checkpoints=[
                "VN-Index và VN30 có cùng hướng không",
                "Chỉ số có vượt hoặc thủng vùng hỗ trợ/kháng cự quan trọng",
                "Nhóm vốn hóa lớn có dẫn dắt hay kìm hãm chỉ số",
            ],
        ),
        StrategyDimension(
            name="Thanh khoản và độ rộng",
            objective="Đánh giá dòng tiền có xác nhận diễn biến giá hay không.",
            checkpoints=[
                "Giá trị giao dịch so với các phiên gần đây",
                "Tương quan số mã tăng/giảm và số mã trần/sàn",
                "Tin tức về khối ngoại, tự doanh, chính sách tiền tệ",
            ],
        ),
        StrategyDimension(
            name="Chủ đề dẫn dắt",
            objective="Nhận diện nhóm cổ phiếu có tính bền vững và nhóm cần tránh.",
            checkpoints=[
                "Ngân hàng/chứng khoán/bất động sản có luân chuyển dòng tiền",
                "Nhóm được hỗ trợ bởi tin vĩ mô, nâng hạng thị trường, đầu tư công",
                "Tin tức có hỗ trợ diễn biến giá hay không",
            ],
        ),
    ],
    action_framework=[
        "Tấn công: VN-Index/VN30 cùng tăng + thanh khoản cải thiện + độ rộng tích cực.",
        "Cân bằng: chỉ số phân hóa hoặc thanh khoản thấp, kiểm soát tỷ trọng và chờ xác nhận.",
        "Phòng thủ: chỉ số suy yếu + nhiều mã giảm sàn hoặc thanh khoản tăng khi giảm, ưu tiên quản trị rủi ro.",
    ],
)


def get_market_strategy_blueprint(region: str) -> MarketStrategyBlueprint:
    """Trả về khung chiến lược (chỉ Việt Nam). / Return the strategy blueprint (Vietnam only)."""
    return VN_BLUEPRINT
