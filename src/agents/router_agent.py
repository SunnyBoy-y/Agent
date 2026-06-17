import json
import re
from typing import Any, Dict, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src.config import Config
from src.services.context_guard import ContextGuard
from src.utils.logger import logger
from src.agents.companion_prompt import build_companion_system_prompt


class RouterAgent:
    """Fast intent router with rule protection and LLM fallback."""

    AGENTS = {
        "medical_agent",
        "daily_life_agent",
        "interest_agent",
        "mental_health_agent",
        "antifraud_agent",
        "emotional_agent",
    }

    MUSIC_KEYWORDS = [
        "放歌",
        "放音乐",
        "播放",
        "听歌",
        "音乐",
        "歌曲",
        "歌单",
        "唱片",
        "来一首",
        "点一首",
    ]
    MUSIC_PATTERNS = [
        r"(放|播|听|点|来).{0,6}(歌|音乐|曲子|唱片)",
        r"(想听|帮我放|给我放).{0,12}(歌|音乐|曲子)",
    ]
    EMERGENCY_KEYWORDS = [
        "救命",
        "摔倒",
        "跌倒",
        "起不来",
        "胸口疼",
        "胸闷",
        "喘不上气",
        "呼吸困难",
        "快不行了",
    ]
    MEDICAL_KEYWORDS = [
        "吃药",
        "药",
        "血压",
        "血糖",
        "头疼",
        "头痛",
        "发烧",
        "疼",
        "痛",
        "晕",
        "不舒服",
    ]
    DAILY_PATTERNS = [
        r"我(今天|刚才|刚刚|中午|晚上|早上).{0,8}(吃|喝|洗澡|散步|出门|去了|买了)",
        r"(记录|记一下|帮我记).{0,12}(吃|喝|服药|散步|睡觉|去了|买了)",
    ]
    STRONG_FRAUD_KEYWORDS = [
        "中奖",
        "法院传票",
        "公安局打电话",
        "让我转账",
        "安全账户",
        "银行卡冻结",
        "验证码",
    ]

    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.0,
            timeout=20,
            max_retries=2,
        )
        self.agents = list(self.AGENTS)
        self.context_guard = ContextGuard()

    async def route(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Route by protected rules first, then ask the LLM for ambiguous turns."""
        context = context or {}
        text = str(input_text or "").strip()
        logger.info(f"RouterAgent routing: {text}")

        rule_based_route = self._route_by_rules(text, context=context)
        if rule_based_route:
            logger.info(f"Rule-based route selected: {rule_based_route}")
            return rule_based_route

        return await self._llm_route(text, context)

    def route_sync(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Synchronous protected-rule route for callers that cannot await."""
        return self._route_by_rules(input_text, context=context) or "emotional_agent"

    def _route_by_rules(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = str(input_text or "").strip()
        if not text:
            return "emotional_agent"

        context_guard = getattr(self, "context_guard", None) or ContextGuard()
        guarded_route = context_guard.route_override(text, context=context)
        if guarded_route:
            return guarded_route

        if self._contains_any(text, self.EMERGENCY_KEYWORDS):
            return "medical_agent"
        if self._is_music_request(text):
            return "interest_agent"
        if self._contains_any(text, self.STRONG_FRAUD_KEYWORDS):
            return "antifraud_agent"
        if any(re.search(pattern, text) for pattern in self.DAILY_PATTERNS):
            return "daily_life_agent"

        # Keep only high-confidence keyword routes. Ambiguous health or mood text
        # should still reach LLM routing so the answer fits the current scene.
        if self._looks_like_direct_medical_query(text):
            return "medical_agent"
        if self._looks_like_simple_companionship(text):
            return "emotional_agent"

        return ""

    def _looks_like_direct_medical_query(self, text: str) -> bool:
        if not self._contains_any(text, self.MEDICAL_KEYWORDS):
            return False
        query_markers = ("怎么办", "怎么吃", "要不要", "能不能", "该不该", "是不是", "需要")
        return any(marker in text for marker in query_markers)

    def _is_music_request(self, input_text: str) -> bool:
        if self._contains_any(input_text, self.MUSIC_KEYWORDS):
            return True
        return any(re.search(pattern, input_text) for pattern in self.MUSIC_PATTERNS)

    def _looks_like_simple_companionship(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized or len(normalized) > 18:
            return False
        if normalized in {"hi", "hello", "hey"}:
            return True
        companionship_markers = (
            "\u4f60\u597d",  # 你好
            "\u60a8\u597d",  # 您好
            "\u5728\u5417",  # 在吗
            "\u55e8",
            "\u54c8\u55bd",
            "\u4f60\u54c8",
            "\u5c0f\u6696",
        )
        if any(marker in normalized for marker in companionship_markers):
            return True

        blockers = (
            "\u6551\u547d", "\u6454", "\u75bc", "\u75db", "\u80f8", "\u5598",
            "\u836f", "\u8f6c\u8d26", "\u94b1", "\u9a8c\u8bc1\u7801",
            "\u7167\u7247", "\u76f8\u518c", "\u97f3\u4e50", "\u653e\u6b4c",
            "\u542c\u6b4c", "\u5929\u6c14", "\u600e\u4e48", "\u4e3a\u4ec0\u4e48",
        )
        if any(marker in normalized for marker in blockers):
            return False
        return len(normalized) <= 8

    async def _llm_route(self, input_text: str, context: Dict[str, Any]) -> str:
        context_hint = self._build_context_hint(context)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    build_companion_system_prompt(
                        phase="intent_router",
                        stage=(context.get("care_plan") or {}).get("current_stage") or "companionship",
                        risk_tier=(context.get("risk_assessment") or {}).get("risk_tier") or (context.get("care_plan") or {}).get("risk_tier") or "safe",
                        task="Choose exactly one agent name from the allowed list. Use current input first, then scene and memory.",
                        extra_rules=[
                            "Return only one agent name. Do not explain.",
                            "Do not route based only on old memory if the current input is ordinary companionship.",
                            "Crisis/self-harm goes to mental_health_agent; physical emergency or medication logistics goes to medical_agent.",
                        ],
                    ),
                ),
                (
                    "human",
                    "Allowed agents: medical_agent, daily_life_agent, interest_agent, "
                    "mental_health_agent, antifraud_agent, emotional_agent\n\n"
                    "Routing guide:\n"
                    "- medical_agent: physical symptoms, medication, health logistics.\n"
                    "- daily_life_agent: daily records, meals, errands, routines.\n"
                    "- interest_agent: music, photos, hobbies, entertainment, art, games.\n"
                    "- mental_health_agent: anxiety, loneliness, depression, crisis, self-harm.\n"
                    "- antifraud_agent: money, transfer, suspicious calls, scams.\n"
                    "- emotional_agent: ordinary companionship or unclear cases.\n\n"
                    "User input: {input_text}\n"
                    "Context: {context_hint}\n"
                    "Return only one agent name.",
                ),
            ]
        )
        try:
            chain = prompt | self.llm
            response = await chain.ainvoke(
                {
                    "input_text": input_text,
                    "context_hint": context_hint,
                }
            )
            selected_agent = self._extract_agent_name(str(getattr(response, "content", "") or ""))
            if selected_agent not in self.AGENTS:
                logger.warning(f"Router selected unknown agent: {selected_agent}")
                return "emotional_agent"
            logger.info(f"LLM routed to: {selected_agent}")
            return selected_agent
        except Exception as exc:
            logger.error(f"LLM routing failed: {exc}")
            return "emotional_agent"

    def _build_context_hint(self, context: Dict[str, Any]) -> str:
        if not context:
            return "no extra context"

        profile = context.get("user_profile") or {}
        visual = context.get("visual_analysis") or {}
        scene_context = context.get("scene_context") or {}
        care_plan = context.get("care_plan") or {}
        parts = {
            "profile": {
                "name": profile.get("name"),
                "health_condition": profile.get("health_condition", []),
                "preferences": profile.get("preferences", []),
                "family_members": profile.get("family_members", []),
            },
            "scene_context": scene_context,
            "care_plan": care_plan,
            "visual_emotion": visual.get("emotion") if isinstance(visual, dict) else None,
            "audio_transcript": context.get("audio_transcript") or "",
            "recent_history": context.get("recent_history_text") or "",
            "memory_context": context.get("memory_context") or "",
        }
        return json.dumps(parts, ensure_ascii=False)[:2400]

    def _contains_any(self, text: str, markers) -> bool:
        return any(marker in text for marker in markers)

    def _extract_agent_name(self, text: str) -> str:
        value = str(text or "").strip()
        if value in self.AGENTS:
            return value
        for agent_name in self.AGENTS:
            if agent_name in value:
                return agent_name
        return value.split()[0].strip(".,;:，。；：`'\"") if value else ""
