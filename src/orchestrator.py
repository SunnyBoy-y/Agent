import httpx
import json
import traceback
import asyncio
import uuid
from typing import Dict, Any, Optional, List
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
from src.services.medication_reminder_service import MedicationReminderService
from src.services.music_library_service import MusicLibraryService
from src.services.photo_library_service import PhotoLibraryService
from src.services.relay_message_service import RelayMessageService
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
                "context_snapshot": {}
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

            result = await self.proactive_agent.check_and_generate(user_id=user_id)
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

    def _schedule_assessment_background_tasks(self, assessment: MentalRiskAssessment) -> None:
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

        self.background_planner_service.schedule_from_assessment(assessment)

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

    async def process_input_stream(self, user_input: str, context: Optional[Dict[str, Any]] = None):
        """
        处理输入流，协调智能体运行
        """
        context = dict(context or {})
        user_id = self.user_context_service.normalize_user_id(context.get("user_id"))
        context["user_id"] = user_id
        turn_id = str(context.get("turn_id") or f"turn_{uuid.uuid4().hex}")
        context["turn_id"] = turn_id
        assessment = self.assessment_service.assess_text(user_input, context)
        assessment_detail = self.format_assessment_response(assessment)
        context["risk_assessment"] = assessment_detail
        current_care_plan = self.care_plan_service.get_plan(user_id)
        context["care_plan"] = self.format_care_plan_response(current_care_plan)
        self._schedule_assessment_background_tasks(assessment)

        # 0. 立即返回日志，给前端即时反馈
        logger.info(f"收到用户输入: {user_input}")
        yield create_event("log", f"收到用户输入: {user_input}")

        # 1. 并行：RAG预加载 + 外部表情API + 路由决策
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

        # 路由决策（纯规则，<1ms）
        force_agent = context.get("force_agent")
        valid_agents = [
            "emotional_agent", "medical_agent", "daily_life_agent",
            "interest_agent", "mental_health_agent", "antifraud_agent"
        ]
        target_agent_name = self._select_target_agent(
            user_input,
            context=context,
            assessment=assessment,
            care_plan=current_care_plan,
            force_agent=force_agent,
            valid_agents=valid_agents,
        )

        # RAG 预加载；视觉API 异步火墙（不等它，好了就用，不好不阻塞）
        shared_context_task = asyncio.create_task(
            self._build_shared_context(user_input, context)
        )
        visual_task = asyncio.create_task(_fetch_visual())

        shared_context = await shared_context_task
        shared_context = self.context_guard.sanitize_context(shared_context)
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

        # 2. 路由阶段（仅输出日志，决策已在上面完成）
        yield create_event("step", {"name": "router", "status": "running"})
            
        async with self.state_lock:
            self.last_system_state["last_route"] = target_agent_name
            self.last_system_state["last_input"] = user_input
            self.last_system_state["tool_calls"] = [] # Reset tool calls for the new turn
            self.last_system_state["context_snapshot"] = self._build_context_snapshot(shared_context)

        yield create_event("step", {"name": "router", "status": "done", "output": target_agent_name})
        yield create_event("log", f"🤖 路由至智能体: {target_agent_name}")
        
        # 3. 智能体执行
        yield create_event("step", {"name": target_agent_name, "status": "running"})
        
        # 更新 Agent 状态 (最后更新时间)
        await asyncio.to_thread(
            self.user_context_service.update_agent_status,
            user_id,
            agent_type=target_agent_name.replace("_agent", "")
        )
        
        full_response = ""
        
        try:
            if target_agent_name == "emotional_agent":
                # 情感智能体保持流式特性
                async for event in self._run_emotional_agent(user_input, shared_context):
                    if json.loads(event)["type"] == "token":
                        full_response += json.loads(event)["data"]
                    yield event
            else:
                # 其他智能体
                result = await self._run_specific_agent(target_agent_name, user_input, shared_context)
                
                content = self.safety_policy.sanitize_response(
                    result.get("content", ""),
                    risk_tier=assessment.risk_tier
                )
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
                yield create_event("error", "阿里云百炼服务欠费或余额不足。")
            else:
                yield create_event("error", "系统处理出错，请稍后再试。")
            
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
        profile, recent_history, memory_context, emotion_trend, agent_status = await asyncio.gather(
            asyncio.to_thread(self.user_context_service.get_profile, user_id),
            asyncio.to_thread(self.user_context_service.get_recent_history, user_id, 5),
            asyncio.to_thread(rag.search_comprehensive_memory, user_input, 3),
            asyncio.to_thread(self.user_context_service.get_emotion_trend, user_id),
            asyncio.to_thread(self.user_context_service.get_agent_status, user_id),
        )
        recent_history = self._sanitize_recent_history(recent_history)

        shared_context = dict(context)
        shared_context["user_id"] = user_id
        shared_context["user_profile"] = profile
        shared_context["recent_history"] = recent_history
        shared_context["recent_history_text"] = self._format_recent_history(recent_history)
        shared_context["memory_context"] = memory_context
        shared_context["emotion_trend"] = emotion_trend
        shared_context["agent_status"] = agent_status
        shared_context["care_plan"] = context.get("care_plan") or self.format_care_plan_response(
            self.care_plan_service.get_plan(user_id)
        )
        try:
            shared_context["music_library_summary"] = await asyncio.to_thread(
                self.music_library_service.library_summary,
                user_id,
                12,
            )
        except Exception as exc:
            logger.warning(f"Music library summary failed: {exc}")
            shared_context["music_library_summary"] = []
        try:
            shared_context["photo_library_summary"] = await asyncio.to_thread(
                self.photo_library_service.summarize_music_photo_context,
                user_id,
                8,
            )
        except Exception as exc:
            logger.warning(f"Photo library summary failed: {exc}")
            shared_context["photo_library_summary"] = ""
        return shared_context

    def _select_target_agent(
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

        return self.router.route_sync(user_input, context=context)

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
        profile = context.get("user_profile") or {}
        return {
            "user_id": context.get("user_id"),
            "profile_name": profile.get("name", "未知"),
            "health_condition": profile.get("health_condition", []),
            "preferences": profile.get("preferences", []),
            "visual_analysis": context.get("visual_analysis"),
            "voice_emotion": context.get("voice_emotion"),
            "recent_history_preview": (context.get("recent_history_text") or "")[:300],
            "memory_context_preview": (context.get("memory_context") or "")[:300],
        }

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
                if analysis.get("risk_level") == "Safe":
                     content = "我帮您看了，这个信息看起来是安全的，不用担心。"
                else:
                     content = f"这里面可能有诈骗风险（等级：{analysis.get('risk_level')}），千万别转账！我这就通知您的家人。"
            
            risk = self._normalize_risk_level(analysis.get("risk_level", "low"))
            return {
                "content": content,
                "action": "warning" if risk != "safe" else "nod",
                "risk_level": risk,
                "family_message": intervention.get("action_to_family"),
                "community_message": intervention.get("action_to_community"),
            }
        return {"content": "系统暂时没找到合适的处理专员，我先陪您慢慢说。", "action": "nod", "risk_level": "low"}

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
                    completed_segments.append(safe_segment)

            if force and pending_stream_buffer:
                safe_segment = self.safety_policy.sanitize_response(
                    pending_stream_buffer,
                    risk_tier=risk_tier,
                )
                pending_stream_buffer = ""
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
                        fallback_reply = self._build_emotional_fallback_reply(
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

    def _build_emotional_fallback_reply(
        self,
        user_input: str,
        context: Dict[str, Any],
        emotional_args: Optional[Dict[str, Any]] = None
    ) -> str:
        profile = context.get("user_profile") or {}
        name = profile.get("name") or "您"
        expression = (emotional_args or {}).get("expression", "neutral")
        risk_level = (emotional_args or {}).get("risk_level", "low")
        normalized_input = (user_input or "").strip()

        greeting_keywords = ("你好", "您好", "在吗", "在不在", "哈喽")
        if normalized_input in greeting_keywords or any(k in normalized_input for k in greeting_keywords):
            return f"{name}，您好呀，我在这儿陪着您呢。您这会儿想先聊聊天，还是想听段戏、看看老照片？"

        if risk_level == "high":
            return f"{name}，我听着您这会儿挺难受的，先别一个人扛着。我陪您慢慢说，您现在最想让我先帮您做点什么？"
        if expression == "sad":
            return f"{name}，我听出来您心里有点发沉。没事，咱们慢慢唠，您愿意跟我说说刚才最挂心的是啥吗？"
        if expression == "concerned":
            return f"{name}，我在呢。您刚才这句话我听进心里了，咱们慢慢说，看看我能陪您一起理一理什么。"
        if expression == "happy":
            return f"{name}，听您这么一说，我也跟着高兴。您要是愿意，咱们接着往下聊。"

        return f"{name}，我在这儿陪着您。您要是愿意，就接着跟我说说，我认真听着呢。"

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
        post_reply = payload.get("post_reply") or "这首歌先到这里。您现在心里有没有松一点？"
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
