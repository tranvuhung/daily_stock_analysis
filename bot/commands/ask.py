# -*- coding: utf-8 -*-
"""
Ask command - analyze one or more stocks using Agent skills.

Usage:
    /ask HPG                           -> Phân tích với kỹ năng mặc định / default skill
    /ask HPG chan_theory               -> Chỉ định kỹ năng / specify skill id directly
    /ask HPG phân tích theo sóng       -> Kỹ năng lấy từ câu / parse skill from message
    /ask HPG,FPT wave_theory           -> So sánh nhiều mã / multi-stock comparison
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.config import get_config
from src.services.stock_code_utils import normalize_code
from src.storage import get_db

logger = logging.getLogger(__name__)

# Quy đổi mức tin cậy dạng chữ sang số (Anh / Việt). Giữ token cũ (high/medium/low tiếng Trung) vì
# analyzer vẫn có thể trả giá trị mặc định cũ trong dữ liệu đã lưu.
# Map textual confidence levels to scores (English / Vietnamese, plus legacy tokens).
_CONFIDENCE_LEVEL_SCORES = {
    "high": 0.85, "cao": 0.85, "\u9ad8": 0.85,
    "medium": 0.65, "trung bình": 0.65, "\u4e2d": 0.65,
    "low": 0.45, "thấp": 0.45, "\u4f4e": 0.45,
}

# Nhãn đứng đầu giá trị điểm mua/bán cần cắt bỏ (Việt / Anh).
# Leading labels stripped from sniper-point values (Vietnamese / English).
_SNIPER_LABEL_PREFIXES = (
    "Điểm mua lý tưởng:",
    "Điểm mua thứ cấp:",
    "Cắt lỗ:",
    "Mục tiêu:",
    "Ideal buy:",
    "Secondary buy:",
    "Stop loss:",
    "Take profit:",
)


class AskCommand(BotCommand):
    """
    Ask command handler - invoke Agent with a specific skill to analyze stocks.
    """

    _MULTI_ANALYZE_TIMEOUT_S = 150.0

    @property
    def name(self) -> str:
        return "ask"

    @property
    def aliases(self) -> List[str]:
        return ["hoima", "hoicophieu"]

    @property
    def description(self) -> str:
        return "Phân tích cổ phiếu bằng kỹ năng Agent"

    @property
    def usage(self) -> str:
        return "/ask <mã[,mã2,...]> [tên kỹ năng]"

    def _merge_code_args(self, args: List[str]) -> tuple[str, List[str]]:
        """Merge stock code arguments separated by commas or explicit ``vs`` markers."""
        if not args:
            return "", []

        def _is_code_token(token: str) -> bool:
            # Token là mã Việt Nam hợp lệ (bỏ dấu phẩy hai đầu) / valid VN code token
            return normalize_code(token.strip(",，")) is not None

        raw_codes_parts = [args[0]]
        rest_args = list(args[1:])

        while rest_args:
            token = rest_args[0]
            prev = raw_codes_parts[-1].rstrip()

            if token.lower() == "vs" and len(rest_args) > 1 and _is_code_token(rest_args[1]):
                raw_codes_parts.append(rest_args[1])
                rest_args = rest_args[2:]
                continue

            has_comma_separator = (
                prev.endswith(",")
                or prev.endswith("，")
                or token.lstrip().startswith(",")
                or token.lstrip().startswith("，")
            )
            if _is_code_token(token) and has_comma_separator:
                raw_codes_parts.append(token)
                rest_args = rest_args[1:]
                continue

            break

        normalized_parts = [part.strip(",，") for part in raw_codes_parts]
        raw_code_str = ",".join(normalized_parts)
        return raw_code_str, rest_args

    def _parse_stock_codes(self, raw: str) -> List[str]:
        """Tách một hoặc nhiều mã từ tham số đầu; mã VN được chuẩn hoá (hpg/HPG.VN -> HPG).

        Parse one or more stock codes from the first argument; Vietnam codes are normalized.
        """
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        return [normalize_code(part) or part.upper() for part in parts]

    def _validate_single_code(self, code: str) -> Optional[str]:
        """Kiểm tra một mã Việt Nam / Validate a single Vietnam code."""
        if normalize_code(code) is None:
            return (
                f"Mã không hợp lệ: {code.upper()} "
                f"(cần mã cổ phiếu, chứng chỉ quỹ hoặc chỉ số Việt Nam, vd: HPG, FUEVN100, VNINDEX)"
            )
        return None

    def validate_args(self, args: List[str]) -> Optional[str]:
        """Validate arguments."""
        if not args:
            return "Vui lòng nhập mã cổ phiếu. Cách dùng: /ask <mã[,mã2,...]> [tên kỹ năng]"

        raw_code_str, _ = self._merge_code_args(args)
        codes = self._parse_stock_codes(raw_code_str)
        if not codes:
            return "Vui lòng nhập ít nhất một mã cổ phiếu hợp lệ"

        for code in codes:
            error = self._validate_single_code(code)
            if error:
                return error

        if len(codes) > 5:
            return "Mỗi lần chỉ phân tích tối đa 5 mã"

        return None

    @staticmethod
    def _load_skills() -> List[object]:
        try:
            from src.agent.factory import get_skill_manager

            sm = get_skill_manager()
            return list(sm.list_skills())
        except Exception as e:
            logger.warning("_load_skills failed: Failed to load skills: %s", e, exc_info=True)
            return []

    @classmethod
    def _get_default_skill_id(cls) -> str:
        try:
            from src.agent.skills.defaults import get_primary_default_skill_id

            return get_primary_default_skill_id(cls._load_skills())
        except Exception as e:
            logger.warning("_get_default_skill_id failed: Failed to resolve default skill id: %s", e, exc_info=True)
            return ""

    @classmethod
    def _build_skill_alias_pairs(cls) -> List[tuple[str, str]]:
        alias_pairs: List[tuple[str, str]] = []
        for skill in cls._load_skills():
            skill_id = str(getattr(skill, "name", "")).strip()
            if not skill_id:
                continue
            aliases = [skill_id, getattr(skill, "display_name", "")] + list(getattr(skill, "aliases", []) or [])
            for alias in aliases:
                alias_text = str(alias).strip()
                if alias_text:
                    alias_pairs.append((alias_text, skill_id))

        alias_pairs.sort(key=lambda item: (len(item[0]), item[0]), reverse=True)
        return alias_pairs

    def _parse_skill(self, args: List[str]) -> str:
        """Parse skill from arguments, returning the resolved skill id."""
        default_skill_id = self._get_default_skill_id()
        if len(args) < 2:
            return default_skill_id

        skill_text = " ".join(args[1:]).strip()
        available_ids = {str(getattr(skill, "name", "")).strip() for skill in self._load_skills()}
        if skill_text in available_ids:
            return skill_text

        for alias_text, skill_id in self._build_skill_alias_pairs():
            if alias_text in skill_text:
                return skill_id

        return default_skill_id

    def _resolve_skill_name(self, skill_id: Optional[str]) -> str:
        """Resolve a skill id to a human-readable display name."""
        if not skill_id:
            return "default"
        for skill in self._load_skills():
            if str(getattr(skill, "name", "")).strip() == skill_id:
                display_name = str(getattr(skill, "display_name", "")).strip()
                return display_name or skill_id
        return skill_id

    @staticmethod
    def _build_execution_context(stock_code: str, skill_id: str) -> Dict[str, Any]:
        selected = [skill_id] if skill_id else []
        return {
            "stock_code": stock_code,
            "skills": selected,
            "strategies": selected,
        }

    @staticmethod
    def _build_user_message(stock_code: str, skill_id: str, skill_text: str) -> str:
        user_msg = f"Hãy phân tích cổ phiếu {stock_code}"
        if skill_id:
            user_msg = f"Hãy dùng kỹ năng {skill_id} để phân tích cổ phiếu {stock_code}"
        if skill_text:
            user_msg = f"Hãy phân tích cổ phiếu {stock_code}, {skill_text}"
        return user_msg

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Execute the ask command via Agent pipeline. Supports multi-stock."""
        config = get_config()

        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Chế độ Agent chưa bật, không thể dùng tính năng hỏi cổ phiếu.\nVui lòng đặt `AGENT_MODE=true` trong cấu hình."
            )

        raw_code_str, remaining_args = self._merge_code_args(args)
        codes = self._parse_stock_codes(raw_code_str)
        skill_id = self._parse_skill(["placeholder"] + remaining_args) if remaining_args else self._get_default_skill_id()
        skill_text = " ".join(remaining_args).strip()

        logger.info("[AskCommand] Stocks: %s, Skill: %s, Extra: %s", codes, skill_id, skill_text)

        if len(codes) == 1:
            return self._analyze_single(config, message, codes[0], skill_id, skill_text)

        return self._analyze_multi(config, message, codes, skill_id, skill_text)

    def _analyze_single(
        self,
        config,
        message: BotMessage,
        code: str,
        skill_id: str,
        skill_text: str,
    ) -> BotResponse:
        """Analyze a single stock."""
        try:
            from src.agent.factory import build_agent_executor

            executor = build_agent_executor(config, skills=[skill_id] if skill_id else None)
            user_msg = self._build_user_message(code, skill_id, skill_text)
            session_id = f"{message.platform}_{message.user_id}:ask_{code}_{uuid.uuid4()}"
            result = executor.chat(
                message=user_msg,
                session_id=session_id,
                context=self._build_execution_context(code, skill_id),
            )

            if result.success:
                skill_name = self._resolve_skill_name(skill_id)
                header = f"📊 {code} | Kỹ năng: {skill_name}\n{'─' * 30}\n"
                return BotResponse.text_response(header + result.content)
            return BotResponse.text_response(f"⚠️ Phân tích thất bại: {result.error}")

        except Exception as exc:
            logger.error("Ask command failed: %s", exc)
            logger.exception("Ask error details:")
            return BotResponse.text_response(f"⚠️ Lỗi khi hỏi cổ phiếu: {str(exc)}")

    def _analyze_multi(
        self,
        config,
        message: BotMessage,
        codes: List[str],
        skill_id: str,
        skill_text: str,
    ) -> BotResponse:
        """Analyze multiple stocks in parallel and produce a comparison summary."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed

        skill_name = self._resolve_skill_name(skill_id)
        results: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        started_at = time.monotonic()
        overall_timeout_s = self._MULTI_ANALYZE_TIMEOUT_S

        platform = message.platform
        user_id = message.user_id

        def _run_one(stock_code: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
            try:
                from src.agent.conversation import conversation_manager
                from src.agent.factory import build_agent_executor

                executor = build_agent_executor(config, skills=[skill_id] if skill_id else None)
                user_msg = self._build_user_message(stock_code, skill_id, skill_text)
                session_id = f"{platform}_{user_id}:ask_{stock_code}_{uuid.uuid4()}"
                conversation_manager.add_message(session_id, "user", user_msg)

                result = executor.run(
                    task=user_msg,
                    context=self._build_execution_context(stock_code, skill_id),
                )
                if result.success or self._should_accept_fallback_content(result):
                    dashboard = result.dashboard if isinstance(result.dashboard, dict) else None
                    formatted_analysis = self._format_stock_result(stock_code, dashboard, result.content)
                    conversation_manager.add_message(session_id, "assistant", formatted_analysis)
                    return (
                        stock_code,
                        {
                            "content": result.content,
                            "dashboard": dashboard,
                            "signal": self._extract_signal(dashboard),
                            "confidence": self._extract_confidence(dashboard),
                            "summary": self._extract_summary(stock_code, dashboard, result.content),
                            "markdown": formatted_analysis,
                            "stock_name": self._extract_stock_name(stock_code, dashboard),
                            "risk_flags": self._extract_risk_flags(dashboard),
                        },
                        None,
                    )

                error_note = f"[Phân tích thất bại] {result.error or 'Lỗi không xác định'}"
                conversation_manager.add_message(session_id, "assistant", error_note)
                return (stock_code, None, result.error or "Lỗi không xác định")
            except Exception as exc:
                return (stock_code, None, str(exc))

        # Warm up DB connections before parallel history writes.
        get_db()
        pool = ThreadPoolExecutor(max_workers=min(len(codes), 5))
        future_map = {pool.submit(_run_one, code): code for code in codes}
        try:
            for future in as_completed(future_map, timeout=overall_timeout_s):
                try:
                    code, content, error = future.result(timeout=5)
                    if content is not None:
                        results[code] = content
                    else:
                        errors[code] = error or "Lỗi không xác định"
                except Exception as exc:
                    code = future_map[future]
                    errors[code] = f"Lỗi thực thi: {exc}"
        except FutureTimeoutError:
            logger.warning("[AskCommand] Multi-stock analysis hit overall timeout (%.1fs)", overall_timeout_s)
            for future, code in future_map.items():
                if code in results or code in errors:
                    continue
                if future.done():
                    try:
                        code_done, content, error = future.result(timeout=0)
                        if content is not None:
                            results[code_done] = content
                        else:
                            errors[code] = error or "Lỗi không xác định"
                    except Exception as exc:
                        errors[code] = f"Lỗi thực thi: {exc}"
                else:
                    errors[code] = "Phân tích quá thời gian (không xong trong 150 giây)"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        for code in codes:
            if code not in results and code not in errors:
                errors[code] = "Phân tích quá thời gian"

        parts = [f"📊 **So sánh nhiều mã** | Kỹ năng: {skill_name}", f"{'─' * 30}", ""]

        remaining_timeout_s = max(0.0, overall_timeout_s - (time.monotonic() - started_at))
        portfolio_section = self._build_portfolio_section(
            config,
            codes,
            results,
            timeout_s=remaining_timeout_s,
        )
        if portfolio_section:
            parts.append(portfolio_section)
            parts.append("")

        if len(results) >= 2:
            parts.append("| Mã | Tín hiệu | Độ tin cậy | Tóm tắt |")
            parts.append("|----|----------|------------|---------|")
            for code in codes:
                if code in results:
                    item = results[code]
                    signal = item.get("signal") or "unknown"
                    confidence = item.get("confidence")
                    confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "-"
                    summary_line = str(item.get("summary") or "Đã phân tích xong").replace("|", "/")[:80]
                    parts.append(f"| {code} | {signal} | {confidence_text} | {summary_line} |")
                elif code in errors:
                    parts.append(f"| {code} | error | - | ⚠️ {errors[code][:40]} |")
            parts.append("")

        for code in codes:
            if code in results:
                parts.append(f"### {code}")
                parts.append(results[code]["markdown"])
                parts.append("")
            elif code in errors:
                parts.append(f"### {code}")
                parts.append(f"⚠️ Phân tích thất bại: {errors[code]}")
                parts.append("")

        return BotResponse.markdown_response("\n".join(parts))

    @staticmethod
    def _should_accept_fallback_content(result: Any) -> bool:
        """Keep usable free-form answers when dashboard JSON parsing fails."""
        if getattr(result, "success", False):
            return True

        content = getattr(result, "content", "")
        error = str(getattr(result, "error", "") or "")
        if not isinstance(content, str) or not content.strip():
            return False

        return error == "Failed to parse dashboard JSON from agent response"

    @staticmethod
    def _extract_stock_name(stock_code: str, dashboard: Optional[Dict[str, Any]]) -> str:
        if isinstance(dashboard, dict):
            stock_name = dashboard.get("stock_name")
            if isinstance(stock_name, str) and stock_name.strip():
                return stock_name.strip()
        return stock_code

    @staticmethod
    def _extract_signal(dashboard: Optional[Dict[str, Any]]) -> str:
        if isinstance(dashboard, dict):
            signal = dashboard.get("decision_type")
            if isinstance(signal, str) and signal.strip():
                return signal.strip()
        return "unknown"

    @staticmethod
    def _extract_confidence(dashboard: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(dashboard, dict):
            return None

        score = dashboard.get("sentiment_score")
        try:
            return max(0.0, min(1.0, float(score) / 100.0))
        except (TypeError, ValueError):
            pass

        level = str(dashboard.get("confidence_level") or "").strip().lower()
        return _CONFIDENCE_LEVEL_SCORES.get(level)

    @staticmethod
    def _extract_summary(stock_code: str, dashboard: Optional[Dict[str, Any]], raw_content: str) -> str:
        if isinstance(dashboard, dict):
            for key in ("analysis_summary", "risk_warning", "trend_prediction"):
                value = dashboard.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            dashboard_block = dashboard.get("dashboard")
            if not isinstance(dashboard_block, dict):
                dashboard_block = {}
            core_conclusion = dashboard_block.get("core_conclusion")
            if not isinstance(core_conclusion, dict):
                core_conclusion = {}
            core = core_conclusion.get("one_sentence")
            if isinstance(core, str) and core.strip():
                return core.strip()

        for line in raw_content.splitlines():
            stripped = line.strip()
            if stripped and len(stripped) > 4 and not stripped.startswith(("{", "}", "\"")):
                return stripped[:120]
        return f"{stock_code}: đã phân tích xong"

    @staticmethod
    def _extract_risk_flags(dashboard: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not isinstance(dashboard, dict):
            return []

        flags: List[Dict[str, str]] = []
        dashboard_block = dashboard.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        intelligence = dashboard_block.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
        for alert in intelligence.get("risk_alerts", [])[:5]:
            if isinstance(alert, str) and alert.strip():
                flags.append({"category": "portfolio_input", "description": alert.strip(), "severity": "medium"})

        risk_warning = dashboard.get("risk_warning")
        if isinstance(risk_warning, str) and risk_warning.strip():
            flags.append({"category": "portfolio_input", "description": risk_warning.strip(), "severity": "medium"})
        return flags

    @staticmethod
    def _format_sniper_value(value: Any) -> Optional[str]:
        if value is None:
            return None

        text = str(value).strip()
        if not text or text in {"-", "—", "N/A", "None"}:
            return None

        for prefix in _SNIPER_LABEL_PREFIXES:
            if text.startswith(prefix):
                stripped = text[len(prefix):].strip()
                return stripped or None

        return text

    @staticmethod
    def _format_stock_result(stock_code: str, dashboard: Optional[Dict[str, Any]], raw_content: str) -> str:
        if not isinstance(dashboard, dict):
            content = raw_content
            if len(content) > 800:
                content = content[:800] + "\n... (đã rút gọn, vui lòng truy vấn riêng để xem phân tích đầy đủ)"
            return content

        lines = []
        stock_name = dashboard.get("stock_name")
        if isinstance(stock_name, str) and stock_name.strip() and stock_name.strip() != stock_code:
            lines.append(f"**Tên**: {stock_name.strip()}")

        decision = dashboard.get("decision_type")
        confidence = AskCommand._extract_confidence(dashboard)
        trend = dashboard.get("trend_prediction")
        if isinstance(decision, str):
            lines.append(
                f"**Kết luận**: {decision}"
                + (f" | **Độ tin cậy**: {confidence:.0%}" if isinstance(confidence, (int, float)) else "")
                + (f" | **Xu hướng**: {trend}" if isinstance(trend, str) and trend.strip() else "")
            )

        summary = AskCommand._extract_summary(stock_code, dashboard, raw_content)
        if summary:
            lines.append(f"**Tóm tắt**: {summary}")

        operation = dashboard.get("operation_advice")
        if isinstance(operation, str) and operation.strip():
            lines.append(f"**Khuyến nghị**: {operation.strip()}")

        risk_warning = dashboard.get("risk_warning")
        if isinstance(risk_warning, str) and risk_warning.strip():
            lines.append(f"**Cảnh báo rủi ro**: {risk_warning.strip()}")

        dashboard_block = dashboard.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        battle_plan = dashboard_block.get("battle_plan")
        if not isinstance(battle_plan, dict):
            battle_plan = {}
        sniper = battle_plan.get("sniper_points")
        if isinstance(sniper, dict):
            price_parts = []
            for key in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit"):
                value = AskCommand._format_sniper_value(sniper.get(key))
                if value:
                    price_parts.append(f"{key}={value}")
            if price_parts:
                lines.append("**Mức giá quan trọng**: " + " | ".join(price_parts))

        return "\n\n".join(lines) if lines else raw_content[:800]

    def _build_portfolio_section(
        self,
        config,
        codes: List[str],
        results: Dict[str, Dict[str, Any]],
        timeout_s: Optional[float] = None,
    ) -> str:
        """Generate a portfolio-level overlay for multi-stock ask results."""
        if len(results) < 2:
            return ""

        if timeout_s is not None and timeout_s <= 0:
            logger.info("[AskCommand] Skip portfolio overlay because no timeout budget remains")
            return ""

        def _render_overlay() -> str:
            from src.agent.agents.portfolio_agent import PortfolioAgent
            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter
            from src.agent.protocols import AgentContext

            stock_opinions: Dict[str, Dict[str, Any]] = {}
            risk_flags: List[Dict[str, str]] = []
            stock_list: List[str] = []
            for code in codes:
                item = results.get(code)
                if not item:
                    continue
                stock_list.append(code)
                stock_opinions[code] = {
                    "signal": item.get("signal", "unknown"),
                    "confidence": item.get("confidence", 0.5),
                    "summary": item.get("summary", ""),
                    "stock_name": item.get("stock_name", code),
                }
                risk_flags.extend(item.get("risk_flags", []))

            ctx = AgentContext(query=f"Portfolio overlay for {', '.join(stock_list)}")
            ctx.data["stock_opinions"] = stock_opinions
            ctx.data["stock_list"] = stock_list
            ctx.risk_flags.extend(risk_flags[:10])

            from src.services.portfolio_review_service import resolve_portfolio_agent_timeout

            agent = PortfolioAgent(
                tool_registry=get_tool_registry(),
                llm_adapter=LLMToolAdapter(config),
            )
            # Áp dụng AGENT_PORTFOLIO_AGENT_TIMEOUT_S, giới hạn bởi thời gian còn lại của /ask.
            # Apply the configured portfolio-agent timeout, capped by the remaining /ask budget.
            stage_result = agent.run(
                ctx,
                timeout_seconds=resolve_portfolio_agent_timeout(config, budget_s=timeout_s),
            )
            if not stage_result.success:
                return ""

            assessment = ctx.data.get("portfolio_assessment")
            if not isinstance(assessment, dict):
                return ""

            lines = ["## Góc nhìn danh mục", ""]
            summary = assessment.get("summary")
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip())
                lines.append("")

            risk_score = assessment.get("portfolio_risk_score")
            if risk_score is not None:
                lines.append(f"- Điểm rủi ro danh mục: {risk_score}")
            sector_warnings = assessment.get("sector_warnings") or []
            if sector_warnings:
                lines.append(f"- Tập trung ngành: {'; '.join(str(item) for item in sector_warnings[:3])}")
            correlation_warnings = assessment.get("correlation_warnings") or []
            if correlation_warnings:
                lines.append(f"- Rủi ro tương quan: {'; '.join(str(item) for item in correlation_warnings[:3])}")
            rebalance = assessment.get("rebalance_suggestions") or []
            if rebalance:
                lines.append(f"- Gợi ý cơ cấu: {'; '.join(str(item) for item in rebalance[:3])}")
            positions = assessment.get("positions") or []
            if positions:
                position_parts = []
                for position in positions[:5]:
                    if not isinstance(position, dict):
                        continue
                    code = position.get("code")
                    weight = position.get("suggested_weight")
                    signal = position.get("signal")
                    if code and weight is not None:
                        try:
                            weight_text = f"{float(weight):.0%}"
                        except (TypeError, ValueError):
                            weight_text = str(weight)
                        suffix = f" ({signal})" if signal else ""
                        position_parts.append(f"{code}: {weight_text}{suffix}")
                if position_parts:
                    lines.append(f"- Tỷ trọng đề xuất: {'; '.join(position_parts)}")

            return "\n".join(lines)

        if timeout_s is None:
            try:
                return _render_overlay()
            except Exception as exc:
                logger.warning("[AskCommand] Portfolio overlay failed: %s", exc)
                return ""

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_render_overlay)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            logger.warning("[AskCommand] Portfolio overlay timed out after %.2fs", timeout_s)
            return ""
        except Exception as exc:
            logger.warning("[AskCommand] Portfolio overlay failed: %s", exc)
            return ""
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
