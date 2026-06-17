import json
import re
import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config import Config
from src.utils.logger import logger
from src.tools.professional_skills import ProfessionalSkills
from src.agents.companion_prompt import build_companion_system_prompt, stage_from_context

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

    def _merge_memory_context(self, recent_history_text: str, memory_context: Any) -> str:
        memory_text = str(memory_context or "").strip()
        recent_text = str(recent_history_text or "").strip()
        if not memory_text or memory_text in recent_text:
            return recent_text
        return f"{recent_text}\n\n补充记忆:\n{memory_text}"

    async def astream_response(self, input_text: str, context: dict = None):
        context = context or {}
        profile = context.get("user_profile") or {}
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        recent_history_text = self._merge_memory_context(recent_history_text, context.get("memory_context"))
        profile_hint = self._build_profile_hint(profile)
        response_data = {"content": "", "action": "recommend_content"}

        if self._is_music_request(input_text):
            normalized_query = self._normalize_music_query(input_text)
            music_result = self._trigger_music(
                normalized_query,
                elder_user_id=context.get("user_id"),
            )
            response_data.update({
                "action": "play_music",
                "music": bool(music_result.get("trigger_music", True)),
                "music_query": normalized_query,
                "music_result": music_result,
            })

            async def emit_music(token: str) -> None:
                if token:
                    response_data["content"] += token
                    emitted.append(token)

            emitted: List[str] = []
            post_reply_task = asyncio.create_task(self._generate_music_post_reply(
                input_text=input_text,
                normalized_query=normalized_query,
                music_result=music_result,
                profile_hint=profile_hint,
                recent_history_text=recent_history_text,
            ))
            task = asyncio.create_task(self._generate_music_reply(
                input_text=input_text,
                normalized_query=normalized_query,
                music_result=music_result,
                profile_hint=profile_hint,
                recent_history_text=recent_history_text,
                on_token=emit_music,
            ))
            while not task.done() or emitted:
                while emitted:
                    yield {"type": "token", "data": emitted.pop(0)}
                if not task.done():
                    await asyncio.sleep(0)
            task.result()
            music_result["post_reply"] = await post_reply_task
            yield {"type": "done", "data": response_data}
            return

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="interest_stream",
                    stage=stage_from_context(context, "interest.music"),
                    risk_tier="safe",
                    task="围绕音乐、照片、戏曲、书画、园艺等兴趣陪老人自然聊下去。",
                    extra_rules=[
                        "有懂行的生活味，但不要显摆知识。",
                        "老人点歌或想看回忆时，先顺着体验，不长篇解释。",
                    ],
                ),
            ),
            (
                "human",
                "用户资料: {profile_hint}\n最近对话: {recent_history_text}\n用户说: {input_text}\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        async for chunk in chain.astream({
            "input_text": input_text,
            "profile_hint": profile_hint,
            "recent_history_text": recent_history_text,
        }):
            token = getattr(chunk, "content", "") or ""
            if token:
                response_data["content"] += token
                yield {"type": "token", "data": token}

        yield {"type": "done", "data": response_data}

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
        recent_history_text = self._merge_memory_context(recent_history_text, context.get("memory_context"))
        profile_hint = self._build_profile_hint(profile)

        if self._is_music_request(input_text):
            normalized_query = self._normalize_music_query(input_text)
            music_result = self._trigger_music(
                normalized_query,
                elder_user_id=context.get("user_id"),
            )
            post_reply_task = asyncio.create_task(self._generate_music_post_reply(
                input_text=input_text,
                normalized_query=normalized_query,
                music_result=music_result,
                profile_hint=profile_hint,
                recent_history_text=recent_history_text,
            ))
            content = await self._generate_music_reply(
                input_text=input_text,
                normalized_query=normalized_query,
                music_result=music_result,
                profile_hint=profile_hint,
                recent_history_text=recent_history_text,
            )
            music_result["post_reply"] = await post_reply_task
            return {
                "content": content,
                "action": "play_music",
                "music": bool(music_result.get("trigger_music", True)),
                "music_query": normalized_query,
                "music_result": music_result
            }
        
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="interest_agent",
                    stage=stage_from_context(context, "interest.music"),
                    risk_tier="safe",
                    task="用兴趣爱好陪老人展开轻松互动，保留小暖的温和人格。",
                    extra_rules=[
                        "可以有一点懂戏、懂歌、懂生活的趣味，但不要卖弄。",
                        "默认2到3句话；老人明确想细聊时再稍微展开。",
                        "不要列点，不要用Markdown。",
                    ],
                ),
            ),
            (
                "human",
                "老人画像: {profile_hint}\n最近对话: {recent_history_text}\n\n老人的话题: {input_text}\n\n请直接回复老人。",
            ),
        ])
        
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

    def _trigger_music(self, query: str, elder_user_id: str = None) -> dict:
        try:
            payload = {"query": query}
            if elder_user_id:
                payload["elder_user_id"] = elder_user_id
            result = ProfessionalSkills.play_music.invoke(payload)
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

    async def _generate_music_reply(
        self,
        *,
        input_text: str,
        normalized_query: str,
        music_result: Dict[str, Any],
        profile_hint: str,
        recent_history_text: str,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="music_action_reply",
                    stage="interest.music",
                    risk_tier="safe",
                    task="把点歌处理结果转成小暖对老人说的一句话或两句话。",
                    extra_rules=[
                        "点歌成功时自然确认已安排；点歌模糊时顺着老人想听的感觉回应。",
                        "不要解释工具或播放系统。",
                    ],
                ),
            ),
            (
                "human",
                "用户画像：{profile_hint}\n最近对话：\n{recent_history_text}\n\n用户刚才说：{input_text}\n识别出的点歌需求：{normalized_query}\n点歌处理结果：{music_result}",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "input_text": input_text,
            "normalized_query": normalized_query,
            "music_result": json.dumps(music_result, ensure_ascii=False),
            "profile_hint": profile_hint,
            "recent_history_text": recent_history_text,
        }
        if on_token:
            content = ""
            async for chunk in chain.astream(payload):
                token = getattr(chunk, "content", "") or ""
                if token:
                    content += token
                    await on_token(token)
            return content
        response = await chain.ainvoke(payload)
        return response.content

    async def _generate_music_post_reply(
        self,
        *,
        input_text: str,
        normalized_query: str,
        music_result: Dict[str, Any],
        profile_hint: str,
        recent_history_text: str,
    ) -> str:
        if not hasattr(self, "llm"):
            return ""
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="music_post_reply",
                    stage="interest.music",
                    risk_tier="safe",
                    task="写音乐结束后前端立刻播放的一句中文短句。",
                    extra_rules=[
                        "只输出一句，不要Markdown和JSON。",
                        "要承接这首歌带来的情绪，不用固定模板。",
                        "最多问一个很轻的问题，不重复称呼老人名字。",
                    ],
                ),
            ),
            (
                "human",
                "Profile: {profile_hint}\nRecent dialogue: {recent_history_text}\nUser request: {input_text}\nMusic query: {normalized_query}\nMusic result: {music_result}",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "input_text": input_text,
            "normalized_query": normalized_query,
            "music_result": json.dumps(music_result, ensure_ascii=False),
            "profile_hint": profile_hint,
            "recent_history_text": recent_history_text,
        }
        try:
            response = await chain.ainvoke(payload)
            content = str(getattr(response, "content", "") or "").strip()
            return content
        except Exception as exc:
            logger.warning(f"Music post-reply generation failed: {exc}")
            return ""
