from typing import Dict, Any
import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import Config
from src.policies.safety_policy import SafetyPolicy
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper
from src.tools.professional_skills import ProfessionalSkills
from src.services.user_context_service import UserContextService

class MedicalAgent:
    def __init__(
        self,
        safety_policy: SafetyPolicy | None = None,
        user_context_service: UserContextService | None = None,
        medication_reminder_service: Any | None = None,
    ):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.1
        )
        self.rag_helper = RAGHelper()
        self.safety_policy = safety_policy or SafetyPolicy()
        self.user_context_service = user_context_service
        self.medication_reminder_service = medication_reminder_service
        self.emergency_keywords = ["救命", "摔倒", "跌倒", "起不来", "胸口疼", "胸闷", "喘不上气", "呼吸困难", "快不行了"]

    async def arun(self, input_text: str, context: Dict[str, Any] = None):
        """
        处理医药康复相关的请求
        1. 提醒吃药 (基于时间/画像)
        2. 健康咨询回答
        3. 身体不适预警
        """
        logger.info(f"MedicalAgent received: {input_text}")
        context = context or {}
        profile = context.get("user_profile") or self.rag_helper.get_user_profile()
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        memory_context = context.get("memory_context", "")

        if self._is_emergency_phrase(input_text):
            return self._build_emergency_response(input_text, "high")
        
        # 1. 分析意图与健康状况
        analysis = await self._analyze_health_intent(input_text)
        
        response_data = {
            "content": "",
            "action": "none", # alert_family, call_doctor, none
            "risk_level": "safe"
        }

        if analysis.get("is_emergency"):
            return self._build_emergency_response(input_text, "high")
        
        elif analysis.get("intent") == "medication_query":
            # ??????????MedicationPlan ??????? profile.medications ???????
            medication_summary = self._recorded_medication_summary(context, profile)
            if not medication_summary:
                response_data["content"] = "???????????????????????????????????"
            else:
                response_data["content"] = f"??????????????{medication_summary}???????????????????????????????????????"

        elif analysis.get("intent") == "symptom_report":
            # 记录不适，不做诊断或医疗处置建议
            symptom = analysis.get("symptom", "未知不适")
            symptom_text = self._format_symptom(symptom)
            response_data["content"] = f"我先帮您记下：{symptom_text}。咱们先慢一点，我可以帮您通知家里人一起确认情况。"
            response_data["risk_level"] = "medium"
            # 更新画像中的健康状况
            self._record_health_condition(symptom, context)
            
        else:
            # 一般健康咨询
            response_data["content"] = await self._generate_health_advice(
                input_text,
                profile,
                recent_history_text,
                memory_context
            )

        return self._finalize_response(response_data)

    def _record_health_condition(self, symptom: Any, context: Dict[str, Any]) -> None:
        user_context_service = getattr(self, "user_context_service", None)
        if user_context_service is not None:
            user_id = str(context.get("user_id") or "user_001").strip() or "user_001"
            user_context_service.update_profile(user_id, {"health_condition": symptom})
            return
        self.rag_helper.update_user_profile("health_condition", symptom)

    def _recorded_medication_summary(self, context: Dict[str, Any], profile: Dict[str, Any]) -> str:
        user_id = str(context.get("user_id") or "user_001").strip() or "user_001"
        service = getattr(self, "medication_reminder_service", None)
        if service is not None:
            try:
                plans = service.list_plans(user_id, include_inactive=False)
            except TypeError:
                plans = service.list_plans(user_id)
            except Exception as exc:
                logger.warning(f"Failed to read medication plans for {user_id}: {exc}")
                plans = []
            if plans:
                formatted = [
                    self._format_medication_plan_for_reply(plan)
                    for plan in plans
                ]
                formatted = [item for item in formatted if item]
                if formatted:
                    return "?".join(formatted)

        meds = profile.get("medications", []) if isinstance(profile, dict) else []
        if not meds:
            return ""
        parts = []
        for item in meds:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                time_text = str(item.get("time") or item.get("schedule") or "").strip()
                if name and time_text:
                    parts.append(f"{name}?{time_text}?")
                elif name:
                    parts.append(name)
            elif str(item).strip():
                parts.append(str(item).strip())
        return "?".join(parts)

    def _format_medication_plan_for_reply(self, plan: Any) -> str:
        name = str(self._field_value(plan, "name") or "").strip()
        if not name:
            return ""
        pieces = [name]
        dosage = str(self._field_value(plan, "dosage_text") or "").strip()
        instruction = str(self._field_value(plan, "instruction_text") or "").strip()
        schedule_text = self._schedule_text(self._field_value(plan, "schedule", default=[]))
        if dosage:
            pieces.append(dosage)
        if instruction:
            pieces.append(instruction)
        if schedule_text:
            pieces.append(f"???{schedule_text}")
        return "?".join(pieces)

    def _field_value(self, obj: Any, field: str, default: Any = "") -> Any:
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field, default)

    def _schedule_text(self, schedule: Any) -> str:
        if not schedule:
            return ""
        items = []
        for entry in schedule:
            if isinstance(entry, dict):
                time_text = str(entry.get("time") or "").strip()
                label = str(entry.get("label") or "").strip()
            else:
                time_text = str(getattr(entry, "time", "") or "").strip()
                label = str(getattr(entry, "label", "") or "").strip()
            if time_text and label:
                items.append(f"{label} {time_text}")
            elif time_text:
                items.append(time_text)
            elif label:
                items.append(label)
        return "?".join(items)

    async def _analyze_health_intent(self, text: str) -> Dict:
        prompt = ChatPromptTemplate.from_template("""
        你是健康关怀意图分类器。只做意图与紧急程度识别，不生成诊断、治疗或用药建议。
        输入: {text}
        
        输出 JSON (无 Markdown):
        {{
            "intent": "medication_query (问药) / symptom_report (报病) / general_query (咨询) / other",
            "is_emergency": true/false (是否紧急，如“救命，我摔倒了”“我胸口疼得厉害”“我喘不上气了”),
            "symptom": "提取的症状 (如头晕、腿疼)，无则 null"
        }}
        """)
        chain = prompt | self.llm | JsonOutputParser()
        return await chain.ainvoke({"text": text})

    async def _generate_health_advice(self, text: str, profile: Dict[str, Any], recent_history_text: str, memory_context: str) -> str:
        prompt = ChatPromptTemplate.from_template("""
        你是健康关怀记录与已知医嘱提醒助手。请用**口语**回应老人的健康相关问题。
        老人画像: {profile}
        最近对话: {recent_history_text}
        补充记忆: {memory_context}

        要求：
        1. 默认用2到3句话回答，说明可以稍微完整一点，最多不超过4句话。
        2. 不做诊断命名，不说“您这是X病”。
        3. 不给出就医建议、治疗处置、用药调整、加减药或补服建议。
        4. 如果涉及用药，只能基于已记录信息，并使用“按记录 / 已记录”的表述。
        5. 语气亲切、关怀；不列点，不用Markdown，直接说。
        
        问题: {text}
        回答:
        """)
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "text": text,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text,
            "memory_context": memory_context or "暂无"
        })
        return self.safety_policy.sanitize_response(response.content)

    def _finalize_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        finalized = dict(response_data or {})
        content = finalized.get("content", "")
        if isinstance(content, str):
            finalized["content"] = self.safety_policy.sanitize_response(
                content,
                risk_tier=finalized.get("risk_level")
            )
        return finalized

    def _format_symptom(self, symptom: Any) -> str:
        if isinstance(symptom, list):
            items = [str(item).strip() for item in symptom if str(item).strip()]
            if not items:
                return "有点不舒服"
            return "、".join(items)

        if symptom is None:
            return "有点不舒服"

        symptom_text = str(symptom).strip()
        return symptom_text or "有点不舒服"

    def _is_emergency_phrase(self, text: str) -> bool:
        return any(keyword in text for keyword in self.emergency_keywords)

    def _build_emergency_response(self, input_text: str, level: str) -> Dict[str, Any]:
        tool_output = self._trigger_emergency_contact(input_text, level)
        logger.warning(f"EMERGENCY ALERT: {input_text}")
        return self._finalize_response({
            "content": "您先别慌，我已经马上联系家里人了。您先尽量别乱动，慢慢呼吸，我陪着您。",
            "action": "alert_family",
            "risk_level": "high",
            "sos": bool(tool_output.get("trigger_sos", True)),
            "emergency_contact_result": tool_output
        })

    def _trigger_emergency_contact(self, reason: str, level: str) -> Dict[str, Any]:
        try:
            result = ProfessionalSkills.emergency_contact.invoke({
                "reason": reason,
                "level": level
            })
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
        except Exception as exc:
            logger.error(f"Emergency contact skill failed: {exc}")

        return {
            "status": "fallback",
            "trigger_sos": True,
            "level": level,
            "reason_summary": "elder_reported_emergency",
            "family_message": f"老人发出紧急求助：{reason}",
            "community_message": "有老人发出紧急求助，请社区值守端关注。",
            "community_raw_quote_visible": False,
            "actions": []
        }

    def check_medication_reminder(self):
        """
        Backward-compatible no-op.

        Medication timing is owned by MedicationReminderService and
        TimedEventService. MedicalAgent should not independently schedule,
        infer, or emit medication reminders from wall-clock time.
        """
        return None
