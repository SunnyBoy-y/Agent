import httpx
import json
import traceback
import asyncio
from typing import Dict, Any, Optional, List
from src.utils.logger import logger
from src.config import Config

# Import Agents
from src.agents.emotional_agent import EmotionalConnectionAgent
from src.agents.router_agent import RouterAgent
from src.agents.medical_agent import MedicalAgent
from src.agents.daily_life_agent import DailyLifeAgent
from src.agents.interest_agent import InterestAgent
from src.agents.mental_health_agent import MentalHealthAgent
from src.agents.antifraud_agent import AntiFraudAgent
from src.agents.proactive_agent import ProactiveAgent

# 辅助函数：构造 SSE 事件格式
def create_event(event_type: str, data: Any):
    return json.dumps({
        "type": event_type,
        "data": data
    }, ensure_ascii=False)

class SystemOrchestrator:
    def __init__(self):
        logger.info("正在初始化多智能体系统...")
        try:
            self.router = RouterAgent()
            self.emotional_agent = EmotionalConnectionAgent()
            self.medical_agent = MedicalAgent()
            self.daily_life_agent = DailyLifeAgent()
            self.interest_agent = InterestAgent()
            self.mental_health_agent = MentalHealthAgent()
            self.antifraud_agent = AntiFraudAgent()
            self.proactive_agent = ProactiveAgent()
            self.state_lock = asyncio.Lock()
            self.last_system_state = {
                "last_input": "",
                "last_route": "",
                "tool_calls": [],
                "context_snapshot": {}
            }
            logger.info("系统初始化完成。")
        except Exception as e:
            logger.error(f"智能体初始化失败: {e}")
            raise e

    async def check_and_generate_proactive_event(self):
        """检查是否需要生成主动问候"""
        try:
            result = await self.proactive_agent.check_and_generate()
            if result:
                logger.info(f"Generated proactive event: {result}")
                return create_event("proactive_question", result)
            return None
        except Exception as e:
            logger.error(f"Proactive check failed: {e}")
            return None

    async def process_input_stream(self, user_input: str, context: Optional[Dict[str, Any]] = None):
        """
        处理输入流，协调智能体运行
        """
        context = dict(context or {})

        # 0. 立即返回日志，给前端即时反馈
        logger.info(f"收到用户输入: {user_input}")
        yield create_event("log", f"收到用户输入: {user_input}")

        # 1. 并行：RAG预加载 + 外部表情API + 路由决策
        async def _fetch_visual():
            if context.get("visual_analysis"):
                return context["visual_analysis"]
            try:
                async with httpx.AsyncClient(timeout=0.3) as client:
                    response = await client.get(Config.VISUAL_ANALYSIS_URL, params={"mode": "camera"})
                    if response.status_code == 200:
                        data = response.json()
                        if data:
                            context["visual_analysis"] = data
                            return data
            except Exception:
                pass
            return None

        # 路由决策（纯规则，<1ms）
        force_agent = context.get("force_agent")
        valid_agents = [
            "emotional_agent", "medical_agent", "daily_life_agent",
            "interest_agent", "mental_health_agent", "antifraud_agent"
        ]
        if force_agent in valid_agents:
            target_agent_name = force_agent
        else:
            target_agent_name = self.router.route_sync(user_input)

        # RAG 预加载；视觉API 异步火墙（不等它，好了就用，不好不阻塞）
        shared_context_task = asyncio.create_task(
            self._build_shared_context(user_input, context)
        )
        visual_task = asyncio.create_task(_fetch_visual())

        shared_context = await shared_context_task
        # 视觉API 不等：若 RAG 完成后 0.3s 内没拿到结果就放弃
        visual_emotion = None
        try:
            visual_emotion = await asyncio.wait_for(visual_task, timeout=0.3)
        except asyncio.TimeoutError:
            pass
        voice_text = shared_context.get("audio_transcript")
        if visual_emotion:
            yield create_event("log", f"📷 接收到视觉情感数据: {visual_emotion}")
        if voice_text:
            yield create_event("log", f"🎤 接收到语音转文字: {voice_text}")

        # 2. 路由阶段（仅输出日志，决策已在上面完成）
        yield create_event("step", {"name": "router", "status": "running"})
            
        async with self.state_lock:
            self.last_system_state["last_route"] = target_agent_name
            self.last_system_state["last_input"] = user_input
            self.last_system_state["tool_calls"] = [] # Reset tool calls for the new turn
            self.last_system_state["context_snapshot"] = self._build_context_snapshot(shared_context)

        yield create_event("step", {"name": "router", "status": "done", "output": target_agent_name})
        yield create_event("log", f"🤖 路由至智能体: {target_agent_name}")
        
        # 3. 智能体执行
        yield create_event("step", {"name": target_agent_name, "status": "running"})
        
        # 更新 Agent 状态 (最后更新时间)
        await asyncio.to_thread(self.proactive_agent.rag_helper.update_agent_status, agent_type=target_agent_name.replace("_agent", ""))
        
        full_response = ""
        
        try:
            if target_agent_name == "emotional_agent":
                # 情感智能体保持流式特性
                async for event in self._run_emotional_agent(user_input, shared_context):
                    if json.loads(event)["type"] == "token":
                        full_response += json.loads(event)["data"]
                    yield event
            else:
                # 其他智能体
                result = await self._run_specific_agent(target_agent_name, user_input, shared_context)
                
                content = result.get("content", "")
                if content:
                    for chunk in self._chunk_response_text(content):
                        full_response += chunk
                        yield create_event("token", chunk)
                        await asyncio.sleep(0)
                
                if result.get("action"):
                    yield create_event("action", result["action"])
                music_payload = self._normalize_music_payload(
                    result.get("music_result"),
                    fallback_query=result.get("music_query") or user_input,
                    music_flag=result.get("music")
                )
                if music_payload is not None:
                    yield create_event("music_payload", music_payload)
                    yield create_event("music", bool(music_payload["trigger_music"]))
                if result.get("sos") is not None:
                    yield create_event("sos", bool(result["sos"]))
                if result.get("risk_level"):
                    yield create_event("risk", result["risk_level"])
                
                yield create_event("log", f"✅ {target_agent_name} 执行完成")
            
            yield create_event("step", {"name": target_agent_name, "status": "done"})
            
            # 保存到对话记忆
            if full_response:
                await asyncio.to_thread(self.proactive_agent.rag_helper.add_memory, user_input, full_response)
            
        except Exception as e:
            err_msg = str(e)
            logger.error(f"❌ 智能体运行出错: {err_msg}")
            logger.error(traceback.format_exc())
            yield create_event("log", f"❌ 智能体运行出错: {err_msg}")
            
            if "Arrearage" in err_msg or "overdue payment" in err_msg:
                yield create_event("error", "阿里云百炼服务欠费或余额不足。")
            else:
                yield create_event("error", "系统处理出错，请稍后再试。")
            
        yield create_event("done", "stop")

    async def _build_shared_context(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        rag = self.emotional_agent.rag_helper
        profile, recent_history, memory_context, emotion_trend, agent_status = await asyncio.gather(
            asyncio.to_thread(rag.get_user_profile),
            asyncio.to_thread(rag.get_recent_history, 5),
            asyncio.to_thread(rag.search_comprehensive_memory, user_input, 3),
            asyncio.to_thread(rag.get_emotion_trend),
            asyncio.to_thread(rag.get_agent_status),
        )
        recent_history = self._sanitize_recent_history(recent_history)

        shared_context = dict(context)
        shared_context["user_profile"] = profile
        shared_context["recent_history"] = recent_history
        shared_context["recent_history_text"] = self._format_recent_history(recent_history)
        shared_context["memory_context"] = memory_context
        shared_context["emotion_trend"] = emotion_trend
        shared_context["agent_status"] = agent_status
        return shared_context

    def _format_recent_history(self, recent_history: List[Dict[str, Any]]) -> str:
        if not recent_history:
            return "暂无最近对话"

        lines: List[str] = []
        for item in recent_history[-6:]:
            role = "老人" if item.get("role") == "user" else "小暖"
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else "暂无最近对话"

    def _sanitize_recent_history(self, recent_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        skip_next_assistant = False
        for item in recent_history:
            content = str(item.get("content", "")).strip()
            if content.startswith("[系统判定老人沉默"):
                skip_next_assistant = True
                continue
            if skip_next_assistant and item.get("role") == "assistant":
                skip_next_assistant = False
                continue
            skip_next_assistant = False
            cleaned.append(item)
        return cleaned

    def _build_context_snapshot(self, context: Dict[str, Any]) -> Dict[str, Any]:
        profile = context.get("user_profile") or {}
        return {
            "user_id": context.get("user_id"),
            "profile_name": profile.get("name", "未知"),
            "health_condition": profile.get("health_condition", []),
            "preferences": profile.get("preferences", []),
            "visual_analysis": context.get("visual_analysis"),
            "voice_emotion": context.get("voice_emotion"),
            "recent_history_preview": (context.get("recent_history_text") or "")[:300],
            "memory_context_preview": (context.get("memory_context") or "")[:300],
        }

    async def _run_specific_agent(self, agent_name: str, input_text: str, context: Dict) -> Dict:
        """运行非流式智能体并标准化输出"""
        if agent_name == "medical_agent":
            return await self.medical_agent.arun(input_text, context)
        elif agent_name == "daily_life_agent":
            return await self.daily_life_agent.arun(input_text, context)
        elif agent_name == "interest_agent":
            return await self.interest_agent.arun(input_text, context)
        elif agent_name == "mental_health_agent":
            return await self.mental_health_agent.arun(input_text, context)
        elif agent_name == "antifraud_agent":
            res = await self.antifraud_agent.arun(input_text, context)
            intervention = res.get("intervention", {})
            analysis = res.get("analysis", {})
            
            content = intervention.get("action_to_senior", "")
            if not content:
                if analysis.get("risk_level") == "Safe":
                     content = "我帮您看了，这个信息看起来是安全的，不用担心。"
                else:
                     content = f"这里面可能有诈骗风险（等级：{analysis.get('risk_level')}），千万别转账！我这就通知您的家人。"
            
            risk = self._normalize_risk_level(analysis.get("risk_level", "low"))
            return {
                "content": content,
                "action": "warning" if risk != "safe" else "nod",
                "risk_level": risk,
                "family_message": intervention.get("action_to_family"),
                "community_message": intervention.get("action_to_community"),
            }
        return {"content": "系统暂时没找到合适的处理专员，我先陪您慢慢说。", "action": "nod", "risk_level": "low"}

    def _chunk_response_text(self, text: str, chunk_size: int = 18) -> List[str]:
        """Split non-streaming agent output into small SSE token chunks."""
        text = text or ""
        chunks: List[str] = []
        buffer = ""
        for char in text:
            buffer += char
            if len(buffer) >= chunk_size or char in "，。！？；,.!?;":
                chunks.append(buffer)
                buffer = ""
        if buffer:
            chunks.append(buffer)
        return chunks

    def _normalize_risk_level(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if "crisis" in text or "危机" in text:
            return "crisis"
        if "high" in text or "高" in text or "紧急" in text:
            return "high"
        if "medium" in text or "中" in text or "确认" in text:
            return "medium"
        if "low" in text or "低" in text or "疑似" in text:
            return "low"
        if "safe" in text or "安全" in text:
            return "safe"
        return "low"

    async def _run_emotional_agent(self, user_input: str, context: Dict[str, Any]):
        """Handle emotional agent streaming output and tool events."""
        parentheses_depth = 0
        streamed_text = ""

        async for event in self.emotional_agent.astream_run(
            input_text=user_input,
            voice_text=context.get("audio_transcript"),
            voice_emotion=context.get("voice_emotion"),
            visual_emotion=context.get("visual_analysis"),
            session_context=context
        ):
            try:
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    chunk_content = self._extract_message_text(
                        getattr(chunk, "content", None) if chunk else None
                    )
                    if chunk_content:
                        filtered_content = ""
                        for char in chunk_content:
                            if char in ["(", "（"]:
                                parentheses_depth += 1
                                continue
                            if char in [")", "）"]:
                                if parentheses_depth > 0:
                                    parentheses_depth -= 1
                                continue
                            if parentheses_depth > 0:
                                continue
                            filtered_content += char

                        if filtered_content:
                            streamed_text += filtered_content
                            yield create_event("token", filtered_content)

                elif kind == "on_tool_start":
                    tool_name = event.get("name")
                    if tool_name and tool_name != "EmotionalStateUpdate":
                        tool_input = event.get("data", {}).get("input", {})
                        async with self.state_lock:
                            self.last_system_state["tool_calls"].append({
                                "tool": tool_name,
                                "input": tool_input,
                                "output": None
                            })

                elif kind == "on_tool_end":
                    event_name = event.get("name")
                    if event_name and event_name != "EmotionalStateUpdate":
                        parsed_output = self._parse_tool_output(event.get("data", {}).get("output", ""))
                        async with self.state_lock:
                            for idx in range(len(self.last_system_state["tool_calls"]) - 1, -1, -1):
                                item = self.last_system_state["tool_calls"][idx]
                                if item.get("tool") == event_name and item.get("output") is None:
                                    item["output"] = parsed_output
                                    break

                    if event_name == "search_family_photos":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            status = output_data.get("status")
                            photos = output_data.get("photos", [])
                            if status == "success" and photos:
                                logger.info(f"Found {len(photos)} photos from emotional agent")
                                yield create_event("photos", photos)
                            else:
                                yield create_event(
                                    "photos_result",
                                    {
                                        "status": status or "unknown",
                                        "message": output_data.get("message", ""),
                                        "photos": photos or []
                                    }
                                )
                        except Exception:
                            pass
                    elif event_name == "emergency_contact":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            if output_data.get("trigger_sos") is True:
                                yield create_event("sos", True)
                        except Exception:
                            pass
                    elif event_name == "play_music":
                        try:
                            output_data = self._parse_tool_output(event.get("data", {}).get("output", ""))
                            music_payload = self._normalize_music_payload(output_data, music_flag=True)
                            if music_payload and music_payload.get("trigger_music") is True:
                                yield create_event("music_payload", music_payload)
                                yield create_event("music", True)
                        except Exception:
                            pass

                elif kind == "on_chat_model_end":
                    metadata = event.get("metadata")
                    if isinstance(metadata, dict) and metadata.get("langgraph_node") == "agent":
                        output = event.get("data", {}).get("output")
                        final_content = self._strip_parenthetical_text(
                            self._extract_message_text(
                                getattr(output, "content", None) if output else None
                            )
                        )
                        if final_content:
                            remaining_text = ""
                            if not streamed_text:
                                remaining_text = final_content
                            elif final_content.startswith(streamed_text):
                                remaining_text = final_content[len(streamed_text):]

                            if remaining_text:
                                streamed_text += remaining_text
                                yield create_event("token", remaining_text)

                elif kind == "on_chain_end" and event.get("name") == "agent":
                    output = event.get("data", {}).get("output")
                    emotional_args = self._extract_emotional_update_args(output)
                    if emotional_args:
                        if "expression" in emotional_args:
                            yield create_event("expression", emotional_args["expression"])
                        if "action" in emotional_args:
                            yield create_event("action", emotional_args["action"])
                        if "risk_level" in emotional_args:
                            yield create_event("risk", emotional_args["risk_level"])
                        if "profile_update" in emotional_args and emotional_args["profile_update"]:
                            await asyncio.to_thread(
                                lambda: [
                                    self.emotional_agent.rag_helper.update_user_profile(k, v)
                                    for k, v in emotional_args["profile_update"].items()
                                ]
                            )
                        if "risk_level" in emotional_args and "expression" in emotional_args:
                            await asyncio.to_thread(
                                self.emotional_agent.rag_helper.log_emotion,
                                emotional_args["expression"],
                                emotional_args["risk_level"]
                            )

                    if not streamed_text:
                        fallback_reply = self._build_emotional_fallback_reply(
                            user_input=user_input,
                            context=context,
                            emotional_args=emotional_args
                        )
                        if fallback_reply:
                            streamed_text += fallback_reply
                            yield create_event("token", fallback_reply)
            except Exception as inner_e:
                logger.error(f"Emotional agent streaming handling failed: {inner_e}")
                logger.error(traceback.format_exc())
                yield create_event("log", f"Emotional agent stream parsing failed: {str(inner_e)}")

        logger.info("Emotional response streaming completed")
        yield create_event("log", "情感回复流式传输完成")

    def _extract_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
            return "".join(text_parts)
        return ""

    def _strip_parenthetical_text(self, text: str) -> str:
        if not text:
            return ""

        result: List[str] = []
        depth = 0
        for char in text:
            if char in ['(', '（']:
                depth += 1
                continue
            if char in [')', '）']:
                if depth > 0:
                    depth -= 1
                continue
            if depth == 0:
                result.append(char)

        return "".join(result)

    def _extract_emotional_update_args(self, output: Any) -> Dict[str, Any]:
        messages = []
        if isinstance(output, dict):
            messages = output.get("messages") or []
        elif output is not None:
            messages = [output]

        for message in reversed(messages):
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                args = tool_call.get("args", {})
                if not isinstance(args, dict):
                    continue
                if tool_call.get("name") == "EmotionalStateUpdate":
                    if args:
                        return args
                    continue
                if {"expression", "action", "risk_level"} & set(args.keys()):
                    return args
        return {}

    def _build_emotional_fallback_reply(
        self,
        user_input: str,
        context: Dict[str, Any],
        emotional_args: Optional[Dict[str, Any]] = None
    ) -> str:
        profile = context.get("user_profile") or {}
        name = profile.get("name") or "您"
        expression = (emotional_args or {}).get("expression", "neutral")
        risk_level = (emotional_args or {}).get("risk_level", "low")
        normalized_input = (user_input or "").strip()

        greeting_keywords = ("你好", "您好", "在吗", "在不在", "哈喽")
        if normalized_input in greeting_keywords or any(k in normalized_input for k in greeting_keywords):
            return f"{name}，您好呀，我在这儿陪着您呢。您这会儿想先聊聊天，还是想听段戏、看看老照片？"

        if risk_level == "high":
            return f"{name}，我听着您这会儿挺难受的，先别一个人扛着。我陪您慢慢说，您现在最想让我先帮您做点什么？"
        if expression == "sad":
            return f"{name}，我听出来您心里有点发沉。没事，咱们慢慢唠，您愿意跟我说说刚才最挂心的是啥吗？"
        if expression == "concerned":
            return f"{name}，我在呢。您刚才这句话我听进心里了，咱们慢慢说，看看我能陪您一起理一理什么。"
        if expression == "happy":
            return f"{name}，听您这么一说，我也跟着高兴。您要是愿意，咱们接着往下聊。"

        return f"{name}，我在这儿陪着您。您要是愿意，就接着跟我说说，我认真听着呢。"

    def _parse_tool_output(self, output_str: Any) -> Dict[str, Any]:
        if isinstance(output_str, list) and output_str:
            output_str = output_str[-1]
        if isinstance(output_str, dict):
            return output_str
        if hasattr(output_str, "content") and isinstance(getattr(output_str, "content", None), str):
            output_str = output_str.content
        if not output_str:
            return {}
        try:
            return json.loads(output_str)
        except Exception:
            return {}

    def _normalize_music_payload(
        self,
        payload: Optional[Dict[str, Any]],
        fallback_query: str = "",
        music_flag: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        if payload is None and music_flag is None:
            return None

        payload = payload or {}
        trigger_music = bool(payload.get("trigger_music", music_flag))
        normalized_query = payload.get("query") or fallback_query

        return {
            "status": payload.get("status", "success" if trigger_music else "noop"),
            "intent": payload.get("intent", "play_music"),
            "trigger_music": trigger_music,
            "query": normalized_query,
            "source": payload.get("source", "agent")
        }
