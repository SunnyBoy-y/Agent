import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
try:
    from langchain_core.pydantic_v1 import BaseModel, Field
except ImportError:
    from pydantic import BaseModel, Field

from src.config import Config
from src.services.response_style_guard import ResponseStyleGuard
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper
from src.agents.companion_prompt import build_companion_system_prompt


class ProactiveDraft(BaseModel):
    content: str = ""
    target_agent: str = "emotional_agent"
    scene: str = ""
    open_question: str = ""
    addressing_used: bool = False


class ProactiveAgent:
    def __init__(self, user_context_service=None):
        self.user_context_service = user_context_service
        self.rag_helper = None if user_context_service else RAGHelper()
        self.response_style_guard = ResponseStyleGuard()
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.35,
            timeout=30,
            max_retries=2,
        )

        self.idle_threshold_seconds = 15
        self.proactive_interval_seconds = 15

        self.anxiety_keywords = ["焦虑", "紧张", "心慌", "担心", "害怕", "睡不着", "不安"]
        self.lonely_keywords = ["孤独", "寂寞", "没人陪", "想家", "想孩子", "无聊"]
        self.family_keywords = ["家人", "孩子", "孙子", "孙女", "儿子", "女儿"]
        self.health_keywords = ["药", "吃药", "头痛", "胸闷", "不舒服", "发烧", "疼"]
        self.interest_keywords = ["唱歌", "听歌", "戏", "下棋", "跳舞", "广场舞", "照片", "音乐"]
        self.daily_life_keywords = ["吃饭", "洗澡", "穿衣", "起床", "睡觉", "散步", "喝水"]

        self.domain_to_agent = {
            "mental_health": "mental_health_agent",
            "medical": "medical_agent",
            "emotional": "emotional_agent",
            "interest": "interest_agent",
            "daily_life": "daily_life_agent",
        }

    async def check_and_generate(
        self,
        user_id: str = "user_001",
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        user_id = self._normalize_user_id(user_id)
        context = dict(context or {})

        status = context.get("agent_status") or self._get_agent_status(user_id)
        if not status:
            return None

        last_interaction = self._parse_time(status.get("last_user_interaction"))
        last_proactive = self._parse_time(status.get("last_proactive_time"), fallback="2000-01-01 00:00:00")
        now = datetime.now()
        idle_seconds = (now - last_interaction).total_seconds()
        proactive_gap = (now - last_proactive).total_seconds()

        if idle_seconds < self.idle_threshold_seconds:
            logger.info(f"User is active (idle {idle_seconds:.1f}s), skipping proactive check.")
            return None

        if proactive_gap < self.proactive_interval_seconds:
            logger.info(f"Recent proactive ping exists ({proactive_gap:.1f}s), skipping duplicate prompt.")
            return None

        profile = context.get("user_profile") or self._get_profile(user_id)
        recent_history = context.get("recent_history") or self._get_recent_history(user_id, limit=12)
        emotion_trend = context.get("emotion_trend") or self._get_emotion_trend(user_id)
        memory_context = str(context.get("memory_context") or "").strip()
        recent_history_text = str(context.get("recent_history_text") or self._format_recent_history(recent_history)).strip()
        care_plan = context.get("care_plan") or {}
        strategy = self._select_strategy(profile, recent_history, emotion_trend, status)

        scene_context = context.get("scene_context") or self._build_scene_context(
            user_id=user_id,
            profile=profile,
            recent_history=recent_history,
            recent_history_text=recent_history_text,
            memory_context=memory_context,
            emotion_trend=emotion_trend,
            status=status,
            care_plan=care_plan,
            strategy=strategy,
        )

        draft = await self._generate_reply(
            profile=profile,
            recent_history_text=recent_history_text,
            memory_context=memory_context,
            emotion_trend=emotion_trend,
            status=status,
            scene_context=scene_context,
            strategy=strategy,
        )

        last_content = str(status.get("last_proactive_content") or "").strip()
        content = str(getattr(draft, "content", "") or "").strip()
        target_agent = self._normalize_target_agent(
            getattr(draft, "target_agent", "") or "",
            strategy["domain"],
        )
        scene = str(getattr(draft, "scene", "") or strategy["reason"]).strip()
        open_question = str(getattr(draft, "open_question", "") or "").strip()
        addressing_used = bool(getattr(draft, "addressing_used", False))

        if not content or self._same_reply(content, last_content):
            logger.info("Skipping proactive prompt because generated content was empty or duplicate.")
            return None

        content = self._cleanup_reply(
            content,
            profile=profile,
            status=status,
            scene_context=scene_context,
            addressing_used=addressing_used,
        )
        if not content:
            logger.info("Skipping proactive prompt because cleaned content is empty.")
            return None

        logger.info(f"Proactive Agent selected strategy: {strategy}")

        self._add_memory(
            user_id,
            user_input=f"[proactive:{strategy['reason']}:{int(idle_seconds)}s]",
            agent_response=content,
        )
        self._update_proactive_status(user_id, strategy["domain"], content)

        return {
            "user_id": user_id,
            "content": content,
            "target_agent": target_agent,
            "scene": scene,
            "open_question": open_question,
            "addressing_used": addressing_used,
            "scene_context": scene_context,
        }

    def _normalize_user_id(self, user_id: Optional[str]) -> str:
        if self.user_context_service:
            return self.user_context_service.normalize_user_id(user_id)
        return str(user_id or "user_001").strip() or "user_001"

    def _get_agent_status(self, user_id: str) -> Dict[str, Any]:
        if self.user_context_service:
            return self.user_context_service.get_agent_status(user_id)
        return self.rag_helper.get_agent_status()

    def _get_profile(self, user_id: str) -> Dict[str, Any]:
        if self.user_context_service:
            return self.user_context_service.get_profile(user_id)
        return self.rag_helper.get_user_profile()

    def _get_recent_history(self, user_id: str, limit: int) -> List[Dict[str, Any]]:
        if self.user_context_service:
            return self.user_context_service.get_recent_history(user_id, limit=limit)
        return self.rag_helper.get_recent_history(limit=limit)

    def _get_emotion_trend(self, user_id: str) -> str:
        if self.user_context_service:
            return self.user_context_service.get_emotion_trend(user_id)
        return self.rag_helper.get_emotion_trend()

    def _add_memory(self, user_id: str, user_input: str, agent_response: str) -> None:
        if self.user_context_service:
            self.user_context_service.add_memory(user_id, user_input, agent_response)
            return
        self.rag_helper.add_memory(user_input, agent_response)

    def _update_proactive_status(self, user_id: str, domain: str, content: str) -> None:
        if self.user_context_service:
            self.user_context_service.update_proactive_status(user_id, domain, content)
            return
        self.rag_helper.update_proactive_status(domain, content)

    def _parse_time(self, value: Optional[str], fallback: Optional[str] = None) -> datetime:
        raw = value or fallback or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            if fallback and raw != fallback:
                return self._parse_time(fallback)
            return datetime.now()

    def _select_strategy(
        self,
        profile: Dict[str, Any],
        recent_history: List[Dict[str, Any]],
        emotion_trend: str,
        status: Dict[str, Any],
    ) -> Dict[str, str]:
        recent_user_text = self._get_recent_user_text(recent_history)
        preferences_text = " ".join(map(str, profile.get("preferences", [])))
        health_text = " ".join(map(str, profile.get("health_condition", [])))

        if self._contains_any(recent_user_text, self.anxiety_keywords) or "high risk" in emotion_trend or "unstable" in emotion_trend:
            return {"domain": "mental_health", "reason": "anxiety_support"}
        if self._contains_any(recent_user_text + health_text, self.health_keywords):
            return {"domain": "medical", "reason": "health_check"}
        if self._contains_any(recent_user_text, self.lonely_keywords) or self._contains_any(recent_user_text, self.family_keywords):
            return {"domain": "emotional", "reason": "family_connection"}
        if self._contains_any(recent_user_text + preferences_text, self.interest_keywords):
            return {"domain": "interest", "reason": "interest_followup"}
        if self._contains_any(recent_user_text, self.daily_life_keywords):
            return {"domain": "daily_life", "reason": "daily_life_followup"}

        last_domain = str(status.get("last_proactive_domain") or "")
        if last_domain != "emotional":
            return {"domain": "emotional", "reason": "general_companionship"}
        return {"domain": "daily_life", "reason": "general_checkin"}

    def _build_scene_context(
        self,
        *,
        user_id: str,
        profile: Dict[str, Any],
        recent_history: List[Dict[str, Any]],
        recent_history_text: str,
        memory_context: str,
        emotion_trend: str,
        status: Dict[str, Any],
        care_plan: Dict[str, Any],
        strategy: Dict[str, str],
    ) -> Dict[str, Any]:
        display_name = self._display_name(profile)
        last_user = self._get_recent_user_text(recent_history)
        last_assistant = self._get_recent_assistant_text(recent_history)
        return {
            "turn": {
                "user_id": user_id,
                "source": "proactive",
                "user_input": "",
            },
            "current_scene": {
                "risk_tier": str(care_plan.get("risk_tier") or "safe"),
                "domain": strategy["domain"],
                "reason": strategy["reason"],
                "emotion_trend": emotion_trend,
                "last_proactive_domain": status.get("last_proactive_domain") or "",
            },
            "dialogue_state": {
                "last_user_utterance": last_user,
                "last_assistant_reply": last_assistant,
                "recent_history_text": recent_history_text,
            },
            "care_plan": {
                "active_domain": care_plan.get("active_domain") or "general",
                "risk_tier": care_plan.get("risk_tier") or "safe",
                "current_stage": care_plan.get("current_stage") or "companionship",
                "next_turn_goal": care_plan.get("next_turn_goal") or "",
                "target_agent": care_plan.get("target_agent") or "",
            },
            "retrieval": {
                "memory_context": memory_context,
            },
            "addressing_policy": {
                "display_name": display_name,
                "last_assistant_used_name": bool(display_name and display_name in last_assistant),
                "allow_name_once": bool(display_name),
            },
        }

    async def _generate_reply(
        self,
        *,
        profile: Dict[str, Any],
        recent_history_text: str,
        memory_context: str,
        emotion_trend: str,
        status: Dict[str, Any],
        scene_context: Dict[str, Any],
        strategy: Dict[str, str],
    ) -> ProactiveDraft:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    build_companion_system_prompt(
                        phase="proactive_companion",
                        stage=(scene_context.get("care_plan") or {}).get("current_stage") or "companionship",
                        risk_tier=(scene_context.get("current_scene") or {}).get("risk_tier") or "safe",
                        task=(
                            "Write one short natural Chinese proactive reply that fits the current scene. "
                            "Return structured output only."
                        ),
                        extra_rules=[
                            "不要用固定开场，不要像通知弹窗。",
                            "Respond to the exact scene first.",
                            "Do not repeat the elder's name more than once.",
                            "If you ask a question, ask only one gentle question.",
                            "Return structured output only.",
                        ],
                    ),
                ),
                ("human", "{payload}"),
            ]
        )
        payload = {
            "profile": profile,
            "recent_history_text": recent_history_text,
            "memory_context": memory_context,
            "emotion_trend": emotion_trend,
            "status": status,
            "scene_context": scene_context,
            "strategy": strategy,
        }
        llm = self.llm.with_structured_output(ProactiveDraft)
        chain = prompt | llm
        try:
            response = await chain.ainvoke({"payload": json.dumps(payload, ensure_ascii=False)})
            return self._coerce_draft(response)
        except Exception as exc:
            logger.warning(f"Proactive LLM generation failed: {exc}")
            return ProactiveDraft(
                content="",
                target_agent=self._normalize_target_agent("", strategy["domain"]),
                scene=strategy["reason"],
                open_question="",
                addressing_used=False,
            )

    def _coerce_draft(self, response: Any) -> ProactiveDraft:
        if isinstance(response, ProactiveDraft):
            return response
        if isinstance(response, dict):
            return ProactiveDraft.model_validate(response) if hasattr(ProactiveDraft, "model_validate") else ProactiveDraft.parse_obj(response)
        if hasattr(response, "model_dump"):
            data = response.model_dump(mode="python")
            return ProactiveDraft.model_validate(data) if hasattr(ProactiveDraft, "model_validate") else ProactiveDraft.parse_obj(data)
        if hasattr(response, "content"):
            content = getattr(response, "content", "")
            try:
                data = json.loads(content)
                return ProactiveDraft.model_validate(data) if hasattr(ProactiveDraft, "model_validate") else ProactiveDraft.parse_obj(data)
            except Exception:
                return ProactiveDraft(content=str(content or "").strip())
        return ProactiveDraft()

    def _normalize_target_agent(self, target_agent: str, domain: str) -> str:
        candidate = str(target_agent or "").strip()
        if candidate in self.domain_to_agent.values():
            return candidate
        return self.domain_to_agent.get(domain, "emotional_agent")

    def _cleanup_reply(
        self,
        text: str,
        *,
        profile: Dict[str, Any],
        status: Dict[str, Any],
        scene_context: Dict[str, Any],
        addressing_used: bool,
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = self.response_style_guard.clean(cleaned, {"scene_context": scene_context})
        display_name = self._display_name(profile)
        if display_name:
            cleaned = self._remove_leading_name(
                cleaned,
                display_name=display_name,
                repeat_name=bool(status.get("last_proactive_content") and display_name in str(status.get("last_proactive_content"))),
            )
            if not addressing_used and cleaned.startswith(display_name):
                cleaned = self._remove_leading_name(cleaned, display_name=display_name, repeat_name=True)
        return cleaned.strip()

    def _remove_leading_name(self, text: str, *, display_name: str, repeat_name: bool) -> str:
        if not display_name:
            return text
        escaped = re.escape(display_name)
        pattern = re.compile(rf"^\s*{escaped}\s*[,，、:：]?\s*")
        if repeat_name and pattern.search(text):
            return pattern.sub("", text, count=1).lstrip()
        if text.count(display_name) > 1:
            first = text.find(display_name)
            return text[:first + len(display_name)] + text[first + len(display_name):].replace(display_name, "")
        return text

    def _display_name(self, profile: Optional[Dict[str, Any]]) -> str:
        if self.user_context_service:
            return self.user_context_service.display_name(profile or {}, fallback="")
        if not isinstance(profile, dict):
            return ""
        name = str(profile.get("name") or "").strip()
        if name.lower() in {"unknown", "none", "null"}:
            return ""
        return name

    def _format_recent_history(self, recent_history: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for item in recent_history or []:
            role = "elder" if item.get("role") == "user" else "assistant"
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines[-6:])

    def _get_recent_user_text(self, recent_history: List[Dict[str, Any]]) -> str:
        texts: List[str] = []
        for item in recent_history or []:
            if item.get("role") != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                texts.append(content)
        return " ".join(texts[-4:])

    def _get_recent_assistant_text(self, recent_history: List[Dict[str, Any]]) -> str:
        for item in reversed(recent_history or []):
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content") or "").strip()
            if content:
                return content
        return ""

    def _same_reply(self, current: str, last: str) -> bool:
        return self._normalize_text(current) == self._normalize_text(last)

    def _normalize_text(self, text: str) -> str:
        value = str(text or "").strip().lower()
        value = re.sub(r"\s+", "", value)
        value = re.sub(r"[，。！？!?、,:：]", "", value)
        return value

    def _contains_any(self, text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)
