import re
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config import Config
from src.utils.logger import logger

class RouterAgent:
    MUSIC_REQUEST_PATTERNS = [
        r"(放|播)(一)?首",
        r"来(一)?首",
        r"听(首)?歌",
        r"听(点)?音乐",
        r"播放.*(歌|音乐)",
        r"想听.*(歌|音乐)",
    ]
    MUSIC_KEYWORDS = [
        "放歌",
        "放音乐",
        "播歌",
        "播放歌曲",
        "音乐",
        "歌曲",
        "歌单",
        "唱片",
        "邓丽君",
    ]

    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.0 # 路由需要极其精确
        )
        self.agents = [
            "medical_agent",
            "daily_life_agent",
            "interest_agent",
            "mental_health_agent",
            "antifraud_agent",
            "emotional_agent" # 默认/闲聊/综合
        ]

    async def route(self, input_text: str, context: Dict[str, Any] = None) -> str:
        """
        分析用户意图，选择最合适的智能体（异步版本，保留 LLM 路由兜底）
        """
        logger.info(f"RouterAgent routing: {input_text}")
        context = context or {}
        rule_based_route = self._route_by_rules(input_text)
        if rule_based_route:
            logger.info(f"Rule-based route selected: {rule_based_route}")
            return rule_based_route

        # 理论上不会到达此处（_route_by_rules 永远有返回值）
        # 保留 LLM 路由作为兜底
        return await self._llm_route(input_text, context)

    def route_sync(self, input_text: str) -> str:
        """
        同步路由：纯规则匹配，不调 LLM。延迟 < 1ms。
        覆盖 95%+ 的用户输入。
        """
        return self._route_by_rules(input_text)

    def _route_by_rules(self, input_text: str) -> str:
        text = (input_text or "").strip()
        if not text:
            return "emotional_agent"

        # === 必须走专家的硬规则 ===
        emergency_keywords = ["救命", "摔倒", "跌倒", "起不来", "胸口疼", "胸闷", "喘不上气", "呼吸困难", "快不行了"]
        if any(keyword in text for keyword in emergency_keywords):
            return "medical_agent"
        if "药" in text or "疼" in text or "晕" in text:
            return "medical_agent"

        if self._is_music_request(text):
            return "interest_agent"

        # 诈骗关键词：仍需 LLM 确认（避免误判），但强信号直接给 antifraud
        strong_fraud_kw = ["中奖了", "法院传票", "公安局打电话", "让我转账"]
        if any(kw in text for kw in strong_fraud_kw):
            return "antifraud_agent"

        # === 明确需要特定智能体的信号 ===
        # 生活记录（吃了啥/干了啥/去了哪）
        daily_patterns = [
            r"我今天吃了", r"我刚吃了", r"中午吃了", r"晚上吃了", r"早上吃了",
            r"我今天去了", r"我刚去了", r"去了公园", r"去了超市", r"去了菜市场",
        ]
        if any(re.search(p, text) for p in daily_patterns):
            return "daily_life_agent"

        # === 其他所有情况：直接走 emotional_agent，不再调 LLM 路由 ===
        # emotional_agent 是默认综合智能体，覆盖：
        #   闲聊、情感陪伴、想念亲人、日常问候、查看照片/视频、心理倾诉
        # 这样每次请求省掉一次 LLM 调用（省 1-2s 首字延迟）
        return "emotional_agent"

    def _is_music_request(self, input_text: str) -> bool:
        if any(keyword in input_text for keyword in self.MUSIC_KEYWORDS):
            return True
        return any(re.search(pattern, input_text) for pattern in self.MUSIC_REQUEST_PATTERNS)

    async def _llm_route(self, input_text: str, context: Dict[str, Any]) -> str:
        """LLM 路由兜底（仅在规则无法判定时调用）"""
        context_hint = self._build_context_hint(context)
        prompt = ChatPromptTemplate.from_template("""
        你是一个智能体路由系统。请分析老人的输入，选择一个最合适的处理专员。

        可选专员:
        - medical_agent: 身体健康、吃药提醒、身体不适、看病就医。
        - daily_life_agent: 记录生活琐事（如吃了啥、去了哪）、查询过去做过的事。
        - interest_agent: 讨论兴趣爱好（戏曲、书法、园艺、下棋、广场舞等）。
        - mental_health_agent: 表达孤独、焦虑、抑郁、想找人倾诉心理问题。
        - antifraud_agent: 涉及钱财、中奖、公检法、转账、陌生人电话、怀疑被骗。
        - emotional_agent: 普通闲聊、情感陪伴、想念亲人、日常问候、查看照片/视频、无法归类。

        老人说: {input_text}
        当前上下文:
        {context_hint}

        请仅输出专员名称 (如 medical_agent)，不要有任何解释或标点。
        """)
        try:
            chain = prompt | self.llm
            response = await chain.ainvoke({
                "input_text": input_text,
                "context_hint": context_hint
            })
            selected_agent = response.content.strip()
            if selected_agent not in self.agents:
                logger.warning(f"Router selected unknown agent: {selected_agent}, falling back to emotional_agent")
                return "emotional_agent"
            logger.info(f"LLM routed to: {selected_agent}")
            return selected_agent
        except Exception as e:
            logger.error(f"LLM routing failed: {e}")
            return "emotional_agent"

    def _build_context_hint(self, context: Dict[str, Any]) -> str:
        if not context:
            return "暂无额外上下文"

        profile = context.get("user_profile") or {}
        visual = context.get("visual_analysis") or {}
        parts = [
            f"画像姓名: {profile.get('name', '未知')}",
            f"健康情况: {profile.get('health_condition', []) or '暂无'}",
            f"兴趣偏好: {profile.get('preferences', []) or '暂无'}",
            f"家庭成员: {profile.get('family_members', []) or '暂无'}",
            f"视觉情绪: {visual.get('emotion', 'unknown')}",
            f"语音转文字: {context.get('audio_transcript', '') or '无'}",
            f"最近对话: {context.get('recent_history_text', '') or '暂无'}",
        ]
        return "\n".join(parts)
