import httpx
import json
import re
import traceback
import asyncio
import uuid
from typing import Dict, Any, Optional, List
from langchain_core.prompts import ChatPromptTemplate
from openai import AsyncOpenAI
from src.utils.logger import logger
from src.config import Config

from src.agents.antifraud_agent import AntiFraudAgent
from src.agents.daily_life_agent import DailyLifeAgent
from src.agents.emotional_agent import EmotionalConnectionAgent
from src.agents.family_agent import FamilyAgent
from src.agents.interest_agent import InterestAgent
from src.agents.medical_agent import MedicalAgent
from src.agents.mental_health_agent import MentalHealthAgent
from src.agents.proactive_agent import ProactiveAgent
from src.agents.router_agent import RouterAgent
from src.agents.companion_prompt import build_companion_system_prompt
from src.agents.planning_agent import PlanningAgent
from src.policies.safety_policy import SafetyPolicy
from src.schemas.mental_health import MentalRiskAssessment
from src.schemas.planner import PlannerJob
from src.services.assessment_service import AssessmentService
from src.services.action_session_service import ActionSessionService
from src.services.background_planner_service import BackgroundPlannerService
from src.services.care_plan_service import CarePlanService
from src.services.community_service import CommunityService
from src.services.context_guard import ContextGuard
from src.services.family_context_service import FamilyContextService
from src.services.family_policy_service import FamilyPolicyService
from src.services.frontend_action_service import FrontendActionService
from src.services.medication_reminder_service import MedicationReminderService
from src.services.music_library_service import MusicLibraryService
from src.services.photo_library_service import PhotoLibraryService
from src.services.response_style_guard import ResponseStyleGuard
from src.services.relay_message_service import RelayMessageService
from src.services.scene_context_service import SceneContextService
from src.services.timed_event_service import TimedEventService
from src.services.user_context_service import UserContextService
from src.tools.professional_skills import ProfessionalSkills

# 辅助函数：构造 SSE 事件格式
def create_event(event_type: str, data: Any):
    return json.dumps({
        "type": event_type,
        "data": data
    }, ensure_ascii=False)

