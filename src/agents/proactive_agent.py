from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper

class ProactiveAgent:
    def __init__(self):
        self.rag_helper = RAGHelper()

        # 主动关怀节奏：空闲 15 秒后开始触发，之后每隔 15 秒可再次触发一次
        self.idle_threshold_seconds = 15
        self.proactive_interval_seconds = 15

        self.anxiety_keywords = ["焦虑", "心慌", "发慌", "睡不着", "失眠", "烦", "烦躁", "紧张", "担心", "害怕"]
        self.lonely_keywords = ["孤独", "孤单", "空落落", "一个人", "没人说话", "寂寞", "想老伴", "想孩子", "想孙子", "想孙女"]
        self.family_keywords = ["老伴", "儿子", "女儿", "孙子", "孙女", "全家福", "照片", "家里人"]
        self.health_keywords = ["头晕", "腿疼", "胸闷", "咳嗽", "吃药", "不舒服", "血压", "体温"]
        self.interest_keywords = ["听戏", "戏", "京剧", "锁麟囊", "邓丽君", "音乐", "练字", "养花", "下棋"]
        self.daily_life_keywords = ["吃饭", "做饭", "散步", "买菜", "公园", "今天", "刚刚", "忙啥"]

    async def check_and_generate(self) -> Optional[Dict]:
        """
        检查是否需要主动交互，如果需要，生成交互内容
        返回: {
            "content": "生成的问候语",
            "target_agent": "medical_agent" (后续处理该回复的理想 Agent)
        } 或 None
        """
        status = self.rag_helper.get_agent_status()
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

        profile = self.rag_helper.get_user_profile()
        recent_history = self.rag_helper.get_recent_history(limit=12)
        emotion_trend = self.rag_helper.get_emotion_trend()

        strategy = self._select_strategy(profile, recent_history, emotion_trend, status)
        content = self._render_greeting(strategy, profile, status)

        logger.info(f"Proactive Agent selected strategy: {strategy}")

        self.rag_helper.add_memory(
            user_input=f"[系统判定老人沉默 {int(idle_seconds)} 秒，触发主动关怀/{strategy['reason']}]",
            agent_response=content
        )
        self.rag_helper.update_proactive_status(strategy["domain"], content)

        return {
            "content": content,
            "target_agent": f"{strategy['domain']}_agent",
            "scene": strategy["reason"]
        }

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
        status: Dict[str, Any]
    ) -> Dict[str, str]:
        recent_user_text = self._get_recent_user_text(recent_history)
        preferences_text = " ".join(map(str, profile.get("preferences", [])))
        health_text = " ".join(map(str, profile.get("health_condition", [])))
        family_text = " ".join(map(str, profile.get("family_members", [])))

        if self._contains_any(recent_user_text, self.anxiety_keywords) or "情绪波动较大" in emotion_trend or "极度危险" in emotion_trend:
            return {"domain": "mental_health", "reason": "anxiety_support"}

        if self._contains_any(recent_user_text + health_text, self.health_keywords):
            return {"domain": "medical", "reason": "health_check"}

        if self._contains_any(recent_user_text, self.lonely_keywords) or self._contains_any(recent_user_text, self.family_keywords):
            return {"domain": "emotional", "reason": "family_connection"}

        if self._contains_any(recent_user_text + preferences_text, self.interest_keywords):
            return {"domain": "interest", "reason": "interest_followup"}

        if self._contains_any(recent_user_text, self.daily_life_keywords):
            return {"domain": "daily_life", "reason": "daily_life_followup"}

        last_domain = status.get("last_proactive_domain", "")
        if last_domain != "emotional":
            return {"domain": "emotional", "reason": "general_companionship"}
        return {"domain": "daily_life", "reason": "general_checkin"}

    def _render_greeting(self, strategy: Dict[str, str], profile: Dict[str, Any], status: Dict[str, Any]) -> str:
        domain = strategy["domain"]
        reason = strategy["reason"]
        name = profile.get("name", "爷爷/奶奶")
        last_content = status.get("last_proactive_content", "")

        templates = {
            "anxiety_support": [
                f"{name}，这会儿心里还闷不闷？要不我陪您去社区活动室坐坐，听听歌、跟大家说说话。",
                f"{name}，先别一个人闷着了，待会儿咱去社区活动室转转，我陪您散散心。"
            ],
            "family_connection": [
                f"{name}，这会儿是不是有点想家里人了？要不咱翻翻照片，我陪您慢慢看。",
                f"{name}，一个人待着闷不闷？要不要看看家里照片，顺便聊聊孩子们最近怎么样。"
            ],
            "health_check": [
                f"{name}，这会儿身上还舒服不？要不要先喝口水歇一歇，我陪您慢慢缓缓。",
                f"{name}，腿脚和身上这会儿还行不？要不先坐稳歇会儿，我陪您说说话。"
            ],
            "interest_followup": [
                f"{name}，这会儿想不想听段戏或者听首歌？您开口，我陪您一起聊。",
                f"{name}，最近还有没有惦记着那段戏呀？要不咱接着聊两句。"
            ],
            "daily_life_followup": [
                f"{name}，这会儿在忙啥呢？今天吃了啥、做了啥，跟我说两句呀。",
                f"{name}，刚刚忙完没？今天有没有什么小事想跟我唠唠。"
            ],
            "general_companionship": [
                f"{name}，我来陪您唠两句，这会儿在忙啥呢？",
                f"{name}，我来看看您，这会儿还好吗？要不要跟我说两句。"
            ],
            "general_checkin": [
                f"{name}，这会儿手头忙完没？我陪您聊两句。",
                f"{name}，今天过得还顺不顺？跟我说说呀。"
            ],
        }

        candidates = templates.get(reason, templates["general_companionship"])
        for candidate in candidates:
            if candidate != last_content:
                return candidate
        return candidates[0]

    def _get_recent_user_text(self, recent_history: List[Dict[str, Any]]) -> str:
        user_texts = []
        for item in recent_history:
            if item.get("role") != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content.startswith("[系统判定老人沉默"):
                continue
            user_texts.append(content)
        return " ".join(user_texts[-4:])

    def _contains_any(self, text: str, keywords: List[str]) -> bool:
        return any(keyword in text for keyword in keywords)
