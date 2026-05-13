from typing import Dict, Any, List
import json
import os
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import Config
from src.utils.logger import logger
from src.utils.rag_helper import RAGHelper
from src.tools.professional_skills import ProfessionalSkills

class MedicalAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.1
        )
        self.rag_helper = RAGHelper()
        self.emergency_keywords = ["救命", "摔倒", "跌倒", "起不来", "胸口疼", "胸闷", "喘不上气", "呼吸困难", "快不行了"]

    async def arun(self, input_text: str, context: Dict[str, Any] = None):
        """
        处理医药康复相关的请求
        1. 提醒吃药 (基于时间/画像)
        2. 健康咨询回答
        3. 身体不适预警
        """
        logger.info(f"MedicalAgent received: {input_text}")
        context = context or {}
        profile = context.get("user_profile") or self.rag_helper.get_user_profile()
        recent_history_text = context.get("recent_history_text", "暂无最近对话")
        memory_context = context.get("memory_context", "")

        if self._is_emergency_phrase(input_text):
            return self._build_emergency_response(input_text, "high")
        
        # 1. 分析意图与健康状况
        analysis = await self._analyze_health_intent(input_text)
        
        response_data = {
            "content": "",
            "action": "none", # alert_family, call_doctor, none
            "risk_level": "safe"
        }

        if analysis.get("is_emergency"):
            return self._build_emergency_response(input_text, "high")
        
        elif analysis.get("intent") == "medication_query":
            # 查询用药计划
            meds = profile.get("medications", [])
            if not meds:
                response_data["content"] = "我这边还没记到您的用药。要是新开的药，您告诉我名字和用法，我帮您记上。"
            else:
                med_list = ", ".join([f"{m['name']} ({m['time']})" for m in meds])
                response_data["content"] = f"按记录，您现在要吃的是{med_list}。您吃了吗？"
                
        elif analysis.get("intent") == "symptom_report":
            # 记录症状并给出建议
            symptom = analysis.get("symptom", "未知不适")
            symptom_text = self._format_symptom(symptom)
            response_data["content"] = f"知道了，您这是{symptom_text}。先歇一会儿，量量血压或体温；要是还难受，我帮您联系家里人。"
            response_data["risk_level"] = "medium"
            # 更新画像中的健康状况
            self.rag_helper.update_user_profile("health_condition", symptom)
            
        else:
            # 一般健康咨询
            response_data["content"] = await self._generate_health_advice(
                input_text,
                profile,
                recent_history_text,
                memory_context
            )

        return response_data

    async def _analyze_health_intent(self, text: str) -> Dict:
        prompt = ChatPromptTemplate.from_template("""
        你是一个医疗助手。请分析老人的输入，提取意图和关键信息。
        输入: {text}
        
        输出 JSON (无 Markdown):
        {{
            "intent": "medication_query (问药) / symptom_report (报病) / general_query (咨询) / other",
            "is_emergency": true/false (是否紧急，如“救命，我摔倒了”“我胸口疼得厉害”“我喘不上气了”),
            "symptom": "提取的症状 (如头晕、腿疼)，无则 null"
        }}
        """)
        chain = prompt | self.llm | JsonOutputParser()
        return await chain.ainvoke({"text": text})

    async def _generate_health_advice(self, text: str, profile: Dict[str, Any], recent_history_text: str, memory_context: str) -> str:
        prompt = ChatPromptTemplate.from_template("""
        你是家庭医生助手。请用**口语**回答老人的健康问题。
        老人画像: {profile}
        最近对话: {recent_history_text}
        补充记忆: {memory_context}

        要求：
        1. 默认用2到3句话回答，说明可以稍微完整一点，最多不超过4句话。
        2. 专业但通俗（不要拽术语）。
        3. 语气亲切、关怀。
        4. 不要列点，不要用Markdown，直接说。
        
        问题: {text}
        回答:
        """)
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "text": text,
            "profile": json.dumps(profile, ensure_ascii=False),
            "recent_history_text": recent_history_text,
            "memory_context": memory_context or "暂无"
        })
        return response.content

    def _format_symptom(self, symptom: Any) -> str:
        if isinstance(symptom, list):
            items = [str(item).strip() for item in symptom if str(item).strip()]
            if not items:
                return "有点不舒服"
            return "、".join(items)

        if symptom is None:
            return "有点不舒服"

        symptom_text = str(symptom).strip()
        return symptom_text or "有点不舒服"

    def _is_emergency_phrase(self, text: str) -> bool:
        return any(keyword in text for keyword in self.emergency_keywords)

    def _build_emergency_response(self, input_text: str, level: str) -> Dict[str, Any]:
        tool_output = self._trigger_emergency_contact(input_text, level)
        logger.warning(f"EMERGENCY ALERT: {input_text}")
        return {
            "content": "您先别慌，我已经马上联系家里人了。您先尽量别乱动，慢慢呼吸，我陪着您。",
            "action": "alert_family",
            "risk_level": "high",
            "sos": bool(tool_output.get("trigger_sos", True)),
            "emergency_contact_result": tool_output
        }

    def _trigger_emergency_contact(self, reason: str, level: str) -> Dict[str, Any]:
        try:
            result = ProfessionalSkills.emergency_contact.invoke({
                "reason": reason,
                "level": level
            })
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
        except Exception as exc:
            logger.error(f"Emergency contact skill failed: {exc}")

        return {
            "status": "fallback",
            "trigger_sos": True,
            "level": level,
            "reason": reason,
            "actions": []
        }

    def check_medication_reminder(self):
        """
        定时任务调用的方法 (需外部调度器支持)
        检查当前时间是否需要提醒吃药
        """
        # 简单示例逻辑
        now_hour = datetime.now().hour
        meds = self.rag_helper.get_user_profile().get("medications", [])
        reminders = []
        for med in meds:
            # 假设 med['time'] 是 "08:00" 格式
            if str(now_hour) in med.get('time', ''):
                reminders.append(med['name'])
        
        if reminders:
            return f"爷爷/奶奶，到时间吃药啦：{', '.join(reminders)}。温水送服哦！"
        return None
