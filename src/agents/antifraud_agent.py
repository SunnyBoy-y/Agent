from typing import TypedDict, List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END
from src.config import Config
from src.policies.safety_policy import SafetyPolicy
from src.agents.companion_prompt import build_companion_system_prompt
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
        你是小暖的反诈风险识别阶段，只做分类，不给老人回复。
        请分析以下文本，识别是否存在诈骗风险；优先看当前输入，其次看上下文。
        
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
        
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="antifraud_intervention",
                    stage="fraud.pause_and_verify",
                    risk_tier="medium",
                    task="根据诈骗风险分析结果，生成给老人的口语预警和必要的家属/社区消息。",
                    extra_rules=[
                        "action_to_senior 必须短促、有力、直接，但不要羞辱老人。",
                        "Low风险：温柔提醒先停一下、问家里人。",
                        "Medium/High风险：明确让老人不要转账、不要给验证码、不要点链接。",
                        "默认1到2句话，不附带长解释。",
                    ],
                ),
            ),
            (
                "human",
                "输入: {input_text}\n分析: {analysis}\n上下文信息: {context_hint}\n\n输出 JSON (无 Markdown):\n"
                """
        {{
            "action_to_senior": "给老人的口语提醒",
            "action_to_family": "给子女的消息 (若无则null)",
            "action_to_community": "给社区的消息 (仅High风险，否则null)",
            "intervention_type": "Interruption/Warning/Blocking/None"
        }}
                """,
            ),
        ])
        
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

    async def astream_response(self, input_text: str, context: Dict[str, Any] = None):
        result = await self.arun(input_text, context)
        intervention = result.get("intervention", {}) or {}
        analysis = result.get("analysis", {}) or {}
        content = intervention.get("action_to_senior", "")
        risk = self._normalize_risk_level(analysis.get("risk_level", "low"))
        if not content:
            content = await self._generate_missing_intervention(
                input_text=input_text,
                analysis=analysis,
                context=context or {},
                risk=risk,
            )
        response_data = {
            "content": content,
            "action": "warning" if risk != "safe" else "nod",
            "risk_level": risk,
            "family_message": intervention.get("action_to_family"),
            "community_message": intervention.get("action_to_community"),
            "analysis": analysis,
            "intervention": intervention,
        }
        for token in self._chunk_text(content):
            yield {"type": "token", "data": token}
        yield {"type": "done", "data": response_data}

    async def _generate_missing_intervention(
        self,
        *,
        input_text: str,
        analysis: Dict[str, Any],
        context: Dict[str, Any],
        risk: str,
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                build_companion_system_prompt(
                    phase="antifraud_missing_intervention",
                    stage="fraud.pause_and_verify",
                    risk_tier=risk or "medium",
                    task="反诈分析已有结果，但干预话术缺失；重新生成老人端一句短提醒。",
                    extra_rules=[
                        "只输出1句自然中文。",
                        "safe 时只做轻提醒，不制造恐慌。",
                        "low/medium/high 时先让老人暂停转账、验证码、链接等动作，再建议和家人核对。",
                    ],
                ),
            ),
            (
                "human",
                "老人刚才说: {input_text}\n反诈分析: {analysis}\n上下文: {context_hint}\n\n请直接回复老人。",
            ),
        ])
        chain = prompt | self.llm
        try:
            response = await chain.ainvoke({
                "input_text": input_text,
                "analysis": json.dumps(analysis, ensure_ascii=False),
                "context_hint": self._build_context_hint(context),
            })
            return self.safety_policy.sanitize_response(getattr(response, "content", "") or "")
        except Exception:
            return ""

    def _chunk_text(self, text: str, chunk_size: int = 24) -> List[str]:
        value = str(text or "")
        return [value[index:index + chunk_size] for index in range(0, len(value), chunk_size)]

    def _normalize_risk_level(self, risk_level: Any) -> str:
        value = str(risk_level or "").strip().lower()
        if "high" in value:
            return "high"
        if "medium" in value:
            return "medium"
        if "low" in value:
            return "low"
        if "safe" in value:
            return "safe"
        return "low"

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
