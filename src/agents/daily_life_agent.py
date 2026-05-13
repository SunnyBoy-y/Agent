import json
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, List
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import Config
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper

class DailyLifeAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.3
        )
        self.rag_helper = RAGHelper()

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
        profile_name = profile.get("name", "您")
        
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
                response_data["content"] = f"{profile_name}，好，给您记下了，今天{event}。"
                response_data["action"] = "save_log"
            else:
                response_data["content"] = "我刚才没听太清，您再说一遍，我给您记下来。"
        
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
                response_data["content"] = "这事我一时没想起来，您再提醒我一句？"
        
        else:
            # 闲聊/其他
            response_data["content"] = "挺好呀，您接着说，我听着呢。"

        return response_data

    async def _analyze_intent(self, text: str) -> Dict:
        prompt = ChatPromptTemplate.from_template("""
        你是一个生活记录助手。请分析老人的输入，判断是想记录事情，还是想查询过去发生的事。
        输入: {text}
        
        输出 JSON (无 Markdown):
        {{
            "intent": "log_event (记录) / query_event (查询) / chat (闲聊)",
            "event": "提取的事件内容 (如去公园散步、吃了饺子)，仅 log_event 需要"
        }}
        """)
        chain = prompt | self.llm | JsonOutputParser()
        return await chain.ainvoke({"text": text})

    async def _generate_summary(self, query: str, events: List[str], profile: Dict[str, Any], recent_history_text: str) -> str:
        """基于检索到的事件生成回答"""
        events_str = "\n".join(events)
        prompt = ChatPromptTemplate.from_template("""
        老人问: {query}
        老人画像: {profile}
        最近对话: {recent_history_text}
        
        系统检索到的相关记录:
        {events_str}
        
        请根据记录回答老人的问题。语气亲切、自然，像家人一样。
        默认回答2到3句话，可以补一句贴心的话，但不要展开成长段说明。
        如果记录里没有明确答案，就自然地说印象不深了，再顺着问一句或接一句。
        """)
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "query": query,
            "events_str": events_str,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text
        })
        return response.content
