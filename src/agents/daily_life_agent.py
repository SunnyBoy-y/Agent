import json
import os
import asyncio
from datetime import datetime
from typing import Awaitable, Callable, Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import Config
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper
from src.agents.companion_prompt import build_companion_system_prompt

class DailyLifeAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.3
        )
        self.rag_helper = RAGHelper()

    def _merge_memory_context(self, recent_history_text: str, memory_context: Any) -> str:
        memory_text = str(memory_context or "").strip()
        recent_text = str(recent_history_text or "").strip()
        if not memory_text or memory_text in recent_text:
            return recent_text
        return f"{recent_text}\n\n补充记忆:\n{memory_text}"

    async def astream_response(self, input_text: str, context: Dict[str, Any] = None):
        context = context or {}
        profile = context.get("user_profile") or self.rag_helper.get_user_profile()
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        recent_history_text = self._merge_memory_context(recent_history_text, context.get("memory_context"))
        response_data = {"content": "", "action": "none"}

        async def emit(token: str) -> None:
            if token:
                response_data["content"] += token
                yield_item = {"type": "token", "data": token}
                emitted.append(yield_item)

        emitted: List[Dict[str, Any]] = []
        intent_analysis = await self._analyze_intent(input_text)

        async def drain_generated(awaitable):
            task = asyncio.create_task(awaitable)
            while not task.done() or emitted:
                while emitted:
                    yield emitted.pop(0)
                if not task.done():
                    await asyncio.sleep(0)
            task.result()

        if intent_analysis.get("intent") == "log_event":
            event = intent_analysis.get("event")
            if event:
                await asyncio.to_thread(self.rag_helper.add_daily_event, event)
                async for item in drain_generated(self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result=f"已记录：{event}",
                    reply_goal="温和确认记录完成。",
                    on_token=emit,
                )):
                    yield item
                response_data["action"] = "save_log"
            else:
                async for item in drain_generated(self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result="没有识别到明确的日常事件。",
                    reply_goal="请用户换个说法。",
                    on_token=emit,
                )):
                    yield item
        elif intent_analysis.get("intent") == "query_event":
            events = await asyncio.to_thread(self.rag_helper.search_daily_events, input_text, k=3)
            if events:
                async for item in drain_generated(self._generate_summary(
                    input_text,
                    events,
                    profile,
                    recent_history_text,
                    on_token=emit,
                )):
                    yield item
            else:
                async for item in drain_generated(self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result="没有找到相关日常记录。",
                    reply_goal="自然说明没有找到，并鼓励继续记录。",
                    on_token=emit,
                )):
                    yield item
        else:
            async for item in drain_generated(self._generate_chat_reply(
                input_text,
                profile,
                recent_history_text,
                on_token=emit,
            )):
                yield item

        yield {"type": "done", "data": response_data}

    async def arun(self, input_text: str, context: Dict[str, Any] = None):
        """
        处理日常生活记录、查询、事件管理
        1. 记录日常活动 (如做饭、散步、访客)
        2. 查询历史活动 (支持语义检索)
        """
        logger.info(f"DailyLifeAgent received: {input_text}")
        context = context or {}
        profile = context.get("user_profile") or self.rag_helper.get_user_profile()
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        recent_history_text = self._merge_memory_context(recent_history_text, context.get("memory_context"))
        
        # 1. 分析意图：记录还是查询？
        intent_analysis = await self._analyze_intent(input_text)
        
        response_data = {
            "content": "",
            "action": "none"
        }

        if intent_analysis.get("intent") == "log_event":
            event = intent_analysis.get("event")
            if event:
                # 异步写入向量库
                await asyncio.to_thread(self.rag_helper.add_daily_event, event)
                response_data["content"] = await self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result=f"已记录事件：{event}",
                    reply_goal="自然确认已经记下这件事，可顺着用户的话轻轻接一句。",
                )
                response_data["action"] = "save_log"
            else:
                response_data["content"] = await self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result="未能从用户输入中提取出可记录的具体事件。",
                    reply_goal="说明还没听清，并邀请用户再补充一点关键信息。",
                )
        
        elif intent_analysis.get("intent") == "query_event":
            # 查询 (语义检索)
            query_text = input_text
            # 如果意图分析提取了更具体的查询词，也可以用
            # 但通常直接用用户的原始输入去检索效果也不错，或者提取关键实体
            
            # 异步检索
            events = await asyncio.to_thread(self.rag_helper.search_daily_events, query_text, k=3)
            
            if events:
                # 让 LLM 基于检索结果生成回答
                summary = await self._generate_summary(query_text, events, profile, recent_history_text)
                response_data["content"] = summary
            else:
                response_data["content"] = await self._generate_task_reply(
                    input_text=input_text,
                    profile=profile,
                    recent_history_text=recent_history_text,
                    task_result="没有检索到相关生活记录。",
                    reply_goal="坦诚说明暂时没找到对应记录，并自然地请用户补充线索。",
                )
        
        else:
            # 闲聊/其他
            response_data["content"] = await self._generate_chat_reply(
                input_text,
                profile,
                recent_history_text,
            )

        return response_data

    async def _analyze_intent(self, text: str) -> Dict:
        prompt = ChatPromptTemplate.from_template("""
        你是小暖的生活记录意图识别阶段，只做分类，不给老人回复。
        判断老人是在记录事情、查询过去发生的事，还是普通闲聊。
        只抽取老人明确说出的事实，不补全、不猜测。
        输入: {text}
        
        输出 JSON (无 Markdown):
        {{
            "intent": "log_event (记录) / query_event (查询) / chat (闲聊)",
            "event": "提取的事件内容 (如去公园散步、吃了饺子)，仅 log_event 需要"
        }}
        """)
        chain = prompt | self.llm | JsonOutputParser()
        return await chain.ainvoke({"text": text})

    async def _generate_summary(self, query: str, events: List[str], profile: Dict[str, Any], recent_history_text: str, on_token: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
        """基于检索到的事件生成回答"""
        events_str = "\n".join(events)
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="daily_life_summary",
                    stage="daily_life.record_or_recall",
                    risk_tier="safe",
                    task="根据已记录的生活事件回答老人，并保持小暖的稳定人格。",
                    extra_rules=[
                        "只依据检索到的记录回答；记录不明确就直接说没查到。",
                        "不要把系统摘要伪装成回忆；不要编造日期、地点、人物。",
                    ],
                ),
            ),
            (
                "human",
                "老人问: {query}\n老人画像: {profile}\n最近对话: {recent_history_text}\n\n系统检索到的相关记录:\n{events_str}\n\n请按老人的语气自然回答。",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "query": query,
            "events_str": events_str,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text
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

    async def _generate_chat_reply(
        self,
        input_text: str,
        profile: Dict[str, Any],
        recent_history_text: str,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="daily_life_chat",
                    stage="companionship",
                    risk_tier="safe",
                    task="把日常对话接得自然一点，像小暖本人在轻轻陪着老人聊天。",
                    extra_rules=[
                        "优先回应内容本身，不要一上来做任务总结。",
                        "如果老人是在记录事情，也可以自然地帮他收一收话。",
                    ],
                ),
            ),
            (
                "human",
                "用户画像：{profile}\n最近对话：\n{recent_history_text}\n\n用户刚才说：{input_text}\n\n请直接回答这句话。",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "input_text": input_text,
            "profile": json.dumps(profile, ensure_ascii=False),
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

    async def _generate_task_reply(
        self,
        *,
        input_text: str,
        profile: Dict[str, Any],
        recent_history_text: str,
        task_result: str,
        reply_goal: str,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="daily_life_task",
                    stage="daily_life.record_or_recall",
                    risk_tier="safe",
                    task="把记录结果转成老人听得懂、愿意接着说的话。",
                    extra_rules=[
                        "基于处理结果回答，不要照抄系统描述。",
                        "如果没识别清楚，就坦诚说明并请老人补一句。",
                    ],
                ),
            ),
            (
                "human",
                "用户画像：{profile}\n最近对话：\n{recent_history_text}\n\n用户刚才说：{input_text}\n系统处理结果：{task_result}\n回复目标：{reply_goal}",
            ),
        ])
        chain = prompt | self.llm
        payload = {
            "input_text": input_text,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text,
            "task_result": task_result,
            "reply_goal": reply_goal,
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
