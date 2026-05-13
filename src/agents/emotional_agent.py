from typing import Dict, Any, List, Optional, TypedDict
import operator
import json
import asyncio
import re
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
try:
    from langchain_core.pydantic_v1 import BaseModel, Field
except ImportError:
    from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from src.config import Config
from src.utils.rag_helper import RAGHelper
from src.tools.professional_skills import ProfessionalSkills
from src.utils.logger import logger

# 定义 Agent 状态
class AgentState(TypedDict):
    input_text: str
    voice_text: Optional[str]
    voice_emotion: Optional[str]
    visual_emotion: Optional[Dict[str, Any]]
    session_context: Optional[Dict[str, Any]]
    history: List[BaseMessage]
    context: str
    response: str
    messages: List[BaseMessage] # 用于 ReAct 模式的消息列表
    force_photo_tool: bool
    forced_photo_keyword: str
    photo_tool_ran: bool

# 定义情感状态更新工具结构
class EmotionalStateUpdate(BaseModel):
    """更新数字人的情感状态、动作及风险等级。这应该作为最终回复的一部分调用。"""
    expression: str = Field(..., description="数字人表情指令，可选值：happy, sad, concerned, surprised, neutral, angry")
    action: str = Field(..., description="数字人动作指令，可选值：nod(点头), shake_head(摇头), wave(招手), clench_fist(握拳), none")
    risk_level: str = Field(..., description="心理风险等级，可选值：safe, low, medium, high (high表示有自杀/自残/极度抑郁倾向)")
    profile_update: Optional[Dict[str, Any]] = Field(None, description="需要更新的用户画像字段，如 {'health_condition': '老寒腿'}")

