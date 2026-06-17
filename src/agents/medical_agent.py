from typing import Dict, Any
import asyncio
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
from src.agents.companion_prompt import build_companion_system_prompt, risk_from_context, stage_from_context

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
            return await self._build_emergency_response(
                input_text,
                "high",
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
            )
        
        # 1. 分析意图与健康状况
        analysis = await self._analyze_health_intent(input_text)
        
        response_data = {
            "content": "",
            "action": "none", # alert_family, call_doctor, none
            "risk_level": "safe"
        }

        if analysis.get("is_emergency"):
            return await self._build_emergency_response(
                input_text,
                "high",
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
            )
        
        elif analysis.get("intent") == "medication_query":
            # 优先读取 MedicationPlan，旧 profile.medications 仅作兼容回退
            medication_summary = self._recorded_medication_summary(context, profile)
            response_data["content"] = await self._generate_structured_health_reply(
                intent="medication_query",
                input_text=input_text,
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
                facts={"medication_summary": medication_summary},
            )

        elif analysis.get("intent") == "symptom_report":
            # 记录不适，不做诊断或医疗处置建议
            symptom = analysis.get("symptom", "未知不适")
            symptom_text = self._format_symptom(symptom)
            response_data["content"] = await self._generate_structured_health_reply(
                intent="symptom_report",
                input_text=input_text,
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
                facts={"symptom_text": symptom_text},
            )
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

    async def astream_response(self, input_text: str, context: Dict[str, Any] = None):
        logger.info(f"MedicalAgent streaming received: {input_text}")
        context = context or {}
        profile = context.get("user_profile") or self.rag_helper.get_user_profile()
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        memory_context = context.get("memory_context", "")
        response_data = {
            "content": "",
            "action": "none",
            "risk_level": "safe",
        }

        async def emit_text(text: str) -> None:
            for token in self._chunk_text(text):
                response_data["content"] += token
                yield_items.append({"type": "token", "data": token})

        async def emit_token(token: str) -> None:
            if token:
                response_data["content"] += token
                yield_items.append({"type": "token", "data": token})

        async def drain(awaitable):
            task = __import__("asyncio").create_task(awaitable)
            while not task.done() or yield_items:
                while yield_items:
                    yield yield_items.pop(0)
                if not task.done():
                    await __import__("asyncio").sleep(0)
            task.result()

        yield_items = []

        if self._is_emergency_phrase(input_text):
            response_data.update(await self._build_emergency_response(
                input_text,
                "high",
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
            ))
            await emit_text(response_data.get("content", ""))
            while yield_items:
                yield yield_items.pop(0)
            yield {"type": "sos", "data": bool(response_data.get("sos"))}
            yield {"type": "done", "data": response_data}
            return

        analysis = await self._analyze_health_intent(input_text)
        if analysis.get("is_emergency"):
            response_data.update(await self._build_emergency_response(
                input_text,
                "high",
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
            ))
            await emit_text(response_data.get("content", ""))
            while yield_items:
                yield yield_items.pop(0)
            yield {"type": "sos", "data": bool(response_data.get("sos"))}
            yield {"type": "done", "data": response_data}
            return

        if analysis.get("intent") == "medication_query":
            medication_summary = self._recorded_medication_summary(context, profile)
            response_data["content"] = await self._generate_structured_health_reply(
                intent="medication_query",
                input_text=input_text,
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
                facts={"medication_summary": medication_summary},
            )
            response_data = self._finalize_response(response_data)
            for token in self._chunk_text(response_data["content"]):
                yield {"type": "token", "data": token}
        elif analysis.get("intent") == "symptom_report":
            symptom = analysis.get("symptom", "未知不适")
            symptom_text = self._format_symptom(symptom)
            response_data["content"] = await self._generate_structured_health_reply(
                intent="symptom_report",
                input_text=input_text,
                profile=profile,
                recent_history_text=recent_history_text,
                memory_context=memory_context,
                facts={"symptom_text": symptom_text},
            )
            response_data["risk_level"] = "medium"
            self._record_health_condition(symptom, context)
            response_data = self._finalize_response(response_data)
            for token in self._chunk_text(response_data["content"]):
                yield {"type": "token", "data": token}
        else:
            async for item in drain(self._generate_health_advice(
                input_text,
                profile,
                recent_history_text,
                memory_context,
                on_token=emit_token,
            )):
                yield item
            response_data = self._finalize_response(response_data)

        yield {"type": "done", "data": response_data}

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
                    return "；".join(formatted)

        meds = profile.get("medications", []) if isinstance(profile, dict) else []
        if not meds:
            return ""
        parts = []
        for item in meds:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                time_text = str(item.get("time") or item.get("schedule") or "").strip()
                if name and time_text:
                    parts.append(f"{name}（{time_text}）")
                elif name:
                    parts.append(name)
            elif str(item).strip():
                parts.append(str(item).strip())
        return "；".join(parts)

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
            pieces.append(f"时间：{schedule_text}")
        return "；".join(pieces)

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
        return "、".join(items)

    async def _analyze_health_intent(self, text: str) -> Dict:
        prompt = ChatPromptTemplate.from_template("""
        你是小暖的健康关怀意图识别阶段。只做意图与紧急程度识别，不给老人回复。
        不生成诊断、治疗或用药建议；只抽取老人明确说出的症状或用药问题。
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

    async def _generate_health_advice(self, text: str, profile: Dict[str, Any], recent_history_text: str, memory_context: str, on_token=None) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="medical_reply",
                    stage="medical.safety_check",
                    risk_tier="medium",
                    task="用口语回应老人的健康相关问题，重点是记录、安抚和基于已知记录的提醒。",
                    extra_rules=[
                        "不做诊断命名，不说“您这是X病”。",
                        "不提供就医建议、治疗处置、用药调整、加减药或补服建议。",
                        "涉及用药时，只能基于已记录信息，并使用“按记录 / 已记录”的表述。",
                        "默认2到3句话，最多4句话。",
                    ],
                ),
            ),
            (
                "human",
                "老人画像: {profile}\n最近对话: {recent_history_text}\n补充记忆: {memory_context}\n\n问题: {text}\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "text": text,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text,
            "memory_context": memory_context or "暂无"
        }
        if on_token:
            content = ""
            async for chunk in chain.astream(payload):
                token = getattr(chunk, "content", "") or ""
                if token:
                    content += token
                    await on_token(token)
            return self.safety_policy.sanitize_response(content)

        response = await chain.ainvoke(payload)
        return self.safety_policy.sanitize_response(response.content)

    async def _generate_structured_health_reply(
        self,
        *,
        intent: str,
        input_text: str,
        profile: Dict[str, Any],
        recent_history_text: str,
        memory_context: str,
        facts: Dict[str, Any],
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase=f"medical_{intent}_reply",
                    stage="medical.safety_check",
                    risk_tier="medium",
                    task=(
                        "根据系统事实生成健康场景下的老人端回复。"
                        "只能使用事实字段，不能补充未记录的药物、症状、诊断或处置。"
                    ),
                    extra_rules=[
                        "只输出1到2句自然中文。",
                        "medication_summary 为空时，说明当前上下文没有已记录用药安排，并温和建议补充记录。",
                        "有 medication_summary 时，只按已记录信息复述，不能建议补服、停药或改药。",
                        "symptom_report 时，确认已记下明确症状，建议和家人/社区一起确认，不做诊断。",
                    ],
                ),
            ),
            (
                "human",
                "老人画像: {profile}\n最近对话: {recent_history_text}\n补充记忆: {memory_context}\n老人刚才说: {input_text}\n意图: {intent}\n系统事实: {facts}\n\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "intent": intent,
            "input_text": input_text,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text,
            "memory_context": memory_context or "暂无",
            "facts": json.dumps(facts, ensure_ascii=False),
        })
        return self.safety_policy.sanitize_response(response.content)

    def _chunk_text(self, text: str, chunk_size: int = 24):
        value = str(text or "")
        return [value[index:index + chunk_size] for index in range(0, len(value), chunk_size)]

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

    async def _build_emergency_response(
        self,
        input_text: str,
        level: str,
        *,
        profile: Dict[str, Any],
        recent_history_text: str,
        memory_context: str,
    ) -> Dict[str, Any]:
        tool_output = await asyncio.to_thread(self._trigger_emergency_contact, input_text, level)
        content = await self._generate_emergency_reply(
            input_text=input_text,
            profile=profile,
            recent_history_text=recent_history_text,
            memory_context=memory_context,
            tool_output=tool_output,
        )
        logger.warning(f"EMERGENCY ALERT: {input_text}")
        return self._finalize_response({
            "content": content,
            "action": "alert_family",
            "risk_level": "high",
            "sos": bool(tool_output.get("trigger_sos", True)),
            "emergency_contact_result": tool_output
        })

    async def _generate_emergency_reply(
        self,
        *,
        input_text: str,
        profile: Dict[str, Any],
        recent_history_text: str,
        memory_context: str,
        tool_output: Dict[str, Any],
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="medical_emergency_reply",
                    stage="medical.safety_check",
                    risk_tier="high",
                    task="生成急症/跌倒/呼吸困难场景下给老人的第一句和第二句稳定回应。",
                    extra_rules=[
                        "只输出1到2句，非常短、稳、直接。",
                        "明确已经触发求助/家属确认时，可以说正在帮忙联系，但不要承诺现实中尚未完成的处置。",
                        "不要给诊断、治疗、移动身体、吃药、喝水等处置建议。",
                    ],
                ),
            ),
            (
                "human",
                "老人画像: {profile}\n最近对话: {recent_history_text}\n补充记忆: {memory_context}\n老人刚才说: {input_text}\n求助动作结果: {tool_output}\n\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        try:
            response = await chain.ainvoke({
                "input_text": input_text,
                "profile": json.dumps(profile, ensure_ascii=False),
                "recent_history_text": recent_history_text,
                "memory_context": memory_context or "暂无",
                "tool_output": json.dumps(tool_output, ensure_ascii=False),
            })
            return self.safety_policy.sanitize_response(response.content, risk_tier="high")
        except Exception as exc:
            logger.warning(f"Emergency reply generation failed: {exc}")
            return ""

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
            "community_message": "",
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