class SystemOrchestrator:
    def __init__(self):
        logger.info("正在初始化多智能体系统...")
        try:
            self.router = RouterAgent()
            self.first_response_client = AsyncOpenAI(
                api_key=Config.OPENAI_API_KEY,
                base_url=Config.OPENAI_API_BASE,
                timeout=Config.FIRST_RESPONSE_TIMEOUT,
                max_retries=0,
            )
            self.chat_stream_client = AsyncOpenAI(
                api_key=Config.OPENAI_API_KEY,
                base_url=Config.OPENAI_API_BASE,
                timeout=Config.CHAT_STREAM_TIMEOUT,
                max_retries=0,
            )
            self.safety_policy = SafetyPolicy()
            self.user_context_service = UserContextService()
            self.data_store = self.user_context_service.store
            self.profile_service = self.user_context_service.profile_service
            self.photo_library_service = PhotoLibraryService(self.data_store)
            self.music_library_service = MusicLibraryService(self.data_store)
            ProfessionalSkills.register_photo_library_service(self.photo_library_service)
            ProfessionalSkills.register_music_library_service(self.music_library_service)
            self.emotional_agent = EmotionalConnectionAgent()
            self.medical_agent = MedicalAgent(
                safety_policy=self.safety_policy,
                user_context_service=self.user_context_service,
            )
            self.daily_life_agent = DailyLifeAgent()
            self.interest_agent = InterestAgent()
            self.mental_health_agent = MentalHealthAgent(safety_policy=self.safety_policy)
            self.antifraud_agent = AntiFraudAgent(safety_policy=self.safety_policy)
            self.assessment_service = AssessmentService(self.data_store)
            self.action_session_service = ActionSessionService(self.data_store)
            self.care_plan_service = CarePlanService(self.data_store)
            self.context_guard = ContextGuard()
            self.scene_context_service = SceneContextService(self.user_context_service)
            self.response_style_guard = ResponseStyleGuard()
            self.frontend_action_service = FrontendActionService()
            self.timed_event_service = TimedEventService(self.data_store)
            self.medication_reminder_service = MedicationReminderService(
                self.data_store,
                self.timed_event_service,
            )
            self.medical_agent.medication_reminder_service = self.medication_reminder_service
            self.relay_message_service = RelayMessageService(self.data_store)
            self.community_service = CommunityService(
                self.data_store,
                self.relay_message_service,
            )
            self.family_policy_service = FamilyPolicyService(
                self.data_store,
                self.relay_message_service,
            )
            self.family_context_service = FamilyContextService(
                self.data_store,
                care_plan_service=self.care_plan_service,
                family_policy_service=self.family_policy_service,
                relay_message_service=self.relay_message_service,
                profile_service=self.profile_service,
            )
            self.family_agent = FamilyAgent(
                self.family_context_service,
                safety_policy=self.safety_policy,
            )
            self.planning_agent = PlanningAgent(
                self.care_plan_service,
                safety_policy=self.safety_policy,
            )
            self.background_planner_service = BackgroundPlannerService(
                self.data_store,
                self.care_plan_service,
                planning_agent=self.planning_agent,
                relay_message_service=self.relay_message_service,
                action_session_service=self.action_session_service,
                on_job_event=self._record_planner_job_event,
            )
            self.proactive_agent = ProactiveAgent(user_context_service=self.user_context_service)
            self.state_lock = asyncio.Lock()
            self.background_tasks = set()
            self.last_system_state = {
                "last_input": "",
                "last_route": "",
                "tool_calls": [],
                "background_tasks": [],
                "context_snapshot": {},
                "agent_context": {},
                "llm_inputs": [],
            }
            logger.info("系统初始化完成。")
        except Exception as e:
            logger.error(f"智能体初始化失败: {e}")
            raise e

    async def check_and_generate_proactive_event(self, user_id: str = "user_001", now=None):
        """检查是否需要生成主动问候"""
        try:
            timed_events = self.get_due_timed_events(user_id, now=now)
            if timed_events:
                logger.info(f"Generated timed event: {timed_events[0]}")
                return create_event("timed_event", self.format_timed_event_response(timed_events[0]))

            proactive_context = await self._build_proactive_context(user_id)
            result = await self.proactive_agent.check_and_generate(
                user_id=user_id,
                context=proactive_context,
            )
            if result:
                logger.info(f"Generated proactive event: {result}")
                return create_event("proactive_question", result)
            return None
        except Exception as e:
            logger.error(f"Proactive check failed: {e}")
            return None

    def get_due_timed_events(self, user_id: str, now=None):
        self.medication_reminder_service.scan_due_reminders(user_id, now=now)
        return self.timed_event_service.get_due_events(user_id, now=now)

    def acknowledge_timed_event(self, event_id: str, ack: Any, now=None) -> Dict[str, Any]:
        elder_user_id = ack.elder_user_id
        events = self.timed_event_service.list_events(elder_user_id)
        matched = next((event for event in events if event.event_id == event_id), None)
        if matched is None:
            raise ValueError(f"Timed event not found: {event_id}")

        target_status = "snoozed" if ack.ack == "snooze" else "acknowledged"
        dose_event = None
        if matched.event_type in {"medication_due", "medication_overdue"}:
            dose_event_id = matched.payload.get("dose_event_id")
            if not dose_event_id:
                raise ValueError("Medication timed event is missing dose_event_id")
            dose_event = self.medication_reminder_service.acknowledge(
                elder_user_id,
                dose_event_id,
                ack,
                now=now,
            )
            updated_events = self.timed_event_service.mark_events_by_payload(
                elder_user_id,
                "dose_event_id",
                dose_event_id,
                target_status,
                now=now,
            )
        else:
            updated_events = [
                self.timed_event_service.mark_event(
                    elder_user_id,
                    event_id,
                    target_status,
                    now=now,
                )
            ]

        return {
            "event_id": event_id,
            "ack": ack.ack,
            "timed_events": [self.format_timed_event_response(event) for event in updated_events],
            "dose_event": self._model_to_dict(dose_event) if dose_event else None,
        }

    def format_timed_event_response(self, event: Any) -> Dict[str, Any]:
        data = self._model_to_dict(event)
        payload = data.get("payload") or {}
        data["display_text"] = payload.get("content", "")
        return data

    def format_assessment_response(self, assessment: MentalRiskAssessment) -> Dict[str, Any]:
        data = self._model_to_dict(assessment)
        data["assessment_id"] = data.get("id")
        data["tier"] = data.get("risk_tier")
        return data

    def format_care_plan_response(self, care_plan: Any) -> Dict[str, Any]:
        return self._model_to_dict(care_plan)

    def _schedule_assessment_background_tasks(
        self,
        assessment: MentalRiskAssessment,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if assessment.risk_tier not in {"medium", "high", "crisis"}:
            relay_required = False
        else:
            relay_required = True

        if relay_required:
            self._schedule_background_task(
                asyncio.to_thread(self.relay_message_service.create_from_assessment, assessment),
                label="relay_from_assessment",
                metadata={
                    "assessment_id": assessment.id,
                    "risk_tier": assessment.risk_tier,
                    "elder_user_id": assessment.elder_user_id,
                },
            )

        self.background_planner_service.schedule_from_assessment(assessment, context=context)

    def _schedule_background_task(self, awaitable: Any, label: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        task = asyncio.create_task(awaitable)
        if not hasattr(self, "background_tasks"):
            self.background_tasks = set()
        self.background_tasks.add(task)
        self._record_background_task(label, "scheduled", metadata)

        def _on_done(done_task):
            self.background_tasks.discard(done_task)
            try:
                done_task.result()
                self._record_background_task(label, "done", metadata)
            except Exception as exc:
                logger.error(f"Background task failed: {label}: {exc}")
                self._record_background_task(
                    label,
                    "failed",
                    {**(metadata or {}), "error": str(exc)},
                )

        task.add_done_callback(_on_done)

    def _record_background_task(
        self,
        label: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        state = getattr(self, "last_system_state", None)
        if not isinstance(state, dict):
            return
        tasks = state.setdefault("background_tasks", [])
        tasks.append({
            "label": label,
            "status": status,
            "metadata": metadata or {},
        })
        if len(tasks) > 20:
            del tasks[:-20]

    def _record_planner_job_event(self, job: PlannerJob, status: str) -> None:
        self._record_background_task(
            "planner_from_assessment",
            status,
            {
                "job_id": job.job_id,
                "assessment_id": job.assessment_id,
                "risk_tier": job.priority,
                "elder_user_id": job.elder_user_id,
                "turn_id": job.base_turn_id,
                "stale_reason": job.stale_reason,
            },
        )

    def _structured_error(
        self,
        code: str,
        *,
        source: str,
        retryable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "code": code,
            "source": source,
            "retryable": retryable,
        }
        if details:
            payload["details"] = details
        return payload

    async def process_chat_stream(self, user_input: str, context: Optional[Dict[str, Any]] = None):
        """Low-latency chat stream for the elder-facing realtime conversation API.

        This path deliberately avoids the full multi-agent orchestration before
        the first token. It reads only local profile/history, streams one LLM
        answer, then performs risk assessment, care-plan work, status updates,
        and memory writes in the background.
        """
        context = dict(context or {})
        user_id = self.user_context_service.normalize_user_id(context.get("user_id"))
        context["user_id"] = user_id
        turn_id = str(context.get("turn_id") or f"chat_{uuid.uuid4().hex}")
        context["turn_id"] = turn_id
        source = str(context.get("source") or "api_chat")
        quick_assessment_detail = self._build_quick_assessment_detail(user_input, context)
        quick_ack = self._build_quick_ack(user_input, quick_assessment_detail)
        if quick_ack:
            context["immediate_reply"] = quick_ack

        logger.info(f"realtime chat input: {user_input}")
        yield create_event("step", {"name": "realtime_chat", "status": "running"})
        if quick_ack:
            yield create_event("token", quick_ack)
            await asyncio.sleep(0)

        try:
            profile, history_layers = await self._get_profile_layered_context_quick(
                user_id,
                timeout=0.25,
                label_prefix="realtime_chat",
            )
            recent_history = self._sanitize_recent_history(history_layers.get("recent_window", []))
            recent_history_text = history_layers.get("recent_window_text") or self._format_recent_history(recent_history)
            conversation_summary = str(history_layers.get("summary") or "").strip()
            chat_context = {
                **context,
                "user_profile": profile,
                "recent_history": recent_history,
                "recent_history_text": recent_history_text,
                "conversation_summary": conversation_summary,
                "history_layers": history_layers,
                "memory_context": self._build_memory_context(
                    profile=profile,
                    conversation_summary=conversation_summary,
                    recent_history_text=recent_history_text,
                    semantic_memory="",
                ),
                "semantic_memory_context": "",
            }
            prompt_payload = {
                "mode": "realtime_chat",
                "model": Config.CHAT_STREAM_MODEL,
                "profile": profile,
                "recent_history": recent_history_text,
                "conversation_summary": conversation_summary,
                "history_layers": history_layers,
                "memory_context": chat_context["memory_context"],
                "user_input": user_input,
                "source": source,
                "already_said_to_elder": quick_ack,
            }
            messages = self._build_realtime_chat_messages(prompt_payload)

            async with self.state_lock:
                self.last_system_state["last_route"] = "realtime_chat"
                self.last_system_state["last_input"] = user_input
                self.last_system_state["tool_calls"] = []
                self.last_system_state["background_tasks"] = []
                self.last_system_state["context_snapshot"] = self._build_context_snapshot(chat_context)
                self.last_system_state["agent_context"] = {
                    "turn_id": turn_id,
                    "user_id": user_id,
                    "user_input": user_input,
                    "target_agent": "realtime_chat",
                    "immediate_reply": quick_ack,
                    "memory_context": chat_context["memory_context"],
                    "conversation_summary": conversation_summary,
                    "history_layers": history_layers,
                    "recent_history_text": recent_history_text,
                    "raw_context_keys": sorted(str(key) for key in chat_context.keys()),
                }
                self.last_system_state["llm_inputs"] = [
                    {
                        "source": "realtime_chat",
                        "payload": prompt_payload,
                    }
                ]

            yield create_event("expression", self._guess_chat_expression(user_input))

            stream = await self.chat_stream_client.chat.completions.create(
                model=Config.CHAT_STREAM_MODEL,
                messages=messages,
                temperature=0.45,
                max_tokens=180,
                stream=True,
            )

            full_response = quick_ack or ""
            prefix_buffer = ""
            prefix_filter_done = False
            style_guard = getattr(self, "response_style_guard", ResponseStyleGuard())
            parentheses_depth = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                raw_text = getattr(delta, "content", None) if delta else ""
                if not raw_text:
                    continue
                filtered = []
                for char in raw_text:
                    if char in ["(", "（"]:
                        parentheses_depth += 1
                        continue
                    if char in [")", "）"]:
                        if parentheses_depth > 0:
                            parentheses_depth -= 1
                        continue
                    if parentheses_depth == 0:
                        filtered.append(char)
                text = "".join(filtered)
                if not text:
                    continue
                if not prefix_filter_done:
                    prefix_buffer += text
                    text, prefix_buffer, prefix_filter_done = style_guard.filter_repeated_input_prefix(
                        prefix_buffer,
                        user_input,
                    )
                    if not text:
                        continue
                full_response += text
                yield create_event("token", text)
                await asyncio.sleep(0)

            full_response = self.safety_policy.sanitize_response(full_response, risk_tier="safe")
            if full_response:
                self._schedule_background_task(
                    self._finalize_realtime_chat_turn(
                        user_id=user_id,
                        turn_id=turn_id,
                        user_input=user_input,
                        assistant_response=full_response,
                        context=chat_context,
                    ),
                    label="finalize_realtime_chat",
                    metadata={"turn_id": turn_id, "elder_user_id": user_id},
                )
            else:
                yield create_event(
                    "error",
                    self._structured_error(
                        "empty_model_response",
                        source="realtime_chat",
                    ),
                )

            yield create_event("step", {"name": "realtime_chat", "status": "done"})
            yield create_event("done", "stop")
        except Exception as exc:
            logger.error(f"Realtime chat stream failed: {exc}")
            logger.error(traceback.format_exc())
            yield create_event(
                "error",
                self._structured_error(
                    "realtime_chat_failed",
                    source="realtime_chat",
                ),
            )
            yield create_event("done", "stop")

    async def process_input_stream(self, user_input: str, context: Optional[Dict[str, Any]] = None):
        """
        处理输入流，协调智能体运行
        """
        context = dict(context or {})
        user_id = self.user_context_service.normalize_user_id(context.get("user_id"))
        context["user_id"] = user_id
        turn_id = str(context.get("turn_id") or f"turn_{uuid.uuid4().hex}")
        context["turn_id"] = turn_id
        assessment_task = asyncio.create_task(
            asyncio.to_thread(self.assessment_service.assess_text, user_input, dict(context))
        )
        care_plan_task = asyncio.create_task(
            asyncio.to_thread(self.care_plan_service.get_plan, user_id)
        )
        shared_context_task = asyncio.create_task(
            self._build_shared_context(user_input, context)
        )
        quick_assessment_detail = self._build_quick_assessment_detail(user_input, context)
        context["risk_assessment_preview"] = quick_assessment_detail
        quick_ack = self._build_quick_ack(user_input, quick_assessment_detail)
        if quick_ack:
            context["immediate_reply"] = quick_ack

        # 0. 立即返回日志，给前端即时反馈
        logger.info(f"收到用户输入: {user_input}")
        yield create_event("log", f"收到用户输入: {user_input}")

        valid_agents = [
            "emotional_agent", "medical_agent", "daily_life_agent",
            "interest_agent", "mental_health_agent", "antifraud_agent"
        ]
        force_agent = context.get("force_agent")

        async def _route_when_ready() -> str:
            assessed = await assessment_task
            plan = await care_plan_task
            route_context = dict(context)
            route_context["risk_assessment"] = self.format_assessment_response(assessed)
            route_context["care_plan"] = self.format_care_plan_response(plan)
            return await self._select_target_agent(
                user_input,
                context=route_context,
                assessment=assessed,
                care_plan=plan,
                force_agent=force_agent,
                valid_agents=valid_agents,
            )

        route_task = asyncio.create_task(_route_when_ready())

        immediate_reply = quick_ack or ""
        yield create_event("step", {"name": "first_response_llm", "status": "running"})
        yield create_event("step", {"name": "router", "status": "running"})
        if quick_ack:
            yield create_event("token", quick_ack)
            await asyncio.sleep(0)
        try:
            async with asyncio.timeout(Config.FIRST_RESPONSE_TIMEOUT):
                async for first_chunk in self._stream_llm_first_response(
                    user_input=user_input,
                    user_id=user_id,
                    assessment_detail=quick_assessment_detail,
                    already_said_to_elder=immediate_reply,
                ):
                    immediate_reply += first_chunk
                    context["immediate_reply"] = immediate_reply
                    yield create_event("token", first_chunk)
        except asyncio.TimeoutError:
            yield create_event("log", "LLM first response timed out; continuing slow agent chain")
        if immediate_reply:
            context["immediate_reply"] = immediate_reply
            yield create_event("step", {"name": "first_response_llm", "status": "done"})
            yield create_event("log", "LLM first response streamed before slow agent chain")
        else:
            yield create_event("step", {"name": "first_response_llm", "status": "skipped"})

        try:
            assessment = await assessment_task
            current_care_plan = await care_plan_task
        except Exception as exc:
            logger.error(f"Risk/care-plan preparation failed: {exc}")
            logger.error(traceback.format_exc())
            route_task.cancel()
            shared_context_task.cancel()
            await asyncio.gather(route_task, shared_context_task, return_exceptions=True)
            yield create_event(
                "error",
                self._structured_error(
                    "risk_or_plan_preparation_failed",
                    source="orchestrator",
                ),
            )
            yield create_event("done", "stop")
            return

        assessment_detail = self.format_assessment_response(assessment)
        context["risk_assessment"] = assessment_detail
        context["care_plan"] = self.format_care_plan_response(current_care_plan)
        yield create_event("risk_detail", assessment_detail)
        if assessment.risk_tier != "safe":
            yield create_event("risk", assessment.risk_tier)
        if assessment.risk_tier == "crisis":
            yield create_event("sos", True)

        async def _fetch_visual():
            if context.get("visual_analysis"):
                return context["visual_analysis"]
            try:
                async with httpx.AsyncClient(timeout=0.3) as client:
                    response = await client.get(Config.VISUAL_ANALYSIS_URL, params={"mode": "camera"})
                    if response.status_code == 200:
                        data = response.json()
                        if data:
                            context["visual_analysis"] = data
                            return data
            except Exception:
                pass
            return None

        visual_task = asyncio.create_task(_fetch_visual())

        try:
            target_agent_name = await route_task  # route was started in parallel above
        except Exception as exc:
            logger.error(f"Route task failed: {exc}")
            logger.error(traceback.format_exc())
            target_agent_name = "emotional_agent"
        yield create_event("step", {"name": "router", "status": "done", "output": target_agent_name})  # emitted before shared context is awaited
        yield create_event("log", f"🤖 路由至智能体: {target_agent_name}")  # emitted before shared context is awaited

        try:
            shared_context = await shared_context_task
        except Exception as exc:
            logger.error(f"Shared context task failed: {exc}")
            logger.error(traceback.format_exc())
            profile, recent_history = await self._get_profile_history_quick(
                user_id,
                limit=5,
                timeout=0.2,
                label_prefix="shared_context_fallback",
            )
            recent_history = self._sanitize_recent_history(recent_history)
            recent_history_text = self._format_recent_history(recent_history)
            shared_context = dict(context)
            shared_context["user_profile"] = profile
            shared_context["recent_history"] = recent_history
            shared_context["recent_history_text"] = recent_history_text
            shared_context["memory_context"] = self._build_memory_context(
                profile=profile,
                recent_history_text=recent_history_text,
                semantic_memory="",
            )
            shared_context["semantic_memory_context"] = ""
        shared_context["risk_assessment"] = assessment_detail
        shared_context["care_plan"] = self.format_care_plan_response(current_care_plan)
        if immediate_reply:
            shared_context["immediate_reply"] = immediate_reply
        shared_context = self.context_guard.sanitize_context(shared_context)
        shared_context["scene_context"] = self.scene_context_service.build(
            user_input=user_input,
            context=shared_context,
            assessment=assessment,
            care_plan=current_care_plan,
            source=str(context.get("source") or "chat"),
        )
        self._schedule_assessment_background_tasks(assessment, context=shared_context)

        # 视觉API 不等：若 RAG 完成后 0.3s 内没拿到结果就放弃
        visual_emotion = None
        try:
            visual_emotion = await asyncio.wait_for(visual_task, timeout=0.3)
        except asyncio.TimeoutError:
            pass
        voice_text = shared_context.get("audio_transcript")
        if visual_emotion:
            yield create_event("log", f"📷 接收到视觉情感数据: {visual_emotion}")
        if voice_text:
            yield create_event("log", f"🎤 接收到语音转文字: {voice_text}")

        frontend_action = self._build_immediate_frontend_action(
            user_input,
            turn_id=turn_id,
            context=shared_context,
        )
        if frontend_action is not None:
            yield create_event("action", frontend_action)

        # 2. 路由阶段（仅输出日志，决策已在上面完成）
        async with self.state_lock:
            self.last_system_state["last_route"] = target_agent_name
            self.last_system_state["last_input"] = user_input
            self.last_system_state["tool_calls"] = [] # Reset tool calls for the new turn
            self.last_system_state["llm_inputs"] = []
            self.last_system_state["context_snapshot"] = self._build_context_snapshot(shared_context)
            self.last_system_state["agent_context"] = self._build_agent_context_debug(
                user_input,
                target_agent_name,
                shared_context,
            )

        
        # 3. 智能体执行
        yield create_event("step", {"name": target_agent_name, "status": "running"})
        
        # 更新 Agent 状态 (最后更新时间)
        await asyncio.to_thread(
            self.user_context_service.update_agent_status,
            user_id,
            agent_type=target_agent_name.replace("_agent", "")
        )
        
        full_response = immediate_reply or ""
        
        try:
            if target_agent_name == "emotional_agent":
                # 情感智能体保持流式特性
                async for event in self._run_emotional_agent(user_input, shared_context):
                    if json.loads(event)["type"] == "token":
                        full_response += json.loads(event)["data"]
                    yield event
            else:
                # 其他智能体
                agent_instance = self._get_agent_instance(target_agent_name)
                result = {}

                if agent_instance is not None and hasattr(agent_instance, "astream_response"):
                    async for event in self._run_specific_agent_stream(
                        target_agent_name,
                        user_input,
                        shared_context,
                    ):
                        parsed_event = json.loads(event)
                        event_type = parsed_event.get("type")
                        if event_type == "token":
                            full_response += parsed_event.get("data", "")
                        elif event_type == "agent_done":
                            result = parsed_event.get("data") or {}
                            continue
                        yield event
                else:
                    result = await self._run_specific_agent(target_agent_name, user_input, shared_context)
                
                content = self.safety_policy.sanitize_response(
                    result.get("content", ""),
                    risk_tier=assessment.risk_tier
                )
                content = self.response_style_guard.clean(content, shared_context)
                if content:
                    for chunk in self._chunk_response_text(content):
                        full_response += chunk
                        yield create_event("token", chunk)
                        await asyncio.sleep(0)
                
                if result.get("action"):
                    yield create_event("action", result["action"])
                music_payload = self._normalize_music_payload(
                    result.get("music_result"),
                    fallback_query=result.get("music_query") or user_input,
                    music_flag=result.get("music"),
                    elder_user_id=user_id,
                    turn_id=turn_id,
                    care_plan=current_care_plan,
                )
                if music_payload is not None:
                    yield create_event("music_payload", music_payload)
                    yield create_event("music", bool(music_payload["trigger_music"]))
                if result.get("sos") is not None:
                    yield create_event("sos", bool(result["sos"]))
                if result.get("risk_level"):
                    result_risk = self._normalize_risk_level(result["risk_level"])
                    if result_risk != "safe":
                        yield create_event("risk", result_risk)
                
                yield create_event("log", f"✅ {target_agent_name} 执行完成")
            
            yield create_event("step", {"name": target_agent_name, "status": "done"})
            
            # 保存到对话记忆
            if full_response:
                await asyncio.to_thread(
                    self.user_context_service.add_memory,
                    user_id,
                    user_input,
                    full_response
                )

        except Exception as e:
            err_msg = str(e)
            logger.error(f"❌ 智能体运行出错: {err_msg}")
            logger.error(traceback.format_exc())
            yield create_event("log", f"❌ 智能体运行出错: {err_msg}")
            
            if "Arrearage" in err_msg or "overdue payment" in err_msg:
                yield create_event(
                    "error",
                    self._structured_error(
                        "upstream_billing_unavailable",
                        source=target_agent_name,
                        retryable=False,
                    ),
                )
            else:
                yield create_event(
                    "error",
                    self._structured_error(
                        "agent_stream_failed",
                        source=target_agent_name,
                    ),
                )

        yield create_event("done", "stop")

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})

    async def _build_shared_context(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        user_id = self.user_context_service.normalize_user_id(context.get("user_id"))
        rag = self.emotional_agent.rag_helper

        async def _music_summary():
            try:
                return await asyncio.to_thread(
                    self.music_library_service.library_summary,
                    user_id,
                    12,
                )
            except Exception as exc:
                logger.warning(f"Music library summary failed: {exc}")
                return []

        async def _photo_summary():
            try:
                return await asyncio.to_thread(
                    self.photo_library_service.summarize_music_photo_context,
                    user_id,
                    8,
                )
            except Exception as exc:
                logger.warning(f"Photo library summary failed: {exc}")
                return ""

        (
            profile,
            recent_history,
            memory_context,
            emotion_trend,
            agent_status,
            music_library_summary,
            photo_library_summary,
        ) = await asyncio.gather(
            asyncio.to_thread(self.user_context_service.get_profile, user_id),
            asyncio.to_thread(self.user_context_service.get_recent_history, user_id, 5),
            asyncio.to_thread(rag.search_comprehensive_memory, user_input, 3),
            asyncio.to_thread(self.user_context_service.get_emotion_trend, user_id),
            asyncio.to_thread(self.user_context_service.get_agent_status, user_id),
            _music_summary(),
            _photo_summary(),
        )
        recent_history = self._sanitize_recent_history(recent_history)

        shared_context = dict(context)
        shared_context["user_id"] = user_id
        shared_context["user_profile"] = profile
        recent_history_text = self._format_recent_history(recent_history)
        shared_context["recent_history"] = recent_history
        shared_context["recent_history_text"] = recent_history_text
        shared_context["semantic_memory_context"] = memory_context
        shared_context["memory_context"] = self._build_memory_context(
            profile=profile,
            recent_history_text=recent_history_text,
            semantic_memory=memory_context,
        )
        shared_context["emotion_trend"] = emotion_trend
        shared_context["agent_status"] = agent_status
        shared_context["care_plan"] = context.get("care_plan") or self.format_care_plan_response(
            self.care_plan_service.get_plan(user_id)
        )
        shared_context["music_library_summary"] = music_library_summary
        shared_context["photo_library_summary"] = photo_library_summary
        return shared_context

    def _build_realtime_chat_messages(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        profile = payload.get("profile") or {}
        recent_history = str(payload.get("recent_history") or "暂无最近对话")
        user_input = str(payload.get("user_input") or "")
        already_said = str(payload.get("already_said_to_elder") or "").strip()
        system_prompt = build_companion_system_prompt(
            phase="realtime_chat",
            stage="companionship",
            risk_tier="safe",
            task=(
                "低延迟直接回复老人。只根据画像、最近对话和当前输入接话；"
                "像小暖本人在持续陪伴，而不是一次性问答。"
            ),
            extra_rules=[
                "输出1到3句，优先回应老人此刻的情绪或请求。",
                "不要编造画像外的事实；不确定就自然承认。",
                "危险、急症、自伤、诈骗相关内容先稳住并提醒不要独自处理。",
                "如果 already_said_to_elder 非空，说明老人已经看到这段话；请直接承接，不要重复或改写它。",
            ],
        )
        return [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    f"老人画像:\n{json.dumps(profile, ensure_ascii=False)[:900]}\n\n"
                    f"记忆线索:\n{str(payload.get('memory_context') or '')[:900]}\n\n"
                    f"最近对话:\n{recent_history[-1000:]}\n\n"
                    f"老人刚刚说:\n{user_input}\n\n"
                    f"already_said_to_elder:\n{already_said}\n\n"
                    "小暖直接回复:"
                ),
            },
        ]

    def _build_realtime_chat_messages(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        profile = payload.get("profile") or {}
        recent_history = str(payload.get("recent_history") or "暂无最近对话")
        user_input = str(payload.get("user_input") or "")
        already_said = str(payload.get("already_said_to_elder") or "").strip()
        system_prompt = build_companion_system_prompt(
            phase="realtime_chat",
            stage="companionship",
            risk_tier="safe",
            task=(
                "低延迟直接回复老人。只根据画像、最近对话、记忆线索和当前输入接话，"
                "像小暖本人在持续陪伴，而不是一次性问答。"
            ),
            extra_rules=[
                "不要固定短答。问候或确认可以一句；倾诉、回忆、聊天可以自然展开到 2 到 5 句。",
                "优先回应老人此刻的情绪或请求，再适当承接过去真实发生过的事情。",
                "可以轻轻提醒当前时段相关的小事，但只在有依据时提醒，不要催。",
                "不要编造画像外的事实；不确定就自然承认。",
                "危险、急症、自伤、诈骗相关内容先稳住并提醒不要独自处理。",
                "如果 already_said_to_elder 非空，说明老人已经看到这段话；请直接承接，不要重复或改写它。",
            ],
        )
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"老人画像:\n{json.dumps(profile, ensure_ascii=False)[:900]}\n\n"
                    f"记忆线索:\n{str(payload.get('memory_context') or '')[:900]}\n\n"
                    f"最近对话:\n{recent_history[-1000:]}\n\n"
                    f"老人刚刚说:\n{user_input}\n\n"
                    f"already_said_to_elder:\n{already_said}\n\n"
                    "小暖直接回复:"
                ),
            },
        ]

    def _build_realtime_chat_messages(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        profile = payload.get("profile") or {}
        recent_history = str(payload.get("recent_history") or "暂无最近对话")
        conversation_summary = str(payload.get("conversation_summary") or "").strip()
        user_input = str(payload.get("user_input") or "")
        already_said = str(payload.get("already_said_to_elder") or "").strip()
        system_prompt = build_companion_system_prompt(
            phase="realtime_chat",
            stage="companionship",
            risk_tier="safe",
            task=(
                "低延迟直接回复老人。参考分层记忆：长期画像、上文摘要、最近 5 轮原文窗口、检索线索。"
                "像小暖本人在持续陪伴，而不是一次性问答。"
            ),
            extra_rules=[
                "最近 5 轮原文窗口优先级最高；上文摘要只用于补足更早发生的事。",
                "如果老人提到过去发生的事情，先在分层记忆里找依据；有依据就自然承接，没有依据就坦诚说不确定。",
                "不要固定短答。问候或确认可以一句；倾诉、回忆、聊天可以自然展开到 2 到 5 句。",
                "可以轻轻提醒当前时段相关的小事，但只在有依据时提醒，不要催。",
                "不要编造画像外的事实；不要把摘要当作老人刚刚说的新话。",
                "如果 already_said_to_elder 非空，说明老人已经看到这段话；请直接承接，不要重复或改写它。",
            ],
        )
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"老人画像:\n{json.dumps(profile, ensure_ascii=False)[:900]}\n\n"
                    f"上文摘要（最近 5 轮之前的压缩记忆，不是新输入）:\n{conversation_summary[:900] or '暂无'}\n\n"
                    f"分层记忆块:\n{str(payload.get('memory_context') or '')[:1600]}\n\n"
                    f"最近 5 轮原文窗口:\n{recent_history[-1200:]}\n\n"
                    f"老人刚刚说:\n{user_input}\n\n"
                    f"already_said_to_elder:\n{already_said}\n\n"
                    "小暖直接回复:"
                ),
            },
        ]

    def _guess_chat_expression(self, user_input: str) -> str:
        text = str(user_input or "")
        if any(marker in text for marker in ("开心", "高兴", "哈哈", "你好", "您好", "嗨")):
            return "happy"
        if any(marker in text for marker in ("闷", "难受", "孤单", "孤独", "伤心", "想哭", "不开心", "怕")):
            return "concerned"
        if any(marker in text for marker in ("救命", "摔倒", "胸口疼", "喘不上气", "不想活")):
            return "concerned"
        return "neutral"

    async def _finalize_realtime_chat_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_input: str,
        assistant_response: str,
        context: Dict[str, Any],
    ) -> None:
        try:
            assessment = self.assessment_service.assess_text(user_input, context)
            assessment_detail = self.format_assessment_response(assessment)
            care_plan = self.care_plan_service.get_plan(user_id)
            final_context = dict(context)
            final_context["risk_assessment"] = assessment_detail
            final_context["care_plan"] = self.format_care_plan_response(care_plan)
            final_context["scene_context"] = self.scene_context_service.build(
                user_input=user_input,
                context=final_context,
                assessment=assessment,
                care_plan=care_plan,
                source=str(context.get("source") or "api_chat"),
            )
            self._schedule_assessment_background_tasks(assessment, context=final_context)
            await asyncio.to_thread(
                self.user_context_service.update_agent_status,
                user_id,
                agent_type="emotional",
            )
            await asyncio.to_thread(
                self.user_context_service.add_memory,
                user_id,
                user_input,
                assistant_response,
            )
            async with self.state_lock:
                if self.last_system_state.get("last_input") == user_input:
                    self.last_system_state["agent_context"] = {
                        **(self.last_system_state.get("agent_context") or {}),
                        "risk_assessment": assessment_detail,
                        "care_plan": final_context["care_plan"],
                        "scene_context": final_context["scene_context"],
                    }
        except Exception as exc:
            logger.error(f"Realtime chat finalization failed: {exc}")

    async def _select_target_agent(
        self,
        user_input: str,
        *,
        context: Dict[str, Any],
        assessment: MentalRiskAssessment,
        care_plan: Any,
        force_agent: Optional[str],
        valid_agents: List[str],
    ) -> str:
        if force_agent in valid_agents:
            return force_agent
        if assessment.risk_tier in ("crisis", "high"):
            return "mental_health_agent"

        guarded_route = self.context_guard.route_override(
            user_input,
            assessment=assessment,
            context=context,
        )
        if guarded_route:
            return guarded_route

        plan_target = getattr(care_plan, "target_agent", None)
        plan_tier = getattr(care_plan, "risk_tier", "safe")
        if plan_target in valid_agents and plan_tier in {"low", "medium", "high", "crisis"}:
            return plan_target

        return await self.router.route(user_input, context=context)

    def _format_recent_history(self, recent_history: List[Dict[str, Any]]) -> str:
        if not recent_history:
            return "暂无最近对话"

        lines: List[str] = []
        for item in recent_history[-6:]:
            role = "老人" if item.get("role") == "user" else "小暖"
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else "暂无最近对话"

    def _sanitize_recent_history(self, recent_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        skip_next_assistant = False
        for item in recent_history:
            content = str(item.get("content", "")).strip()
            if content.startswith("[系统判定老人沉默"):
                skip_next_assistant = True
                continue
            if skip_next_assistant and item.get("role") == "assistant":
                skip_next_assistant = False
                continue
            skip_next_assistant = False
            cleaned.append(item)
        return cleaned

    def _build_context_snapshot(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return self.user_context_service.build_context_snapshot(
            context.get("user_id"),
            context
        )

    def _build_agent_context_debug(
        self,
        user_input: str,
        target_agent_name: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the text bundle that is handed to agents before model calls."""
        return {
            "turn_id": context.get("turn_id"),
            "user_id": context.get("user_id"),
            "user_input": user_input,
            "target_agent": target_agent_name,
            "immediate_reply": context.get("immediate_reply", ""),
            "memory_context": context.get("memory_context", ""),
            "semantic_memory_context": context.get("semantic_memory_context", ""),
            "recent_history_text": context.get("recent_history_text", ""),
            "scene_context": context.get("scene_context", {}),
            "risk_assessment": context.get("risk_assessment", {}),
            "care_plan": context.get("care_plan", {}),
            "music_library_summary": context.get("music_library_summary", []),
            "photo_library_summary": context.get("photo_library_summary", ""),
            "raw_context_keys": sorted(str(key) for key in context.keys()),
        }

    def _json_safe_debug_value(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_debug_value(item, depth + 1)
                for key, item in list(value.items())[:40]
            }
        if isinstance(value, (list, tuple)):
            return [self._json_safe_debug_value(item, depth + 1) for item in list(value)[:40]]
        if hasattr(value, "model_dump"):
            try:
                return self._json_safe_debug_value(value.model_dump(mode="json"), depth + 1)
            except Exception:
                pass
        if hasattr(value, "dict"):
            try:
                return self._json_safe_debug_value(value.dict(), depth + 1)
            except Exception:
                pass
        content = getattr(value, "content", None)
        if content is not None:
            return {
                "type": value.__class__.__name__,
                "content": self._json_safe_debug_value(content, depth + 1),
            }
        return str(value)

    async def _record_llm_input_debug(self, source: str, payload: Any) -> None:
        safe_payload = self._json_safe_debug_value(payload)
        async with self.state_lock:
            llm_inputs = self.last_system_state.setdefault("llm_inputs", [])
            llm_inputs.append({
                "source": source,
                "payload": safe_payload,
            })
            if len(llm_inputs) > 10:
                del llm_inputs[:-10]

    async def _build_proactive_context(self, user_id: str) -> Dict[str, Any]:
        user_id = self.user_context_service.normalize_user_id(user_id)
        profile, recent_history, emotion_trend, agent_status, care_plan = await asyncio.gather(
            asyncio.to_thread(self.user_context_service.get_profile, user_id),
            asyncio.to_thread(self.user_context_service.get_recent_history, user_id, 8),
            asyncio.to_thread(self.user_context_service.get_emotion_trend, user_id),
            asyncio.to_thread(self.user_context_service.get_agent_status, user_id),
            asyncio.to_thread(self.care_plan_service.get_plan, user_id),
        )
        recent_history = self._sanitize_recent_history(recent_history)
        recent_history_text = self._format_recent_history(recent_history)
        context: Dict[str, Any] = {
            "user_id": user_id,
            "turn_id": f"proactive_{uuid.uuid4().hex}",
            "source": "proactive",
            "user_profile": profile,
            "recent_history": recent_history,
            "recent_history_text": recent_history_text,
            "emotion_trend": emotion_trend,
            "agent_status": agent_status,
            "care_plan": self.format_care_plan_response(care_plan),
            "memory_context": self._build_memory_context(
                profile=profile,
                recent_history_text=recent_history_text,
                semantic_memory="",
            ),
            "semantic_memory_context": "",
        }
        try:
            context["music_library_summary"] = await asyncio.to_thread(
                self.music_library_service.library_summary,
                user_id,
                12,
            )
        except Exception as exc:
            logger.warning(f"Proactive music library summary failed: {exc}")
            context["music_library_summary"] = []
        try:
            context["photo_library_summary"] = await asyncio.to_thread(
                self.photo_library_service.summarize_music_photo_context,
                user_id,
                8,
            )
        except Exception as exc:
            logger.warning(f"Proactive photo library summary failed: {exc}")
            context["photo_library_summary"] = ""
        context = self.context_guard.sanitize_context(context)
        context["scene_context"] = self.scene_context_service.build(
            user_input="",
            context=context,
            care_plan=care_plan,
            source="proactive",
        )
        return context

    def _build_memory_context(
        self,
        *,
        profile: Dict[str, Any],
        conversation_summary: str = "",
        recent_history_text: str,
        semantic_memory: str,
    ) -> str:
        parts: List[str] = []
        profile_facts: List[str] = []

        display_name = self.user_context_service.display_name(profile, fallback="")
        if display_name:
            profile_facts.append(f"name: {display_name}")
        for key in ("health_condition", "family_members", "preferences", "medications"):
            value = profile.get(key) if isinstance(profile, dict) else None
            if value:
                profile_facts.append(f"{key}: {value}")
        if profile_facts:
            parts.append("[profile]\n" + "\n".join(profile_facts))

        summary_text = str(conversation_summary or "").strip()
        if summary_text:
            parts.append("[conversation_summary_before_recent_window]\n" + summary_text)

        recent_text = str(recent_history_text or "").strip()
        if recent_text:
            parts.append("[recent_dialogue_window_last_5_turns]\n" + recent_text)

        semantic_text = str(semantic_memory or "").strip()
        if semantic_text:
            parts.append("[retrieved_memory]\n" + semantic_text)

        if not parts:
            return ""
        body = "\n\n".join(parts)[:2400]
        return (
            "<memory-context>\n"
            "[System note: The following is layered recalled context, NOT new user input. "
            "Use it to preserve continuity. Do not quote internal section names.]\n\n"
            f"{body}\n"
            "</memory-context>"
        )

    def _latest_recent_user_text(self, context: Dict[str, Any], exclude_text: str = "") -> str:
        exclude = str(exclude_text or "").strip()
        for item in reversed(context.get("recent_history") or []):
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if content and content != exclude:
                return content
        return ""

    async def _absorb_background_task_result(self, task: asyncio.Task) -> Any:
        return await task

    async def _get_profile_history_quick(
        self,
        user_id: str,
        *,
        limit: int,
        timeout: float,
        label_prefix: str,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        profile_task = asyncio.create_task(
            asyncio.to_thread(self.user_context_service.get_profile, user_id)
        )
        history_task = asyncio.create_task(
            asyncio.to_thread(self.user_context_service.get_recent_history, user_id, limit)
        )
        tasks = {profile_task, history_task}
        await asyncio.wait(tasks, timeout=max(0.0, timeout))

        profile: Dict[str, Any] = {}
        recent_history: List[Dict[str, Any]] = []
        task_specs = (
            (profile_task, "profile"),
            (history_task, "recent_history"),
        )
        for task, name in task_specs:
            if task.done():
                try:
                    result = task.result()
                    if name == "profile" and isinstance(result, dict):
                        profile = result
                    elif name == "recent_history" and isinstance(result, list):
                        recent_history = result
                except Exception as exc:
                    logger.warning(f"{label_prefix} {name} read failed: {exc}")
            else:
                self._schedule_background_task(
                    self._absorb_background_task_result(task),
                    label=f"{label_prefix}_{name}_prefetch",
                    metadata={"elder_user_id": user_id},
                )

        return profile, recent_history

    async def _get_profile_layered_context_quick(
        self,
        user_id: str,
        *,
        timeout: float,
        label_prefix: str,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        profile_task = asyncio.create_task(
            asyncio.to_thread(self.user_context_service.get_profile, user_id)
        )
        layers_task = asyncio.create_task(
            asyncio.to_thread(
                self.user_context_service.get_layered_chat_context,
                user_id,
                recent_turns=5,
            )
        )
        tasks = {profile_task, layers_task}
        await asyncio.wait(tasks, timeout=max(0.0, timeout))

        profile: Dict[str, Any] = {}
        history_layers: Dict[str, Any] = {
            "summary": "",
            "recent_window": [],
            "recent_window_text": "",
            "overflow_count": 0,
            "recent_turns": 5,
        }
        task_specs = (
            (profile_task, "profile"),
            (layers_task, "history_layers"),
        )
        for task, name in task_specs:
            if task.done():
                try:
                    result = task.result()
                    if name == "profile" and isinstance(result, dict):
                        profile = result
                    elif name == "history_layers" and isinstance(result, dict):
                        history_layers = result
                except Exception as exc:
                    logger.warning(f"{label_prefix} {name} read failed: {exc}")
            else:
                self._schedule_background_task(
                    self._absorb_background_task_result(task),
                    label=f"{label_prefix}_{name}_prefetch",
                    metadata={"elder_user_id": user_id},
                )

        return profile, history_layers

    def _build_quick_assessment_detail(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = str(user_input or "")
        flags = {
            "self_harm_ideation": any(marker in text for marker in ("不想活", "去死", "死了算了", "活着没意思")),
            "explicit_death_wish": any(marker in text for marker in ("我想去死", "死了算了", "不想活了")),
            "medical_emergency": any(marker in text for marker in ("救命", "摔倒", "胸口疼", "喘不上气", "呼吸困难", "起不来")),
            "fraud_risk": any(marker in text for marker in ("转账", "验证码", "银行卡", "中奖", "安全账户")),
            "manic_activation": any(marker in text for marker in ("一夜没睡也不困", "停不下来", "好多计划")),
        }
        if flags["self_harm_ideation"] or flags["explicit_death_wish"]:
            tier = "crisis"
        elif flags["medical_emergency"]:
            tier = "high"
        elif flags["fraud_risk"] or flags["manic_activation"]:
            tier = "medium"
        elif any(marker in text for marker in ("心慌", "害怕", "睡不着", "孤单", "孤独", "难受", "不开心")):
            tier = "low"
        else:
            tier = "safe"
        return {
            "risk_tier": tier,
            "tier": tier,
            "primary_state": "quick_preview",
            "safety_flags": flags,
            "confidence": 0.35,
            "source": "quick_assessment_preview",
        }

    def _build_quick_ack(self, user_input: str, assessment_detail: Optional[Dict[str, Any]] = None) -> str:
        text = self._normalize_ack_subject(user_input)
        if not text:
            return ""
        return ""

    def _normalize_ack_subject(self, user_input: str, max_chars: int = 24) -> str:
        text = str(user_input or "").strip()
        text = re.sub(r"\s+", "，", text)
        text = text.strip(" \t\r\n，。！？!?；;、")
        if not text:
            return ""
        sentence_match = re.split(r"[。！？!?；;\n]", text, maxsplit=1)
        subject = sentence_match[0].strip("，,、 ")
        if not subject:
            subject = text
        if len(subject) > max_chars:
            subject = subject[:max_chars].rstrip("，,、 ") + "…"
        return subject

    async def _stream_llm_first_response(
        self,
        *,
        user_input: str,
        user_id: str,
        assessment_detail: Dict[str, Any],
        already_said_to_elder: str = "",
    ):
        """Generate a real LLM first response from only profile and recent history."""
        try:
            profile, recent_history = await self._get_profile_history_quick(
                user_id,
                limit=6,
                timeout=0.20,
                label_prefix="first_response",
            )
            recent_history = self._sanitize_recent_history(recent_history)
            recent_history_text = self._format_recent_history(recent_history)
            prompt_payload = {
                "profile": profile,
                "recent_history": recent_history_text,
                "user_input": user_input,
                "risk_tier": assessment_detail.get("risk_tier"),
                "already_said_to_elder": already_said_to_elder,
            }
            await self._record_llm_input_debug("first_response_llm", prompt_payload)
            messages = [
                {
                    "role": "system",
                    "content": build_companion_system_prompt(
                        phase="first_response",
                        stage="companionship",
                        risk_tier=assessment_detail.get("risk_tier") or "safe",
                        task=(
                            "在老人已经看到 already_said_to_elder 的前提下继续接话。"
                            "这句话要像小暖本人顺着上一句往下陪，而不是重新开场。"
                        ),
                        extra_rules=[
                            "18到35个汉字；只输出一句。",
                            "不要重复、改写或解释 already_said_to_elder。",
                            "不要解释系统、路由、风险、工具或后台流程。",
                            "危险、急症、自伤、诈骗转账时先稳住对方。",
                        ],
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"画像:{json.dumps(profile, ensure_ascii=False)[:600]}\n"
                        f"最近:{recent_history_text[-600:]}\n"
                        f"老人:{user_input}\n"
                        f"already_said_to_elder:{already_said_to_elder}\n"
                        "小暖继续说:"
                    ),
                },
            ]
            emitted = ""
            stream = await self.first_response_client.chat.completions.create(
                model=Config.FIRST_RESPONSE_MODEL,
                messages=messages,
                temperature=0.35,
                max_tokens=48,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                text = self._strip_parenthetical_text(
                    getattr(delta, "content", None) if delta else ""
                )
                if not text:
                    continue
                emitted += text
                if len(emitted) > 80:
                    text = text[: max(0, 80 - (len(emitted) - len(text)))]
                if text:
                    yield text
                if len(emitted) >= 28 and any(mark in emitted for mark in "。！？!?"):
                    break
        except Exception as exc:
            logger.warning(f"LLM first response failed: {exc}")
            return

    def _should_use_fast_companion_path(
        self,
        user_input: str,
        assessment_detail: Dict[str, Any],
        context: Dict[str, Any],
    ) -> bool:
        if context.get("force_agent"):
            return False
        risk_tier = str(assessment_detail.get("risk_tier") or "").strip().lower()
        if risk_tier not in {"safe", "low"}:
            return False
        flags = assessment_detail.get("safety_flags") or {}
        if any(flags.get(key) for key in ("self_harm_ideation", "explicit_death_wish", "medical_emergency", "fraud_risk")):
            return False
        text = str(user_input or "").strip()
        full_agent_markers = (
            "救命", "摔倒", "起不来", "胸口疼", "喘不上气", "呼吸困难",
            "吃药", "药", "血压", "血糖", "发烧", "头疼", "不舒服",
            "转账", "验证码", "银行卡", "中奖", "安全账户",
            "照片", "相册", "看看照片", "孙女照片", "孙子照片",
            "音乐", "放歌", "听歌", "歌曲", "唱片",
            "提醒我", "记录一下", "记一下", "几点", "天气",
        )
        if any(marker in text for marker in full_agent_markers):
            return False
        return len(text) <= 80

    def _fast_companion_expression(self, user_input: str, assessment_detail: Dict[str, Any]) -> str:
        text = str(user_input or "")
        risk_tier = str(assessment_detail.get("risk_tier") or "").strip().lower()
        if risk_tier == "low" or any(marker in text for marker in ("闷", "难受", "孤单", "想聊", "不开心")):
            return "concerned"
        if any(marker in text for marker in ("开心", "高兴", "哈哈", "你好", "你哈")):
            return "happy"
        return "neutral"

    async def _stream_fast_companion_response(
        self,
        *,
        user_input: str,
        user_id: str,
        assessment_detail: Dict[str, Any],
    ):
        profile, recent_history = await self._get_profile_history_quick(
            user_id,
            limit=6,
            timeout=0.25,
            label_prefix="fast_companion",
        )
        recent_history = self._sanitize_recent_history(recent_history)
        recent_history_text = self._format_recent_history(recent_history)
        prompt_payload = {
            "profile": profile,
            "recent_history": recent_history_text,
            "user_input": user_input,
            "risk_tier": assessment_detail.get("risk_tier"),
            "mode": "fast_companion",
        }
        await self._record_llm_input_debug("fast_companion", prompt_payload)
        messages = [
            {
                "role": "system",
                "content": build_companion_system_prompt(
                    phase="fast_companion",
                    stage="companionship",
                    risk_tier=assessment_detail.get("risk_tier") or "safe",
                    task="普通低风险陪伴回复。保持小暖的人格连续性，轻轻接住老人这句话。",
                    extra_rules=[
                        "输出1到2句，必要时只问一个很轻的问题。",
                        "不要提系统、风险、模型或后台流程。",
                        "不要复述大段画像；只在有帮助时使用记忆线索。",
                    ],
                ),
            },
            {
                "role": "user",
                "content": (
                    f"画像:{json.dumps(profile, ensure_ascii=False)[:800]}\n"
                    f"最近:{recent_history_text[-800:]}\n"
                    f"老人:{user_input}\n"
                    "小暖回复:"
                ),
            },
        ]
        try:
            stream = await self.first_response_client.chat.completions.create(
                model=Config.FIRST_RESPONSE_MODEL,
                messages=messages,
                temperature=0.45,
                max_tokens=96,
                stream=True,
            )
            emitted = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                text = self._strip_parenthetical_text(
                    getattr(delta, "content", None) if delta else ""
                )
                if not text:
                    continue
                emitted += text
                yield text
                if len(emitted) >= 60 and any(mark in emitted for mark in "。！？!?"):
                    break
        except Exception as exc:
            logger.warning(f"fast_companion LLM failed: {exc}")
            return

    async def _stream_fast_companion_response(
        self,
        *,
        user_input: str,
        user_id: str,
        assessment_detail: Dict[str, Any],
    ):
        profile, recent_history = await self._get_profile_history_quick(
            user_id,
            limit=8,
            timeout=0.25,
            label_prefix="fast_companion",
        )
        recent_history = self._sanitize_recent_history(recent_history)
        recent_history_text = self._format_recent_history(recent_history)
        prompt_payload = {
            "profile": profile,
            "recent_history": recent_history_text,
            "user_input": user_input,
            "risk_tier": assessment_detail.get("risk_tier"),
            "mode": "fast_companion",
        }
        await self._record_llm_input_debug("fast_companion", prompt_payload)
        messages = [
            {
                "role": "system",
                "content": build_companion_system_prompt(
                    phase="fast_companion",
                    stage="companionship",
                    risk_tier=assessment_detail.get("risk_tier") or "safe",
                    task=(
                        "普通低风险陪伴回复。保持小暖的人格连续性，接住老人这句话；"
                        "如果老人愿意聊、回忆或表达情绪，可以自然多说几句。"
                    ),
                    extra_rules=[
                        "不要固定 1 到 2 句；根据老人意图决定长短，通常 1 到 5 句。",
                        "真实使用最近对话和记忆线索，但不要朗读画像。",
                        "可以适当做当前时段提醒；没有依据时不要提醒。",
                        "不要提系统、风险、模型或后台流程。",
                    ],
                ),
            },
            {
                "role": "user",
                "content": (
                    f"画像:{json.dumps(profile, ensure_ascii=False)[:800]}\n"
                    f"最近对话:{recent_history_text[-1000:]}\n"
                    f"老人:{user_input}\n"
                    "小暖回复:"
                ),
            },
        ]
        try:
            stream = await self.first_response_client.chat.completions.create(
                model=Config.FIRST_RESPONSE_MODEL,
                messages=messages,
                temperature=0.5,
                max_tokens=180,
                stream=True,
            )
            emitted = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                text = self._strip_parenthetical_text(
                    getattr(delta, "content", None) if delta else ""
                )
                if not text:
                    continue
                emitted += text
                yield text
                if len(emitted) >= 140 and any(mark in emitted for mark in "。！？!?"):
                    break
        except Exception as exc:
            logger.warning(f"fast_companion LLM failed: {exc}")
            return

    def _get_agent_instance(self, agent_name: str) -> Any:
        agent_map = {
            "medical_agent": self.medical_agent,
            "daily_life_agent": self.daily_life_agent,
            "interest_agent": self.interest_agent,
            "mental_health_agent": self.mental_health_agent,
            "antifraud_agent": self.antifraud_agent,
        }
        return agent_map.get(agent_name)

    async def _run_specific_agent_stream(
        self,
        agent_name: str,
        input_text: str,
        context: Dict[str, Any],
    ):
        agent = self._get_agent_instance(agent_name)
        stream_fn = getattr(agent, "astream_response", None)
        if not callable(stream_fn):
            return

        result: Dict[str, Any] = {}
        async for item in stream_fn(input_text, context):
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            data = item.get("data")
            if item_type == "token":
                yield create_event("token", data or "")
            elif item_type == "done":
                result = dict(data or {})
                result["content"] = ""
            elif item_type in {"action", "risk", "sos", "expression", "music", "music_payload"}:
                yield create_event(item_type, data)
        yield create_event("agent_done", result)

    async def _run_specific_agent(self, agent_name: str, input_text: str, context: Dict) -> Dict:
        """运行非流式智能体并标准化输出"""
        if agent_name == "medical_agent":
            return await self.medical_agent.arun(input_text, context)
        elif agent_name == "daily_life_agent":
            return await self.daily_life_agent.arun(input_text, context)
        elif agent_name == "interest_agent":
            return await self.interest_agent.arun(input_text, context)
        elif agent_name == "mental_health_agent":
            return await self.mental_health_agent.arun(input_text, context)
        elif agent_name == "antifraud_agent":
            res = await self.antifraud_agent.arun(input_text, context)
            intervention = res.get("intervention", {})
            analysis = res.get("analysis", {})
            
            content = intervention.get("action_to_senior", "")
            if not content:
                content = await self._generate_dynamic_emotional_fallback(
                    input_text,
                    context,
                    emotional_args={
                        "source_agent": agent_name,
                        "analysis": analysis,
                        "intervention": intervention,
                    },
                )
            risk = self._normalize_risk_level(analysis.get("risk_level", "low"))
            return {
                "content": content,
                "action": "warning" if risk != "safe" else "nod",
                "risk_level": risk,
                "family_message": intervention.get("action_to_family"),
                "community_message": intervention.get("action_to_community"),
            }
        return {
            "content": await self._generate_dynamic_emotional_fallback(
                input_text,
                context,
                emotional_args={"source_agent": agent_name, "reason": "no_matching_agent"},
            ),
            "action": "nod",
            "risk_level": "low",
        }

    def _chunk_response_text(self, text: str, chunk_size: int = 18) -> List[str]:
        """Split non-streaming agent output into small SSE token chunks."""
        text = text or ""
        chunks: List[str] = []
        buffer = ""
        for char in text:
            buffer += char
            if len(buffer) >= chunk_size or char in "，。！？；,.!?;":
                chunks.append(buffer)
                buffer = ""
        if buffer:
            chunks.append(buffer)
        return chunks

    def _normalize_risk_level(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if "crisis" in text or "危机" in text:
            return "crisis"
        if "high" in text or "高" in text or "紧急" in text:
            return "high"
        if "medium" in text or "中" in text or "确认" in text:
            return "medium"
        if "low" in text or "低" in text or "疑似" in text:
            return "low"
        if "safe" in text or "安全" in text:
            return "safe"
        return "low"

    async def _run_emotional_agent(self, user_input: str, context: Dict[str, Any]):
        """Handle emotional agent streaming output and tool events."""
        parentheses_depth = 0
        streamed_text = ""
        emitted_text = ""
        raw_risk_tier = (context.get("risk_assessment") or {}).get("risk_tier")
        risk_tier = raw_risk_tier.strip().lower() if isinstance(raw_risk_tier, str) else raw_risk_tier
        pending_stream_buffer = ""
        stream_flush_enabled = risk_tier != "crisis"

        def pop_completed_stream_segments(force: bool = False) -> List[str]:
            nonlocal pending_stream_buffer

            completed_segments: List[str] = []
            boundary_chars = "\u3002\uff01\uff1f!?\uff1b;\n"

            while pending_stream_buffer:
                boundary_indices = [
                    pending_stream_buffer.find(char)
                    for char in boundary_chars
                    if pending_stream_buffer.find(char) >= 0
                ]
                if not boundary_indices:
                    break

                boundary_index = min(boundary_indices)
                raw_segment = pending_stream_buffer[:boundary_index + 1]
                pending_stream_buffer = pending_stream_buffer[boundary_index + 1:]

                # Completed stream segments are sanitized before being sent to the UI.
                # We intentionally do not apply the crisis-tier prefix per sentence;
                # crisis turns remain fully buffered and receive the prefix once.
                safe_segment = self.safety_policy.sanitize_response(
                    raw_segment,
                    risk_tier=None,
                )
                if safe_segment:
                    safe_segment = self.response_style_guard.clean(safe_segment, context)
                if safe_segment:
                    completed_segments.append(safe_segment)

            if force and pending_stream_buffer:
                safe_segment = self.safety_policy.sanitize_response(
                    pending_stream_buffer,
                    risk_tier=risk_tier,
                )
                pending_stream_buffer = ""
                if safe_segment:
                    safe_segment = self.response_style_guard.clean(safe_segment, context)
                if safe_segment:
                    completed_segments.append(safe_segment)

            return completed_segments

        async for event in self.emotional_agent.astream_run(
            input_text=user_input,
            voice_text=context.get("audio_transcript"),
            voice_emotion=context.get("voice_emotion"),
            visual_emotion=context.get("visual_analysis"),
            session_context=context
        ):
            try:
                kind = event["event"]

                if kind in {"on_chat_model_start", "on_llm_start"}:
                    await self._record_llm_input_debug(
                        event.get("name") or "chat_model",
                        event.get("data", {}),
                    )

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    chunk_content = self._extract_message_text(
                        getattr(chunk, "content", None) if chunk else None
                    )
                    if chunk_content:
                        filtered_content = ""
                        for char in chunk_content:
                            if char in ["(", "（"]:
                                parentheses_depth += 1
                                continue
                            if char in [")", "）"]:
                                if parentheses_depth > 0:
                                    parentheses_depth -= 1
                                continue
                            if parentheses_depth > 0:
                                continue
                            filtered_content += char

                        if filtered_content:
                            streamed_text += filtered_content
                            if stream_flush_enabled:
                                pending_stream_buffer += filtered_content
                                for safe_segment in pop_completed_stream_segments():
                                    emitted_text += safe_segment
                                    yield create_event("token", safe_segment)

                elif kind == "on_tool_start":
                    tool_name = event.get("name")
                    if tool_name and tool_name != "EmotionalStateUpdate":
                        tool_input = event.get("data", {}).get("input", {})
                        async with self.state_lock:
                            self.last_system_state["tool_calls"].append({
                                "tool": tool_name,
                                "input": tool_input,
                                "output": None
                            })

                elif kind == "on_tool_end":
                    event_name = event.get("name")
                    if event_name and event_name != "EmotionalStateUpdate":
                        parsed_output = self._parse_tool_output(event.get("data", {}).get("output", ""))
                        async with self.state_lock:
                            for idx in range(len(self.last_system_state["tool_calls"]) - 1, -1, -1):
                                item = self.last_system_state["tool_calls"][idx]
                                if item.get("tool") == event_name and item.get("output") is None:
                                    item["output"] = parsed_output
                                    break

                    if event_name == "search_family_photos":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            status = output_data.get("status")
                            photos = output_data.get("photos", [])
                            if status == "success" and photos:
                                logger.info(f"Found {len(photos)} photos from emotional agent")
                                yield create_event("photos", photos)
                            else:
                                yield create_event(
                                    "photos_result",
                                    {
                                        "status": status or "unknown",
                                        "message": output_data.get("message", ""),
                                        "photos": photos or []
                                    }
                                )
                        except Exception:
                            pass
                    elif event_name == "emergency_contact":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            if output_data.get("trigger_sos") is True:
                                yield create_event("sos", True)
                        except Exception:
                            pass
                    elif event_name == "play_music":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            music_payload = self._normalize_music_payload(
                                output_data,
                                music_flag=True,
                                elder_user_id=context.get("user_id"),
                                turn_id=context.get("turn_id"),
                                care_plan=context.get("care_plan"),
                            )
                            if music_payload and music_payload.get("trigger_music") is True:
                                yield create_event("music_payload", music_payload)
                                yield create_event("music", True)
                        except Exception:
                            pass

                elif kind == "on_chat_model_end":
                    metadata = event.get("metadata")
                    if isinstance(metadata, dict) and metadata.get("langgraph_node") == "agent":
                        output = event.get("data", {}).get("output")
                        final_content = self._strip_parenthetical_text(
                            self._extract_message_text(
                                getattr(output, "content", None) if output else None
                            )
                        )
                        if final_content:
                            safe_final_content = self.safety_policy.sanitize_response(
                                final_content,
                                risk_tier=risk_tier,
                            )
                            remaining_text = ""
                            if not emitted_text:
                                remaining_text = safe_final_content
                            elif safe_final_content.startswith(emitted_text):
                                remaining_text = safe_final_content[len(emitted_text):]

                            if remaining_text:
                                emitted_text += remaining_text
                                pending_stream_buffer = ""
                                yield create_event("token", remaining_text)
                            elif pending_stream_buffer:
                                for safe_segment in pop_completed_stream_segments(force=True):
                                    emitted_text += safe_segment
                                    yield create_event("token", safe_segment)

                elif kind == "on_chain_end" and event.get("name") == "agent":
                    output = event.get("data", {}).get("output")
                    emotional_args = self._extract_emotional_update_args(output)
                    if emotional_args:
                        if "expression" in emotional_args:
                            yield create_event("expression", emotional_args["expression"])
                        if "action" in emotional_args:
                            yield create_event("action", emotional_args["action"])
                        if "risk_level" in emotional_args:
                            yield create_event("risk", emotional_args["risk_level"])
                        if "profile_update" in emotional_args and emotional_args["profile_update"]:
                            await asyncio.to_thread(
                                self.user_context_service.update_profile,
                                context.get("user_id"),
                                emotional_args["profile_update"]
                            )
                        if "risk_level" in emotional_args and "expression" in emotional_args:
                            await asyncio.to_thread(
                                self.user_context_service.log_emotion,
                                context.get("user_id"),
                                emotional_args["expression"],
                                emotional_args["risk_level"]
                            )

                    if not emitted_text and streamed_text:
                        safe_streamed_text = self.safety_policy.sanitize_response(
                            streamed_text,
                            risk_tier=risk_tier,
                        )
                        if safe_streamed_text:
                            emitted_text += safe_streamed_text
                            yield create_event("token", safe_streamed_text)
                    elif pending_stream_buffer:
                        for safe_segment in pop_completed_stream_segments(force=True):
                            emitted_text += safe_segment
                            yield create_event("token", safe_segment)

                    if not emitted_text:
                        fallback_reply = await self._generate_dynamic_emotional_fallback(
                            user_input=user_input,
                            context=context,
                            emotional_args=emotional_args
                        )
                        if fallback_reply:
                            safe_fallback_reply = self.safety_policy.sanitize_response(
                                fallback_reply,
                                risk_tier=risk_tier,
                            )
                            emitted_text += safe_fallback_reply
                            yield create_event("token", safe_fallback_reply)
            except Exception as inner_e:
                logger.error(f"Emotional agent streaming handling failed: {inner_e}")
                logger.error(traceback.format_exc())
                yield create_event("log", f"Emotional agent stream parsing failed: {str(inner_e)}")

        logger.info("Emotional response streaming completed")
        yield create_event("log", "情感回复流式传输完成")

    def _extract_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
            return "".join(text_parts)
        return ""

    def _strip_parenthetical_text(self, text: str) -> str:
        if not text:
            return ""

        result: List[str] = []
        depth = 0
        for char in text:
            if char in ['(', '（']:
                depth += 1
                continue
            if char in [')', '）']:
                if depth > 0:
                    depth -= 1
                continue
            if depth == 0:
                result.append(char)

        return "".join(result)

    def _extract_emotional_update_args(self, output: Any) -> Dict[str, Any]:
        messages = []
        if isinstance(output, dict):
            messages = output.get("messages") or []
        elif output is not None:
            messages = [output]

        for message in reversed(messages):
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                args = tool_call.get("args", {})
                if not isinstance(args, dict):
                    continue
                if tool_call.get("name") == "EmotionalStateUpdate":
                    if args:
                        return args
                    continue
                if {"expression", "action", "risk_level"} & set(args.keys()):
                    return args
        return {}

    async def _generate_dynamic_emotional_fallback(
        self,
        user_input: str,
        context: Dict[str, Any],
        emotional_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile = context.get("user_profile") or {}
        scene = context.get("scene_context") or {}
        care_plan = scene.get("care_plan") if isinstance(scene, dict) else {}
        current_scene = scene.get("current_scene") if isinstance(scene, dict) else {}
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="fallback_reply",
                    stage=(care_plan or {}).get("current_stage") or "companionship",
                    risk_tier=(current_scene or {}).get("risk_tier") or "safe",
                    task="生成最后兜底的老人端回复。只输出正文，不能提系统失败。",
                    extra_rules=[
                        "不要使用固定套话或模板开场。",
                        "先回应当前这句话，再轻轻承接最近对话或记忆。",
                        "如果 risk_level 是 high 或 crisis，优先安全稳定。",
                        "只输出1到2句中文回复。",
                    ],
                ),
            ),
            (
                "human",
                "User profile: {profile}\nRecent dialogue: {recent_history_text}\nMemory: {memory_context}\nScene context: {scene_context}\nCurrent utterance: {user_input}\nEmotional metadata: {emotional_args}",
            ),
        ])
        chain = prompt | self.emotional_agent.llm
        try:
            response = await chain.ainvoke({
                "profile": json.dumps(profile, ensure_ascii=False),
                "recent_history_text": context.get("recent_history_text") or "",
                "memory_context": context.get("memory_context") or "",
                "scene_context": json.dumps(context.get("scene_context") or {}, ensure_ascii=False),
                "user_input": user_input,
                "emotional_args": json.dumps(emotional_args or {}, ensure_ascii=False),
            })
            return self._strip_parenthetical_text(getattr(response, "content", "") or "").strip()
        except Exception as exc:
            logger.warning(f"Dynamic emotional fallback failed: {exc}")
            return ""

    def _parse_tool_output(self, output_str: Any) -> Dict[str, Any]:
        if isinstance(output_str, list) and output_str:
            output_str = output_str[-1]
        if isinstance(output_str, dict):
            return output_str
        if hasattr(output_str, "content") and isinstance(getattr(output_str, "content", None), str):
            output_str = output_str.content
        if not output_str:
            return {}
        try:
            return json.loads(output_str)
        except Exception:
            return {}

    def _normalize_music_payload(
        self,
        payload: Optional[Dict[str, Any]],
        fallback_query: str = "",
        music_flag: Optional[bool] = None,
        *,
        elder_user_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        care_plan: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        if payload is None and music_flag is None:
            return None

        payload = payload or {}
        trigger_music = bool(payload.get("trigger_music", music_flag))
        normalized_query = payload.get("query") or fallback_query

        normalized_payload = {
            "status": payload.get("status", "success" if trigger_music else "noop"),
            "intent": payload.get("intent", "play_music"),
            "trigger_music": trigger_music,
            "query": normalized_query,
            "source": payload.get("source", "agent")
        }
        if not trigger_music:
            return normalized_payload

        music_name = (
            payload.get("music_name")
            or payload.get("song_name")
            or payload.get("title")
            or normalized_query
        )
        post_reply = payload.get("post_reply") or ""
        normalized_payload.update(
            {
                "music_name": music_name,
                "post_reply": post_reply,
            }
        )
        for key in ("music_id", "playable_ref", "music_description", "library_match"):
            if payload.get(key) is not None:
                normalized_payload[key] = payload.get(key)

        music_service = getattr(self, "music_library_service", None)
        if elder_user_id and music_service is not None and not normalized_payload.get("music_id"):
            try:
                match = music_service.match_song(elder_user_id, music_name or normalized_query, limit=1)
                song = match.get("song") if isinstance(match, dict) else None
                if song:
                    music_name = song.get("name") or music_name
                    normalized_payload.update(
                        {
                            "source": "music_library",
                            "music_id": song.get("music_id"),
                            "music_name": music_name,
                            "playable_ref": song.get("playable_ref"),
                            "music_description": song.get("description", ""),
                            "library_match": match,
                        }
                    )
            except Exception as exc:
                logger.warning(f"Music library match failed: {exc}")

        if elder_user_id and hasattr(self, "action_session_service"):
            care_plan_data = self._model_to_dict(care_plan) if care_plan is not None else {}
            session = self.action_session_service.create_session(
                elder_user_id,
                "music",
                payload={
                    "turn_id": turn_id,
                    "query": normalized_query,
                    "music_name": normalized_payload.get("music_name", music_name),
                    "music_id": normalized_payload.get("music_id"),
                    "playable_ref": normalized_payload.get("playable_ref"),
                    "music_description": normalized_payload.get("music_description", ""),
                    "source": normalized_payload["source"],
                    "risk_tier": care_plan_data.get("risk_tier", "safe"),
                    "stage": care_plan_data.get("current_stage", ""),
                    "goal": care_plan_data.get("next_turn_goal", ""),
                },
                post_reply=post_reply,
            )
            normalized_payload.update(
                {
                    "action_id": session.action_id,
                    "action_type": session.action_type,
                }
            )

        return normalized_payload

    def complete_action(self, request: Any) -> Dict[str, Any]:
        return self.action_session_service.complete_action(request)

    def list_pending_actions(
        self,
        elder_user_id: str = "user_001",
        *,
        target_channel: str = "frontend",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.action_session_service.list_pending_actions(
            elder_user_id,
            target_channel=target_channel,
            limit=limit,
        )

    def consent_action(self, action_id: str, request: Any) -> Dict[str, Any]:
        return self.action_session_service.consent_action(action_id, request)

    def list_pending_frontend_actions(
        self,
        elder_user_id: str = "user_001",
        *,
        risk_tier: str = "safe",
        now=None,
    ) -> List[Dict[str, Any]]:
        timed_actions = self.frontend_action_service.build_timed_event_actions(
            self.get_due_timed_events(elder_user_id, now=now)
        )
        quiet_actions = [
            self.frontend_action_service.build_quiet_message_prompt_action(prompt)
            for prompt in self.get_elder_pending_messages(elder_user_id, risk_tier=risk_tier)
        ]
        session_actions = [
            self._frontend_action_from_session_action(action)
            for action in self.list_pending_actions(elder_user_id, target_channel="frontend")
        ]
        return self.frontend_action_service.sort_actions(
            [*timed_actions, *quiet_actions, *session_actions]
        )

    def _frontend_action_from_session_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(action.get("payload") or {})
        priority = str(payload.get("priority") or payload.get("risk_tier") or "normal")
        if priority in {"safe", "low"}:
            priority = "normal"
        elif priority == "medium":
            priority = "medium"
        elif priority in {"high", "crisis"}:
            priority = priority
        else:
            priority = "normal"
        return {
            "action_id": action.get("action_id"),
            "source": action.get("source") or "action_session",
            "source_turn_id": action.get("source_turn_id") or "",
            "target_channel": action.get("target_channel") or "frontend",
            "action_type": action.get("action_type") or "other",
            "name": f"start_{action.get('action_type') or 'action'}",
            "priority": priority,
            "requires_confirmation": bool(action.get("consent_required")),
            "interrupt_policy": "queue",
            "payload": {
                **payload,
                "content": action.get("content") or payload.get("content") or "",
                "post_reply": action.get("post_reply") or payload.get("post_reply") or "",
                "status": action.get("status"),
            },
        }

    async def reset_user_state(
        self,
        elder_user_id: str = "user_001",
        *,
        include_legacy_rag: bool = False,
    ) -> Dict[str, Any]:
        """Reset one user's current DataStore state.

        Legacy RAG memory is global in the current codebase, so it is only
        reset when explicitly requested by the caller.
        """

        user_id = self.user_context_service.normalize_user_id(elder_user_id)

        planner_reset = None
        planner = getattr(self, "background_planner_service", None)
        if planner is not None and hasattr(planner, "cancel_user_jobs"):
            planner_reset = await planner.cancel_user_jobs(
                user_id,
                reason="cancelled_by_user_state_reset",
            )

        data_store_reset = self.data_store.reset_user_state(user_id)

        legacy_rag_reset = None
        if include_legacy_rag:
            rag_helper = getattr(getattr(self, "emotional_agent", None), "rag_helper", None)
            if rag_helper is not None and hasattr(rag_helper, "reset_all_memory"):
                legacy_rag_reset = rag_helper.reset_all_memory()

        state = getattr(self, "last_system_state", None)
        if isinstance(state, dict):
            snapshot = state.get("context_snapshot") or {}
            if snapshot.get("user_id") == user_id:
                state.update(
                    {
                        "last_input": "",
                        "last_route": "",
                        "tool_calls": [],
                        "background_tasks": [],
                        "context_snapshot": {},
                        "agent_context": {},
                        "llm_inputs": [],
                    }
                )

        return {
            "user_id": user_id,
            "data_store": data_store_reset,
            "planner": planner_reset,
            "legacy_rag": {
                "requested": include_legacy_rag,
                "scope": "global" if include_legacy_rag else "not_touched",
                "result": legacy_rag_reset,
            },
        }

    def create_family_message(self, request: Any) -> Any:
        return self.family_policy_service.create_quiet_message(request)

    def get_elder_pending_messages(self, elder_user_id: str, risk_tier: str = "safe") -> List[Dict[str, Any]]:
        return self.family_policy_service.pending_quiet_message_prompts(
            elder_user_id,
            risk_tier=risk_tier,
        )

    def consent_to_elder_message(self, message_id: str, request: Any) -> Dict[str, Any]:
        return self.family_policy_service.consent_to_quiet_message(message_id, request)

    def create_community_announcement(self, request: Any, now=None) -> Any:
        return self.community_service.create_announcement(request, now=now)

    def list_community_announcements(
        self,
        community_id: str,
        *,
        only_active: bool = True,
        now=None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        return self.community_service.list_announcements(
            community_id,
            only_active=only_active,
            now=now,
            limit=limit,
        )

    def create_community_activity(self, request: Any, now=None) -> Any:
        return self.community_service.create_activity(request, now=now)

    def list_community_activities(
        self,
        community_id: str,
        *,
        only_active: bool = True,
        now=None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        return self.community_service.list_activities(
            community_id,
            only_active=only_active,
            now=now,
            limit=limit,
        )

    def list_community_crisis_alerts(self, elder_user_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        return self.community_service.list_crisis_alerts(elder_user_id, limit=limit)

    async def process_family_chat_stream(self, request: Any):
        async for event in self.family_agent.process_chat_stream(request):
            yield event

    def get_family_elder_summary(self, elder_user_id: str, child_user_id: str) -> Dict[str, Any]:
        return self.family_agent.build_elder_summary(elder_user_id, child_user_id)
