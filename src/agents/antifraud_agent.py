from typing import TypedDict, List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END
from src.config import Config
from src.policies.safety_policy import SafetyPolicy
import json

# Define the state
class AntiFraudAgentState(TypedDict):
    input_text: str # 实时语音转录文本或消息内容
    context: Dict[str, Any] # 上下文信息 (如对方号码、时间)
    analysis: Dict[str, Any] # risk_level, fraud_type, keywords, confidence
    intervention: Dict[str, Any] # action_to_senior, action_to_family, action_to_community

class AntiFraudAgent:
    def __init__(self, safety_policy: Optional[SafetyPolicy] = None):
        self.safety_policy = safety_policy or SafetyPolicy()
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.0, # Zero temperature for strict classification
            timeout=120,
            max_retries=3
        )
        self.workflow = self._build_workflow()

    def _build_workflow(self):
        workflow = StateGraph(AntiFraudAgentState)

        # Add nodes
        workflow.add_node("analyze_fraud", self.analyze_fraud_node)
        workflow.add_node("generate_intervention", self.generate_intervention_node)

        # Add edges
        workflow.set_entry_point("analyze_fraud")
        workflow.add_edge("analyze_fraud", "generate_intervention")
        workflow.add_edge("generate_intervention", END)

        return workflow.compile()

    def analyze_fraud_node(self, state: AntiFraudAgentState):
        """
        识别诈骗语义与风险分级
        """
        input_text = state["input_text"]
        context = state.get("context", {})
        context_hint = self._build_context_hint(context)
        
        prompt = ChatPromptTemplate.from_template("""
        你是一位空巢老人的“财产安全卫士”。请分析以下文本，识别是否存在诈骗风险。
        
        文本内容: {input_text}
        上下文信息: {context_hint}
        
        请分析并以 JSON 格式输出，不要包含 Markdown 标记：
        {{
            "risk_level": "High (紧急风险) / Medium (确认风险) / Low (疑似风险) / Safe (安全)",
            "fraud_type": "诈骗类型 (如：公检法诈骗、中奖诈骗、亲友求助诈骗、保健品推销、投资理财诈骗、无)",
            "keywords": ["关键词1", "关键词2"],
            "confidence": "0-100 (置信度)",
            "reasoning": "判断理由"
        }}
        
        风险分级标准：
        - High (紧急风险): 明确提到转账、汇款、银行卡号、验证码，且情绪紧迫。
        - Medium (确认风险): 提到“公检法”、“安全账户”、“中奖”、“高收益”，但未涉及具体转账操作。
        - Low (疑似风险): 陌生人推销、索要个人信息、内容可疑但未明确涉及钱财。
        - Safe (安全): 正常家常对话、社区通知等。
        """)
        
        chain = prompt | self.llm | JsonOutputParser()
        result = chain.invoke({
            "input_text": input_text,
            "context_hint": context_hint
        })
        
        return {"analysis": result}

    def generate_intervention_node(self, state: AntiFraudAgentState):
        analysis = state["analysis"]
        input_text = state["input_text"]
        context_hint = self._build_context_hint(state.get("context", {}))
        
        prompt = ChatPromptTemplate.from_template("""
        根据诈骗风险分析结果，生成给老人的口语预警。
        
        输入: {input_text}
        分析: {analysis}
        上下文信息: {context_hint}
        
        要求：
        1. "action_to_senior" 必须是**口语**，短促、有力、直接。
        2. Low风险：温柔提醒（"爷爷，这个听着有点不对劲，咱先挂了问问家里人？"）。
        3. Medium/High风险：严肃警告（"千万别转账！这是骗子！我马上联系您女儿！"）。
        4. 默认控制在1到2句话，不要附带额外解释。
        
        输出 JSON (无 Markdown):
        {{
            "action_to_senior": "给老人的口语提醒",
            "action_to_family": "给子女的消息 (若无则null)",
            "action_to_community": "给社区的消息 (仅High风险，否则null)",
            "intervention_type": "Interruption/Warning/Blocking/None"
        }}
        """)
        
        chain = prompt | self.llm | JsonOutputParser()
        result = chain.invoke({
            "input_text": input_text,
            "analysis": json.dumps(analysis, ensure_ascii=False),
            "context_hint": context_hint
        })
        
        return {"intervention": self._sanitize_intervention(result)}

    def run(self, input_text: str, context: Dict[str, Any] = None):
        if context is None:
            context = {}
        initial_state = {
            "input_text": input_text,
            "context": context,
            "analysis": {},
            "intervention": {}
        }
        return self.workflow.invoke(initial_state)

    async def arun(self, input_text: str, context: Dict[str, Any] = None):
        if context is None:
            context = {}
        initial_state = {
            "input_text": input_text,
            "context": context,
            "analysis": {},
            "intervention": {}
        }
        return await self.workflow.ainvoke(initial_state)

    def _build_context_hint(self, context: Dict[str, Any]) -> str:
        if not context:
            return "暂无上下文"
        profile = context.get("user_profile") or {}
        return json.dumps(
            {
                "user_profile": {
                    "name": profile.get("name", "未知"),
                    "family_members": profile.get("family_members", []),
                    "health_condition": profile.get("health_condition", []),
                },
                "recent_history_text": context.get("recent_history_text", ""),
                "visual_analysis": context.get("visual_analysis", {}),
            },
            ensure_ascii=False
        )

    def _sanitize_intervention(self, intervention: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the shared client-facing safety policy to anti-fraud output."""
        if not isinstance(intervention, dict):
            return {}

        cleaned = dict(intervention)
        for field in ("action_to_senior", "action_to_family", "action_to_community"):
            value = cleaned.get(field)
            if isinstance(value, str) and value.strip():
                cleaned[field] = self.safety_policy.sanitize_response(value)
        return cleaned
