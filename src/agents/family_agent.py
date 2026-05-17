import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.policies.safety_policy import SafetyPolicy
from src.schemas.family import FamilyChatRequest
from src.services.family_context_service import FamilyContextService


def create_event(event_type: str, data: Any) -> str:
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False)


class FamilyAgent:
    """Deterministic family-side SSE agent.

    This first production slice intentionally avoids live LLM dependency so the
    family channel stays deterministic, safe, and testable. It answers from
    family-visible context only and never writes to the elder chat history.
    """

    def __init__(
        self,
        context_service: FamilyContextService,
        *,
        safety_policy: Optional[SafetyPolicy] = None,
        chunk_size: int = 28,
    ):
        self.context_service = context_service
        self.safety_policy = safety_policy or SafetyPolicy()
        self.chunk_size = chunk_size

    async def process_chat_stream(self, request: FamilyChatRequest) -> AsyncGenerator[str, None]:
        message = str(request.message or "").strip()
        if not message:
            yield create_event("error", "Message cannot be empty")
            yield create_event("done", "stop")
            return

        context = self.context_service.build_family_chat_context(
            request.elder_user_id,
            request.child_user_id,
        )
        response_text = self._build_response(message, context)
        risk_tier = str(context.get("summary", {}).get("risk_tier") or "safe")
        response_text = self.safety_policy.sanitize_response(response_text)

        self.context_service.add_family_turn(
            request.elder_user_id,
            request.child_user_id,
            message,
            response_text,
            metadata={
                "risk_tier": risk_tier,
                "care_plan_stage": context.get("summary", {}).get("care_plan_stage"),
            },
        )

        for chunk in self._chunk_text(response_text):
            yield create_event("token", chunk)

        yield create_event("family_context", self._public_context_payload(context))
        yield create_event("done", "stop")

    def build_elder_summary(self, elder_user_id: str, child_user_id: str) -> Dict[str, Any]:
        return self.context_service.build_elder_summary(elder_user_id, child_user_id)

    def _build_response(self, message: str, context: Dict[str, Any]) -> str:
        summary = context.get("summary") or {}
        evidence = context.get("visible_evidence") or []
        policy = context.get("family_policy") or {}
        recent_alerts = context.get("recent_family_alerts") or []

        risk_tier = str(summary.get("risk_tier") or "safe")
        primary_state = str(summary.get("primary_state") or "stable_or_general")
        care_plan_stage = str(summary.get("care_plan_stage") or "companionship")
        care_plan_goal = str(summary.get("care_plan_goal") or "")
        suggested_action = str(summary.get("suggested_family_action") or "")
        profile_name = str(summary.get("profile_name") or "老人")
        preferred_tone = str(policy.get("preferred_tone") or "")

        opening = self._opening_by_risk(risk_tier, profile_name, primary_state)
        lines = [
            opening,
            f"当前照护阶段是 {care_plan_stage}；下一步目标是：{care_plan_goal or '保持稳定陪伴'}。",
        ]
        if suggested_action:
            lines.append(f"建议你现在这样做：{suggested_action}")
        if preferred_tone:
            lines.append(f"你之前设置的沟通偏好是：{preferred_tone}。我会按这个方向给建议，但不会让它覆盖安全边界。")

        if evidence:
            latest = evidence[-1]
            raw_quotes = latest.get("raw_quotes") or []
            lines.append(
                f"最近一条 family-visible 证据摘要：{latest.get('summary') or '暂无摘要'}。"
            )
            if raw_quotes:
                lines.append("如需核对原话，已在 family_context.visible_evidence 中提供；对老人沟通时建议不要直接复述刺激性原句。")
        else:
            lines.append("当前没有明显的中高风险证据记录，建议保持轻量问候。")

        if recent_alerts:
            lines.append(f"系统当前有 {len(recent_alerts)} 条家庭侧提醒，可在 family_context.recent_family_alerts 中查看。")

        if self._asks_for_diagnosis_or_medical_advice(message):
            lines.append("我不能做诊断命名，也不能给用药或处置建议；这里提供的是照护沟通建议和风险倾向摘要。")

        lines.append("可以先发一句短消息：我看到你这两天可能有点不轻松，我不催你解释，只是想陪你说说话。")
        return "\n".join(line for line in lines if line)

    def _opening_by_risk(self, risk_tier: str, profile_name: str, primary_state: str) -> str:
        if risk_tier == "crisis":
            return f"从最近记录看，{profile_name} 出现 crisis 级别安全信号，主要状态是 {primary_state}。请优先确认身边是否有人陪伴，并保持短句、平静、不中断。"
        if risk_tier in {"high", "medium"}:
            return f"从最近记录看，{profile_name} 有 {risk_tier} 风险倾向，主要状态是 {primary_state}。沟通重点是陪伴、减压、少追问。"
        if risk_tier == "low":
            return f"从最近记录看，{profile_name} 有轻度波动，主要状态是 {primary_state}。适合轻量问候，不要急着推进话题。"
        return f"从最近记录看，{profile_name} 当前没有明显高风险信号。可以保持稳定、自然的日常联系。"

    def _asks_for_diagnosis_or_medical_advice(self, message: str) -> bool:
        text = str(message or "")
        triggers = [
            "诊断",
            "是不是",
            "抑郁",
            "焦虑症",
            "吃药",
            "加量",
            "减量",
            "停药",
            "补服",
            "看医生",
            "去医院",
        ]
        return any(item in text for item in triggers)

    def _public_context_payload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "elder_user_id": context.get("elder_user_id"),
            "child_user_id": context.get("child_user_id"),
            "summary": context.get("summary") or {},
            "visible_evidence": context.get("visible_evidence") or [],
            "recent_family_alerts": context.get("recent_family_alerts") or [],
            "family_policy": context.get("family_policy") or {},
            "recent_interventions": context.get("recent_interventions") or [],
            "recent_family_history": context.get("recent_family_history") or [],
        }

    def _chunk_text(self, text: str) -> List[str]:
        value = str(text or "")
        if not value:
            return []
        return [value[index : index + self.chunk_size] for index in range(0, len(value), self.chunk_size)]
