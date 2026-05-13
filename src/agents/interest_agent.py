import json
import re
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config import Config
from src.utils.logger import logger
from src.tools.professional_skills import ProfessionalSkills

class InterestAgent:
    MUSIC_REQUEST_PATTERNS = [
        r"(放|播)(一)?首",
        r"来(一)?首",
        r"听(首)?歌",
        r"听(点)?音乐",
        r"播放.*(歌|音乐)",
        r"想听.*(歌|音乐)",
    ]

    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.7 # 较高温度以增加趣味性
        )
        self.music_keywords = ["放一首", "听歌", "放歌", "放音乐", "来首歌", "邓丽君", "音乐", "歌曲", "歌单", "播放"]

    async def arun(self, input_text: str, context: dict = None):
        """
        处理兴趣爱好相关的聊天
        1. 戏曲、书法、园艺等话题深度讨论
        2. 推荐相关内容 (模拟)
        """
        logger.info(f"InterestAgent received: {input_text}")
        context = context or {}
        profile = context.get("user_profile") or {}
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        profile_hint = self._build_profile_hint(profile)

        if self._is_music_request(input_text):
            normalized_query = self._normalize_music_query(input_text)
            music_result = self._trigger_music(normalized_query)
            return {
                "content": "好，我这就给您放上。您先听着，要是想换一首，跟我说一声就行。",
                "action": "play_music",
                "music": bool(music_result.get("trigger_music", True)),
                "music_query": normalized_query,
                "music_result": music_result
            }
        
        prompt = ChatPromptTemplate.from_template("""
        你是一位博学多才、风趣幽默的老友。
        你精通京剧、书法、养花、下棋等老年人喜爱的活动。
        老人画像: {profile_hint}
        最近对话: {recent_history_text}
        
        老人的话题: {input_text}
        
        请针对该兴趣话题进行轻松自然的互动。
        默认说2到3句话，内容稍微展开一点，但别长篇大论。
        可以简单引用一句名段、提一个小技巧，或者夸一句老人的品味。
        语气要像个懂行的老票友或行家。
        
        如果老人明确想细聊，再适当多说一点，但仍然保持自然。
        不要列点，不要用Markdown。
        """)
        
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "input_text": input_text,
            "profile_hint": profile_hint,
            "recent_history_text": recent_history_text
        })
        
        return {
            "content": response.content,
            "action": "recommend_content" # 暗示前端可以展示相关卡片
        }

    def _is_music_request(self, input_text: str) -> bool:
        if any(keyword in input_text for keyword in self.music_keywords):
            return True
        return any(re.search(pattern, input_text) for pattern in self.MUSIC_REQUEST_PATTERNS)

    def _normalize_music_query(self, input_text: str) -> str:
        text = (input_text or "").strip()
        return re.sub(r"[。！？!?,，]+$", "", text) or "来一首舒缓的歌"

    def _trigger_music(self, query: str) -> dict:
        try:
            result = ProfessionalSkills.play_music.invoke({"query": query})
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
        except Exception as exc:
            logger.error(f"Play music skill failed: {exc}")

        return {
            "status": "fallback",
            "trigger_music": True,
            "query": query,
            "intent": "play_music",
            "source": "interest_agent"
        }

    def _build_profile_hint(self, profile: Dict[str, Any]) -> str:
        if not profile:
            return "暂无画像"
        return json.dumps(
            {
                "name": profile.get("name", "未知"),
                "preferences": profile.get("preferences", []),
                "family_members": profile.get("family_members", []),
            },
            ensure_ascii=False
        )
