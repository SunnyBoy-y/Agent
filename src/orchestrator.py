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
                    full_response = content
                    yield create_event("token", content)
                
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
            
            risk = analysis.get("risk_level", "low").lower()
            if risk == "high": risk = "high" # Normalize
            
            return {
                "content": content,
                "action": "warning" if risk != "safe" else "nod",
                "risk_level": risk
            }
        return {"content": "系统错误：未知的智能体。", "action": "shake"}

    async def _run_emotional_agent(self, user_input: str, context: Dict[str, Any]):
        """复用原有的情感智能体流式逻辑"""
        parentheses_depth = 0
        print(user_input)
        print(context)
        
        async for event in self.emotional_agent.astream_run(
            input_text=user_input,
            voice_text=context.get("audio_transcript"),
            voice_emotion=context.get("voice_emotion"),
            visual_emotion=context.get("visual_analysis"),
            session_context=context
        ):
            try:
                kind = event["event"]
                print("kind=", kind)
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        # 实时过滤括号
                        filtered_content = ""
                        for char in chunk.content:
                            if char in ['(', '（']:
                                parentheses_depth += 1
                                continue
                            elif char in [')', '）']:
                                if parentheses_depth > 0:
                                    parentheses_depth -= 1
                                continue

                            if parentheses_depth > 0:
                                continue
                            else:
                                filtered_content += char

                        if filtered_content:
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
                                logger.info(f"📸 检索到 {len(photos)} 张照片")
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
                        if output and hasattr(output, "tool_calls") and output.tool_calls:
                            for tool_call in output.tool_calls:
                                if isinstance(tool_call, dict) and tool_call.get("name") == "EmotionalStateUpdate":
                                    args = tool_call.get("args", {})
                                    if isinstance(args, dict):
                                        if "expression" in args:
                                            yield create_event("expression", args["expression"])
                                        if "action" in args:
                                            yield create_event("action", args["action"])
                                        if "risk_level" in args:
                                            yield create_event("risk", args["risk_level"])

                                        # 副作用
                                        if "profile_update" in args and args["profile_update"]:
                                            await asyncio.to_thread(
                                                lambda: [self.emotional_agent.rag_helper.update_user_profile(k, v)
                                                         for k, v in args["profile_update"].items()]
                                            )
                                        if "risk_level" in args and "expression" in args:
                                            await asyncio.to_thread(
                                                self.emotional_agent.rag_helper.log_emotion,
                                                args["expression"],
                                                args["risk_level"]
                                            )
            except Exception as inner_e:
                logger.error(f"处理情感智能体事件时出错: {inner_e}")
                logger.error(traceback.format_exc())
                yield create_event("log", f"⚠️ 事件处理异常: {str(inner_e)}")

        logger.info("✅ 情感回复流式传111输完成")
        yield create_event("log", "✅ 情感回复流式传输完成")

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
