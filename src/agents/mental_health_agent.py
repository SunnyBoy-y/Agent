import asyncio
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config import Config
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper

class MentalHealthAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.5
        )
        self.rag_helper = RAGHelper()
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
            response = self._build_anxiety_guidance(context)
            return {
                "content": response,
                "action": "recommend_community_activity",
                "risk_level": "medium"
            }

        prompt = ChatPromptTemplate.from_template("""
        你是一位专业的心理咨询师，专为老年人提供心理支持。
        
        老人的状态: {input_text}
        当前视觉情绪: {visual_emotion}
        老人画像: {profile}
        最近对话: {recent_history_text}
        
        检索到的心理学知识和相关记忆:
        {knowledge_context}
        
        请运用“共情式倾听”和“积极心理暗示”技巧：
        1. 先接纳老人的情绪（不评判）。
        2. 优先参考检索到的心理学知识和相关记忆，不要只凭空泛安慰。
        3. 引导老人关注当下的微小幸福。
        4. 如果老人感到孤独，提供一个具体、可马上执行的建议（如给老友打个电话、晒晒太阳、去社区活动室坐坐）。
        5. 语气温和、缓慢，像一位耐心倾听的晚辈兼专家。
        
        不要使用过于专业的术语，要生活化。
        默认控制在2到3句话，安抚和建议都可以稍微展开一点。
        只有在明显情绪风险较高或老人明确追问时，才放宽到3到4句话。
        不要列点，不要用Markdown。
        """)
        
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
            "content": response.content,
            "action": "comfort",
            "risk_level": "medium" # 心理咨询通常意味着有一定困扰
        }

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

    def _build_anxiety_guidance(self, context: Dict[str, Any]) -> str:
        community_text = self._get_community_activity_text(context)
        return f"您先别急，我陪您缓一缓，先把这口气慢慢顺下来。{community_text}"

    def _get_community_activity_text(self, context: Dict[str, Any]) -> str:
        community_activities = context.get("community_activities") or []
        if isinstance(community_activities, list) and community_activities:
            first = community_activities[0]
            if isinstance(first, dict):
                name = first.get("name", "社区活动")
                time = first.get("time")
                location = first.get("location")
                parts: List[str] = []
                if time:
                    parts.append(time)
                if location:
                    parts.append(location)
                schedule = "，".join(parts)
                if schedule:
                    return f"{schedule}有{name}，俺也去陪您坐坐，跟大家说说话，心里会松一点。"
                return f"社区这会儿有{name}，俺也去陪您坐坐，跟大家说说话，心里会松一点。"
            if isinstance(first, str) and first.strip():
                return f"社区这会儿有{first.strip()}，俺也去陪您坐坐，跟大家说说话，心里会松一点。"

        return "下午社区活动室有聊天和唱歌，俺也去陪您坐坐，跟大家说说话，心里会松一点。"

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
