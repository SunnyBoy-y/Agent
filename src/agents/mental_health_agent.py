import asyncio
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config import Config
from src.policies.safety_policy import SafetyPolicy
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper
from src.agents.companion_prompt import build_companion_system_prompt, risk_from_context, stage_from_context

class MentalHealthAgent:
    def __init__(self, safety_policy: SafetyPolicy | None = None):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.5
        )
        self.rag_helper = RAGHelper()
        self.safety_policy = safety_policy or SafetyPolicy()
        self.anxiety_keywords = [
            "焦虑", "心慌", "发慌", "睡不着", "失眠", "烦", "烦躁", "紧张", "担心", "害怕"
        ]
        self.lonely_keywords = [
            "孤独", "孤单", "一个人", "没人说话", "寂寞"
        ]

    async def arun(self, input_text: str, context: dict = None):
        """
        处理心理健康相关的咨询
        1. 情绪疏导 (CBT认知行为疗法风格)
        2. 孤独感缓解
        3. 心理支持
        """
        logger.info(f"MentalHealthAgent received: {input_text}")
        if context is None:
            context = {}
        
        # 记录本次情绪
        if context.get("emotion"):
            self.rag_helper.log_emotion(context["emotion"], "medium")

        knowledge_context = await self._get_knowledge_context(input_text, context)
        profile = context.get("user_profile")
        if profile is None:
            profile = await asyncio.to_thread(self.rag_helper.get_user_profile)
        profile_str = self._format_profile(profile)
        visual_emotion = context.get("visual_analysis", {}).get("emotion", "neutral")
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        intervention_mode = self._detect_intervention_mode(input_text)

        if intervention_mode == "anxiety":
            response = await self._build_anxiety_guidance(
                input_text=input_text,
                context=context,
                profile_str=profile_str,
                recent_history_text=recent_history_text,
                knowledge_context=knowledge_context,
            )
            return {
                "content": self._safe_text(response, "medium"),
                "action": "recommend_community_activity",
                "risk_level": "medium"
            }

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="mental_health_support",
                    stage=stage_from_context(context, "anxiety.emotional_first_aid"),
                    risk_tier=risk_from_context(context, "medium"),
                    task=(
                        "做生活化情绪支持：先接纳，再稳定，再给一个很小的下一步。"
                        "小暖要像长期陪伴者，不像临床专家。"
                    ),
                    extra_rules=[
                        "优先参考检索到的知识和记忆，但不要空泛说教。",
                        "不做“抑郁症/焦虑症/双相”等诊断命名，不给医疗建议，不暴露内部推理或Thought。",
                        "孤独时可以给一个马上能做的小建议，如晒太阳、联系熟人、去社区活动室坐坐。",
                        "默认2到3句话；高风险时3到4句以内，短而稳。",
                    ],
                ),
            ),
            (
                "human",
                "老人的状态: {input_text}\n当前视觉情绪: {visual_emotion}\n老人画像: {profile}\n最近对话: {recent_history_text}\n\n检索到的心理学知识和相关记忆:\n{knowledge_context}\n\n请直接回复老人。",
            ),
        ])
        
        visual_emotion = context.get("visual_analysis", {}).get("emotion", "neutral")
        
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "input_text": input_text,
            "visual_emotion": visual_emotion,
            "profile": profile_str,
            "recent_history_text": recent_history_text,
            "knowledge_context": knowledge_context or "暂无可用知识，优先做生活化安抚。"
        })
        
        return {
            "content": self._safe_text(response.content, "medium"),
            "action": "comfort",
            "risk_level": "medium" # 心理咨询通常意味着有一定困扰
        }

    async def astream_response(self, input_text: str, context: dict = None):
        result = await self.arun(input_text, context)
        content = self._safe_text(result.get("content", ""), result.get("risk_level", "medium"))
        result["content"] = content
        for token in self._chunk_text(content):
            yield {"type": "token", "data": token}
        yield {"type": "done", "data": result}

    def _chunk_text(self, text: str, chunk_size: int = 24) -> List[str]:
        value = str(text or "")
        return [value[index:index + chunk_size] for index in range(0, len(value), chunk_size)]

    async def _get_knowledge_context(self, input_text: str, context: Dict[str, Any]) -> str:
        """优先检索知识库和记忆，若索引不存在则尝试自动建立。"""
        memory_context = context.get("memory_context", "")
        if memory_context.strip():
            return memory_context

        memory_context = await asyncio.to_thread(self.rag_helper.search_comprehensive_memory, input_text, 3)
        if memory_context.strip():
            return memory_context

        try:
            await asyncio.to_thread(self.rag_helper.load_and_index_documents, False)
            memory_context = await asyncio.to_thread(self.rag_helper.search_comprehensive_memory, input_text, 3)
        except Exception as exc:
            logger.warning(f"MentalHealthAgent knowledge retrieval fallback failed: {exc}")

        return memory_context

    def _detect_intervention_mode(self, input_text: str) -> str:
        lowered = input_text.lower()
        if any(keyword in lowered for keyword in self.anxiety_keywords):
            return "anxiety"
        if any(keyword in lowered for keyword in self.lonely_keywords):
            return "lonely"
        return "general"

    async def _build_anxiety_guidance(
        self,
        *,
        input_text: str,
        context: Dict[str, Any],
        profile_str: str,
        recent_history_text: str,
        knowledge_context: str,
    ) -> str:
        community_text = self._get_community_activity_text(context)
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="anxiety_guidance",
                    stage="anxiety.emotional_first_aid",
                    risk_tier=risk_from_context(context, "medium"),
                    task="生成焦虑/心慌时的生活化安抚回应，并在合适时自然带入社区活动线索。",
                    extra_rules=[
                        "只输出2到3句，不要诊断。",
                        "先稳定当下，再给一个很小的下一步。",
                        "社区活动线索只能作为可选陪伴，不要像硬推活动。",
                    ],
                ),
            ),
            (
                "human",
                "老人的状态: {input_text}\n老人画像: {profile}\n最近对话: {recent_history_text}\n知识/记忆: {knowledge_context}\n社区线索: {community_text}\n\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "input_text": input_text,
            "profile": profile_str,
            "recent_history_text": recent_history_text,
            "knowledge_context": knowledge_context or "暂无",
            "community_text": community_text or "暂无",
        })
        return str(getattr(response, "content", "") or "")

    def _safe_text(self, text: str, risk_tier: str = "medium") -> str:
        return self.safety_policy.sanitize_response(text or "", risk_tier=risk_tier)

    def _get_community_activity_text(self, context: Dict[str, Any]) -> str:
        community_activities = context.get("community_activities") or []
        if isinstance(community_activities, list) and community_activities:
            first = community_activities[0]
            if isinstance(first, dict):
                name = first.get("title") or first.get("name", "社区活动")
                time = first.get("time_text") or first.get("time")
                location = first.get("location")
                parts: List[str] = []
                if time:
                    parts.append(time)
                if location:
                    parts.append(location)
                schedule = "，".join(parts)
                if schedule:
                    return f"{schedule}有{name}"
                return f"社区这会儿有{name}"
            if isinstance(first, str) and first.strip():
                return f"社区这会儿有{first.strip()}"

        return ""

    def _format_profile(self, profile: Dict[str, Any]) -> str:
        if not profile:
            return "暂无画像信息"

        name = profile.get("name", "未知")
        preferences = profile.get("preferences", [])
        health = profile.get("health_condition", [])
        family = profile.get("family_members", [])
        return (
            f"姓名: {name}; "
            f"偏好: {preferences if preferences else '暂无'}; "
            f"健康情况: {health if health else '暂无'}; "
            f"家庭成员: {family if family else '暂无'}"
        )
