# -*- coding: utf-8 -*-
"""Báo cáo sau hạ bậc rủi ro không được tự mâu thuẫn (hồi quy analysis_history #159).

After a risk downgrade the report must not contradict itself (regression for #159:
"[Hạ bậc: Nắm giữ -> Bán]" while the body still said "tiếp tục giữ vị thế quan sát").
"""

from unittest.mock import MagicMock

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.agent.orchestrator import AgentOrchestrator  # noqa: E402
from src.agent.protocols import AgentContext, AgentOpinion  # noqa: E402
from src.agent.risk_override import (  # noqa: E402
    build_risk_override_application,
    build_risk_override_plan,
)

_LLM_HOLD_SUMMARY = (
    "FPT đang điều chỉnh, RSI quá bán. "
    "Khuyến nghị nhà đầu tư tiếp tục giữ vị thế quan sát, chờ đợi điểm cân bằng."
)


def _downgraded_ctx() -> AgentContext:
    ctx = AgentContext(query="FPT", stock_code="FPT", stock_name="FPT Corporation")
    ctx.meta["report_language"] = "vi"
    ctx.add_opinion(
        AgentOpinion(agent_name="risk", signal="sell", raw_data={"signal_adjustment": "downgrade_one"})
    )
    ctx.meta["risk_override_application"] = build_risk_override_application(
        build_risk_override_plan(ctx, current_signal="hold")
    )
    return ctx


def _hold_payload() -> dict:
    return {
        "decision_type": "sell",
        "operation_advice": "Quan sát",
        "analysis_summary": _LLM_HOLD_SUMMARY,
        "buy_reason": "Chỉ cân nhắc giải ngân thăm dò quanh 51.500 VND.",
        "dashboard": {
            "core_conclusion": {"one_sentence": "FPT giảm, nên kiên nhẫn quan sát vùng hỗ trợ 51.500 VND."},
            "phase_decision": {
                "immediate_action": "Chưa hành động trong phiên, chờ xác nhận vùng cân bằng",
                "next_check_time": "11:30",
            },
            "battle_plan": {"sniper_points": {"ideal_buy": "51.500 VND", "stop_loss": "51.000 VND"}},
        },
    }


def test_hold_to_sell_downgrade_rewrites_action_fields():
    orch = AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())
    ctx = _downgraded_ctx()

    result = orch._finalize_dashboard_payload(_hold_payload(), ctx)

    prefix = "[Hạ bậc do quản trị rủi ro: Nắm giữ -> Bán]"
    summary = result["analysis_summary"]
    assert summary.startswith(prefix)
    assert summary.count(prefix) == 1
    # Thân bài LLM vẫn giữ để có ngữ cảnh, nhưng kết luận cuối đứng sau cùng.
    assert "RSI quá bán" in summary
    assert summary.endswith(
        "Kết luận cuối sau quản trị rủi ro: Giảm tỷ trọng/Bán. "
        "Không mở vị thế mới; ưu tiên giảm tỷ trọng các vị thế đang nắm giữ."
    )

    dashboard = result["dashboard"]
    one_sentence = dashboard["core_conclusion"]["one_sentence"]
    assert one_sentence.startswith(prefix)
    assert "quan sát" not in one_sentence

    phase_decision = dashboard["phase_decision"]
    assert phase_decision["immediate_action"].startswith("Không mở vị thế mới")
    assert phase_decision["next_check_time"] == "11:30"

    assert "Không có điểm mua mới" in result["buy_reason"]
    assert "51.500" not in result["buy_reason"]
    assert dashboard["core_conclusion"]["signal_type"] == "🔴 Tín hiệu bán"


def test_summary_rebuild_is_idempotent():
    orch = AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())
    ctx = _downgraded_ctx()

    first = orch._finalize_dashboard_payload(_hold_payload(), ctx)
    payload = _hold_payload()
    payload["analysis_summary"] = first["analysis_summary"]
    second = orch._finalize_dashboard_payload(payload, ctx)

    assert second["analysis_summary"] == first["analysis_summary"]


def test_declared_confidence_level_is_kept_for_low_score_sell():
    # FPT #161: điểm 35 làm confidence suy ra "Thấp" dù LLM khai báo "Trung bình".
    # A low sentiment score must not override the declared confidence level.
    from src.agent.orchestrator import _resolve_confidence_label

    assert _resolve_confidence_label("Trung bình", 0.35, "vi") == "Trung bình"
    assert _resolve_confidence_label("Medium", 0.35, "vi") == "Trung bình"
    assert _resolve_confidence_label("rất chắc", 0.35, "vi") == "Thấp"
    assert _resolve_confidence_label(None, 0.8, "vi") == "Cao"