class EmotionalConnectionAgent:
    def __init__(self):
        self.rag_helper = RAGHelper()
        
        # 绑定工具 (包含业务工具和元数据更新工具)
        self.business_tools = [
            ProfessionalSkills.search_family_photos,
            ProfessionalSkills.emergency_contact,
            ProfessionalSkills.record_health_complaint
        ]
        
        # 将 EmotionalStateUpdate 也绑定给 LLM，但标记它不是普通的业务工具
        # 在我们的逻辑中，业务工具会进入 'tools' 节点执行，而 EmotionalStateUpdate 只是元数据载体
        all_tools = self.business_tools + [EmotionalStateUpdate]
        
        # 初始化单一 LLM
        self.llm = ChatOpenAI(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model_name=Config.MODEL_NAME,
            temperature=0.3, # 稍微增加一点温度，兼顾创造性和工具调用的准确性
            timeout=120,
            max_retries=3,
        ).bind_tools(all_tools)
        
        self.workflow = self._build_workflow()

    def _strip_parenthetical_text(self, text: str) -> str:
        if not text:
            return ""

        result = []
        depth = 0
        for char in text:
            if char in ["(", "（"]:
                depth += 1
                continue
            if char in [")", "）"]:
                if depth > 0:
                    depth -= 1
                continue
            if depth == 0:
                result.append(char)

        cleaned = "".join(result)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _build_fallback_emotional_update(self, input_text: str, content: str) -> Dict[str, Any]:
        text = f"{input_text} {content}"

        # 紧急/生命危险
        if any(keyword in text for keyword in ["救命", "摔倒", "跌倒", "喘不上气", "胸口疼", "胸闷"]):
            return {"expression": "concerned", "action": "none", "risk_level": "high"}

        # 双相倾向 — 高涨期信号（话多跳跃、精力过剩、冲动）
        if any(keyword in text for keyword in [
            "我最近特别有劲儿", "一夜没睡也不困", "我有好多计划", "我能干大事",
            "停不下来", "脑子里想法一个接一个", "花钱花多了"
        ]):
            return {"expression": "surprised", "action": "shake_head", "risk_level": "medium"}

        # 抑郁倾向 — 低落/空虚/无价值感
        if any(keyword in text for keyword in [
            "活着没意思", "不想活了", "没人在乎我", "我是个累赘", "拖累",
            "做什么都没用", "起不来床", "不想动", "没力气", "吃不下",
            "睡不好", "整夜睡不着", "什么都不想做", "空落落的"
        ]):
            risk = "high" if any(kw in text for kw in ["活着没意思", "不想活了", "没人在乎我"]) else "medium"
            return {"expression": "sad", "action": "nod", "risk_level": risk}

        # 焦虑倾向 — 过度担心/紧张/不安
        if any(keyword in text for keyword in [
            "老是担心", "万一", "会不会出事", "心里发慌", "坐立不安",
            "心慌", "紧张", "怕", "不敢", "总是想", "停不下来想",
            "睡不着觉", "翻来覆去", "一直在想"
        ]):
            return {"expression": "concerned", "action": "nod", "risk_level": "medium"}

        # 一般思念/孤单
        if any(keyword in text for keyword in ["想老伴", "想他", "难过", "伤心", "孤单", "孤独"]):
            return {"expression": "sad", "action": "nod", "risk_level": "medium"}

        # 身体不适
        if any(keyword in text for keyword in ["腿疼", "头晕", "不舒服", "难受", "疼"]):
            return {"expression": "concerned", "action": "nod", "risk_level": "medium"}

        # 开心/回忆
        if any(keyword in text for keyword in ["照片", "孙子", "孙女", "全家福", "回忆"]):
            return {"expression": "happy", "action": "wave", "risk_level": "low"}

        return {"expression": "neutral", "action": "nod", "risk_level": "low"}

    def _ensure_emotional_update(self, message: Any, input_text: str) -> None:
        tool_calls = list(getattr(message, "tool_calls", []) or [])
        has_update = any(
            isinstance(tc, dict) and tc.get("name") == "EmotionalStateUpdate"
            for tc in tool_calls
        )
        if has_update:
            return

        fallback_args = self._build_fallback_emotional_update(input_text, getattr(message, "content", ""))
        fallback_call = {
            "name": "EmotionalStateUpdate",
            "args": fallback_args,
            "id": "emotional_fallback"
        }
        try:
            message.tool_calls = tool_calls + [fallback_call]
        except Exception:
            pass

    def _detect_mental_health_tendency(self, profile: Dict[str, Any], emotion_trend: str) -> str:
        """从用户画像和情感趋势中检测心理健康倾向"""
        # 1. 先检查用户画像中是否已有明确标记
        mh = profile.get("mental_health_tendency", "")
        if mh in ("anxiety", "depression", "bipolar"):
            return mh

        # 2. 从 health_condition 中推断
        conditions = " ".join([
            str(c).lower() for c in profile.get("health_condition", [])
        ])
        keywords_anxiety = ["焦虑", "心慌", "紧张", "担心", "不安", "anxiety", "panic"]
        keywords_depression = ["抑郁", "低落", "消沉", "失眠", "不想动", "没劲", "depression"]
        keywords_bipolar = ["双相", "躁郁", "bipolar", "情绪忽高忽低"]

        if any(kw in conditions for kw in keywords_bipolar):
            return "bipolar"
        if any(kw in conditions for kw in keywords_depression):
            return "depression"
        if any(kw in conditions for kw in keywords_anxiety):
            return "anxiety"

        # 3. 从情感趋势中推断（连续多天低落 -> depression, 波动大 -> bipolar）
        if "极度危险" in emotion_trend or "波动较大" in emotion_trend:
            return "depression"

        return "none"

    def _build_therapy_guidance(self, tendency: str) -> str:
        """根据心理健康倾向构建 CBT 疗愈指导语（精简版）"""
        if tendency == "none":
            return ""

        base = "\n**CBT疗愈策略**（融入聊天，勿直接念出，每次用1-2个技巧）：\n"

        anxiety_guidance = (
            "焦虑倾向：①认知重构—温柔引导区分\"担心\"和\"事实\"，用过去平安的经历反证；"
            "②着陆技术—引导关注当下安全环境（\"您看看屋里，跟小暖说说能看到啥？\"）；"
            "③呼吸引导—\"咱慢慢吸口气，再慢慢呼出去，试一下？\"；"
            "④小步实验—鼓励极小的安全尝试。节奏：先稳住→认知引导→微小行动。语气像锚，不跟焦虑一起急。"
        )

        depression_guidance = (
            "抑郁倾向：①行为激活—找极小切入口（\"今天就做一件舒服的事，阳台站两分钟也算\"），降低要求、肯定每步；"
            "②愉悦回忆—自然提起老人过去的爱好（\"我记得您喜欢听评剧？咱就放一小段\"）；"
            "③认知重构—先共情（\"心里沉，做什么都费劲\"），再温柔找反例，挑战负性自动思维；"
            "④掌控感重建—每天找一件\"您说了算\"的小事。节奏：共情重量→找小小光亮→微型行动选项。不催促，不正能量轰炸。"
        )

        bipolar_guidance = (
            "双相倾向（需药物维持）：①情绪监测—用日常语言问\"这两天在高坡还是低谷？\"帮觉察情绪位置；"
            "②作息维稳（核心）—温柔坚定维护规律睡眠和饮食；③药物提醒—融入关怀自然提及；"
            "④高涨期—不打击热情但温和降速：\"主意真多！咱记下来，一个一个看，不急\"；"
            "⑤低落期—用抑郁策略但要求更小（\"把窗帘拉开就行\"）；"
            "⑥预警—记录睡眠和言语变化信号，危险时调emergency_contact。节奏：平稳、可预测，不跟高涨不沉低谷。"
        )

        guidance_map = {
            "anxiety": anxiety_guidance,
            "depression": depression_guidance,
            "bipolar": bipolar_guidance,
        }
        return base + guidance_map.get(tendency, "")

    def _detect_photo_intent(self, text: str) -> Optional[str]:
        if not text:
            return None
        triggers = ["照片", "相册", "视频", "全家福", "孙子", "孙女", "回忆", "看看照片", "看照片"]
        if not any(t in text for t in triggers):
            return None
        if "孙子" in text:
            return "孙子"
        if "孙女" in text:
            return "孙女"
        if "全家福" in text:
            return "全家福"
        if "相册" in text:
            return "all"
        if "回忆" in text:
            return "random"
        return "recent"
        
    def _build_workflow(self):
        # 1. 定义节点
        async def analyze_inputs(state: AgentState):
            """分析多模态输入，确定主要情感和意图"""
            text = state.get("input_text", "")
            voice = state.get("voice_text", "")
            voice_emotion = state.get("voice_emotion", "")
            visual = state.get("visual_emotion", {})
            session_context = state.get("session_context") or {}
            
            combined_input = text
            if voice:
                combined_input += f" (语音转文字: {voice})"
            if voice_emotion:
                combined_input += f" (语音情感标签: {voice_emotion})"
            if visual:
                emotion = visual.get("emotion", "unknown")
                confidence = visual.get("confidence", 0.0)
                combined_input += f" (视觉情感分析: {emotion}, 置信度: {confidence})"
            
            # 初始化 messages 列表
            messages = state.get("messages", [])
            
            # 如果是新一轮对话（或者首次），需要加载历史记忆
            if not messages:
                # 1. 从持久化存储中加载最近对话历史
                recent_history_dicts = session_context.get("recent_history")
                if recent_history_dicts is None:
                    recent_history_dicts = await asyncio.to_thread(self.rag_helper.get_recent_history, limit=5)
                history_messages = []
                for h in recent_history_dicts:
                    if h["role"] == "user":
                        history_messages.append(HumanMessage(content=h["content"]))
                    elif h["role"] == "assistant":
                        # 清理旧数据中的元数据
                        content = h["content"]
                        try:
                            if content.strip().startswith("{") or content.strip().startswith("```"):
                                clean_c = content.strip()
                                if clean_c.startswith("```json"): clean_c = clean_c[7:]
                                if clean_c.startswith("```"): clean_c = clean_c[3:]
                                if clean_c.endswith("```"): clean_c = clean_c[:-3]
                                data = json.loads(clean_c)
                                if "content" in data:
                                    content = data["content"]
                        except:
                            pass
                        content = self._strip_parenthetical_text(content)
                        history_messages.append(AIMessage(content=content))
                
                # 2. 获取用户画像
                profile = session_context.get("user_profile")
                if profile is None:
                    profile = await asyncio.to_thread(self.rag_helper.get_user_profile)
                profile_str = json.dumps(profile, ensure_ascii=False, separators=(',', ':'))
                
                # 3. 获取情感趋势
                emotion_trend = session_context.get("emotion_trend")
                if emotion_trend is None:
                    emotion_trend = await asyncio.to_thread(self.rag_helper.get_emotion_trend)

                recent_history_text = session_context.get("recent_history_text", "暂无最近对话")
                memory_context = session_context.get("memory_context", "")
                community_activities = session_context.get("community_activities", [])
                community_text = json.dumps(community_activities, ensure_ascii=False) if community_activities else "暂无"

                # 4. 检测心理健康倾向并构建 CBT 疗愈指导
                mental_health_tendency = self._detect_mental_health_tendency(profile, emotion_trend)
                therapy_guidance = self._build_therapy_guidance(mental_health_tendency)

                # 5. 构建 System Message（精简版，减少token消耗）
                system_prompt = f"""你是"小暖"，老人的贴心晚辈。陪老人唠嗑，语气自然温暖像家人面对面。

用户画像:{profile_str}
近期情感:{emotion_trend}
心理需求:{mental_health_tendency if mental_health_tendency != "none" else "一般陪伴"}
最近对话:{recent_history_text}
记忆线索:{memory_context or "无"}
{therapy_guidance}
规则:①口语化不说书面语不列点不用Markdown;②回复2-3句,轻松聊天;③先共情再接内容,安慰自然不堆砌;④高风险/求助时可到3-4句;⑤禁止哄小孩语气,禁止文本中含动作描写(动作通过工具表达)。
工具:搜照片→search_family_photos;急救不适→emergency_contact/record_health_complaint;救命摔倒胸闷→emergency_contact(level=high)且回复短;每次必调EmotionalStateUpdate."""
                messages = [SystemMessage(content=system_prompt)] + history_messages
            
            # 将当前输入作为 User Message 追加
            messages.append(HumanMessage(content=combined_input))
            
            forced_keyword = self._detect_photo_intent(text) or self._detect_photo_intent(combined_input)
            return {
                "input_text": combined_input,
                "messages": messages,
                "force_photo_tool": bool(forced_keyword),
                "forced_photo_keyword": forced_keyword or "",
                "photo_tool_ran": False
            }

        async def retrieve_knowledge(state: AgentState):
            """检索心理学知识库 & 长期记忆"""
            query = state["input_text"]
            session_context = state.get("session_context") or {}
            
            # 使用新的综合检索方法 (Knowledge + Memory + Events)
            # 这是一个同步方法，但在异步节点中调用，建议用 asyncio.to_thread 避免阻塞
            # 不过这里为了简单直接调用也行，Chroma 的操作通常较快
            context = session_context.get("memory_context")
            if context is None:
                context = await asyncio.to_thread(self.rag_helper.search_comprehensive_memory, query, k=3)
            
            # 将知识库内容注入到 System Message
            messages = list(state["messages"])
            if messages and isinstance(messages[0], SystemMessage):
                original_content = messages[0].content.split("\n\n【相关记忆/知识库】")[0]
                new_content = f"{original_content}\n\n【相关记忆/知识库】:\n{context}"
                messages[0] = SystemMessage(content=new_content)
            
            return {"context": context, "messages": messages}

        async def call_model(state: AgentState):
            """调用单一 LLM，处理所有逻辑"""
            messages = state["messages"]
            response = await self.llm.ainvoke(messages)
            
            # 清理文本回复中的括号
            clean_content = self._strip_parenthetical_text(response.content)
            if clean_content:
                response.content = clean_content

            self._ensure_emotional_update(response, state["input_text"])
            
            return {"messages": messages + [response], "response": response.content}
            
        async def call_tools(state: AgentState):
            """执行业务工具"""
            messages = list(state["messages"])
            last_message = messages[-1] if messages else None
            tool_messages: List[BaseMessage] = []

            # 过滤掉 EmotionalStateUpdate，只保留业务工具调用
            business_tool_names = {t.name for t in self.business_tools}
            all_tool_calls = list(getattr(last_message, "tool_calls", None) or [])
            business_tool_calls = [tc for tc in all_tool_calls if tc.get("name") in business_tool_names]

            if business_tool_calls:
                # 临时替换 tool_calls 为纯业务工具，避免 ToolNode 收到无法处理的 EmotionalStateUpdate
                original_tool_calls = last_message.tool_calls
                last_message.tool_calls = business_tool_calls
                try:
                    tool_node = ToolNode(self.business_tools)
                    result = await tool_node.ainvoke(state)
                    tool_messages = result["messages"]
                finally:
                    last_message.tool_calls = original_tool_calls
            elif state.get("force_photo_tool") and not state.get("photo_tool_ran"):
                photo_tool = next((t for t in self.business_tools if getattr(t, "name", "") == "search_family_photos"), None)
                if photo_tool is not None:
                    keyword = state.get("forced_photo_keyword") or "recent"
                    output = await photo_tool.ainvoke({"keyword": keyword})
                    try:
                        tool_messages = [ToolMessage(content=str(output), tool_call_id="photo_force", name="search_family_photos")]
                    except TypeError:
                        tool_messages = [ToolMessage(content=str(output), tool_call_id="photo_force")]

            all_messages = messages + tool_messages
            photo_tool_ran = any(
                isinstance(m, ToolMessage) and getattr(m, "name", None) == "search_family_photos"
                for m in tool_messages
            )
            return {"messages": all_messages, "photo_tool_ran": bool(state.get("photo_tool_ran") or photo_tool_ran)}

        def should_continue(state: AgentState):
            """判断是否需要继续执行工具"""
            messages = state["messages"]
            last_message = messages[-1]

            # 防止无限循环，限制最大消息数 (比如大于 15 条消息说明调用工具太多次)
            if len(messages) > 15:
                return END

            if state.get("force_photo_tool") and not state.get("photo_tool_ran"):
                return "tools"

            # 只检查业务工具调用，忽略 EmotionalStateUpdate 等元数据工具
            tool_calls = getattr(last_message, "tool_calls", None) or []
            if tool_calls:
                business_tool_names = {t.name for t in self.business_tools}
                has_business_tool = any(
                    tc.get("name") in business_tool_names
                    for tc in tool_calls
                    if isinstance(tc, dict)
                )

                if has_business_tool:
                    return "tools"

            return END

        # 2. 构建图
        workflow = StateGraph(AgentState)
        
        # 节点 (retrieve 已移除，记忆上下文在 _build_shared_context 预加载并注入 System Prompt，无需重复检索)
        workflow.add_node("analyze", analyze_inputs)
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", call_tools)

        # 边
        workflow.set_entry_point("analyze")
        workflow.add_edge("analyze", "agent")
        
        # 条件边
        workflow.add_conditional_edges(
            "agent",
            should_continue,
            {
                "tools": "tools",
                END: END
            }
        )
        
        # tools -> agent (ReAct Loop)
        workflow.add_edge("tools", "agent")
        
        return workflow.compile()

    async def astream_run(self, input_text: str, voice_text: Optional[str] = None, voice_emotion: Optional[str] = None, visual_emotion: Optional[Dict[str, Any]] = None, history: List[BaseMessage] = [], session_context: Optional[Dict[str, Any]] = None):
        """异步流式运行智能体"""
        initial_state = {
            "input_text": input_text,
            "voice_text": voice_text,
            "voice_emotion": voice_emotion,
            "visual_emotion": visual_emotion,
            "session_context": session_context or {},
            "history": history,
            "context": "",
            "response": "",
            "messages": []
        }
        
        async for event in self.workflow.astream_events(initial_state, version="v1"):
            if event["event"] == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    chunk.content = self._strip_parenthetical_text(chunk.content)

            elif event["event"] == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output and hasattr(output, "content"):
                    output.content = self._strip_parenthetical_text(output.content)
                    self._ensure_emotional_update(output, input_text)

            yield event
            # 如果是 END，我们需要手动保存记忆 (因为之前的 save logic 在 call_model 里，现在可能多次调用)
            # 最好的方式是在 Orchestrator 里保存，或者在这里检测到最终输出时保存
            # 为了简单起见，我们还是在 Orchestrator 里处理最终的文本累积和保存，或者这里不处理记忆保存，只负责生成。
            # 原有的逻辑是在 call_model 里保存，这会导致每次 call_model 都保存。
            # 正确的做法：Orchestrator 负责收集完整回复后保存。目前 Orchestrator 似乎没做这个，所以我们需要在这里或者 helper 里做。
            # 暂时保持现状，让 rag_helper.add_memory 在 Orchestrator 调用，或者在 Graph 结束时调用。
            # 由于 Graph 没有显式的"结束节点"回调，我们在 Orchestrator 里处理副作用比较好。

    def run(self, input_text: str, voice_text: Optional[str] = None, voice_emotion: Optional[str] = None, visual_emotion: Optional[Dict[str, Any]] = None, history: List[BaseMessage] = [], session_context: Optional[Dict[str, Any]] = None):
        """运行智能体 (同步兼容接口)"""
        # ... (保持原有兼容逻辑，但适配新的单 LLM 结构)
        initial_state = {
            "input_text": input_text,
            "voice_text": voice_text,
            "voice_emotion": voice_emotion,
            "visual_emotion": visual_emotion,
            "session_context": session_context or {},
            "history": history,
            "context": "",
            "response": "",
            "messages": []
        }
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            
        result = self.workflow.invoke(initial_state)
        last_msg = result["messages"][-1]
        last_msg.content = self._strip_parenthetical_text(last_msg.content)
        self._ensure_emotional_update(last_msg, input_text)
        
        response_data = {"content": last_msg.content}
        
        # 提取 EmotionalStateUpdate
        if last_msg.tool_calls:
            for tool_call in last_msg.tool_calls:
                if tool_call["name"] == "EmotionalStateUpdate":
                    args = tool_call["args"]
                    response_data.update(args)
                    
                    if "profile_update" in args and isinstance(args["profile_update"], dict):
                        for k, v in args["profile_update"].items():
                            self.rag_helper.update_user_profile(k, v)
                    if "risk_level" in args and "expression" in args:
                        self.rag_helper.log_emotion(args["expression"], args["risk_level"])
                        
        return response_data
