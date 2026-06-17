import sys
import os
import json
import time
import uuid
import inspect
import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List, Optional

# 确保能找到 src 包
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import uvicorn

from src.config import Config
from src.orchestrator import SystemOrchestrator
from src.schemas.actions import ActionCompleteRequest, ActionConsentRequest
from src.schemas.community import (
    CommunityActivityCreateRequest,
    CommunityActivityUpdateRequest,
    CommunityAnnouncementCreateRequest,
    CommunityAnnouncementUpdateRequest,
)
from src.schemas.family import (
    FamilyChatRequest,
    FamilyMessageCreateRequest,
    FamilyPolicyUpdateRequest,
    QuietMessageConsentRequest,
)
from src.schemas.music_library import MusicLibrarySyncRequest
from src.schemas.photo_library import PhotoLibrarySyncRequest
from src.schemas.timed_events import MedicationPlan, TimedEventAck
from src.utils.logger import logger

# 全局 Orchestrator 实例
orchestrator: Optional[SystemOrchestrator] = None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "user_001"
    context: Dict[str, Any] = Field(default_factory=dict)


def error_response(status_code: int, message: str, request_id: str = None, details: Any = None) -> JSONResponse:
    payload = {
        "status": "error",
        "code": status_code,
        "message": message,
    }
    if request_id:
        payload["request_id"] = request_id
    if details is not None:
        payload["details"] = details
    return JSONResponse(status_code=status_code, content=payload)


def model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model or {})


def parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    生命周期管理器：
    - 启动时初始化资源 (Orchestrator, DB连接等)
    - 关闭时清理资源
    """
    global orchestrator
    logger.info("正在初始化系统核心组件...")
    try:
        orchestrator = SystemOrchestrator()
        logger.info("系统核心组件初始化完成。")
    except Exception as e:
        logger.error(f"系统初始化失败: {e}")
        raise e
    
    yield
    
    logger.info("正在关闭系统资源...")
    if orchestrator:
        await orchestrator.background_planner_service.shutdown()
    logger.info("系统已关闭。")

app = FastAPI(
    title="Elderly Companion Agent API",
    description="企业级空巢老人陪伴系统后端接口",
    version="1.0.0",
    lifespan=lifespan
)

# 全局异常处理
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return error_response(exc.status_code, str(exc.detail))

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return error_response(422, "Invalid request parameters", details=str(exc))

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global Exception: {exc}")
    return error_response(500, "Internal Server Error")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ORIGINS,
    allow_credentials=Config.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEBUG_UI_DIR = os.path.join(ROOT_DIR, "frontend")
if os.path.isdir(DEBUG_UI_DIR):
    app.mount("/debug", StaticFiles(directory=DEBUG_UI_DIR, html=True), name="debug-ui")

# 请求日志与 ID 中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    logger.info(f"Request started: {request.method} {request.url.path} [ID: {request_id}]")
    
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info(f"Request completed: {response.status_code} [ID: {request_id}] - {process_time:.2f}ms")
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(f"Request failed: {str(e)} [ID: {request_id}] - {process_time:.2f}ms")
        return error_response(500, "Internal Server Error", request_id=request_id)

@app.post("/api/chat1")
@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    """
    流式响应接口 (SSE)
    """
    message = (payload.message or "").strip()
    context = dict(payload.context or {})

    if payload.user_id:
        payload_user_id = str(payload.user_id).strip()
        context_user_id = str(context.get("user_id") or "").strip()
        if context_user_id and context_user_id != payload_user_id:
            raise HTTPException(
                status_code=400,
                detail="Conflicting user_id between request body and context",
            )
        context["user_id"] = payload_user_id

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    async def event_generator():
        try:
            use_full_agent = (
                str(context.get("mode") or "").strip().lower() in {"agent", "full", "full_agent"}
                or bool(context.get("force_agent"))
            )
            stream_fn = None if use_full_agent else getattr(orchestrator, "process_chat_stream", None)
            if not callable(stream_fn):
                stream_fn = orchestrator.process_input_stream
            async for event_data in stream_fn(message, context):
                # SSE 格式: data: <json>\n\n
                yield f"data: {event_data}\n\n"
                await asyncio.sleep(0)
        except Exception as e:
            logger.error(f"流式生成错误: {e}")
            error_json = json.dumps(
                {
                    "type": "error",
                    "data": {
                        "code": "api_chat_stream_failed",
                        "source": "api_chat",
                        "retryable": True,
                    },
                },
                ensure_ascii=False,
            )
            yield f"data: {error_json}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/profile")
async def update_profile(request: Request, user_id: str = Query("user_001")):
    """
    更新用户画像接口
    支持批量更新字段，如 name, health_condition, family_members, preferences 等
    """
    try:
        profile = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        # 遍历所有字段并更新
        # rag_helper.update_user_profile 会处理文件读写和列表合并逻辑
        if not isinstance(profile, dict):
            raise HTTPException(status_code=400, detail="Profile body must be an object")
        elder_user_id = profile.pop("user_id", None) or user_id
        updated_profile = orchestrator.user_context_service.update_profile(elder_user_id, profile)
        return {
            "status": "success",
            "message": "Profile updated successfully",
            "user_id": elder_user_id,
            "updated_keys": list(profile.keys()),
            "profile": updated_profile
        }
            # 这里调用同步方法，如果是大量写入可能需要 run_in_executor，但画像更新通常量小
            
    except Exception as e:
        logger.error(f"更新画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profile")
async def get_profile(user_id: str = Query("user_001")):
    """
    获取当前用户画像
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
        
    try:
        return orchestrator.user_context_service.get_profile(user_id)
    except Exception as e:
        logger.error(f"获取画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """
    健康检查接口
    """
    status = "ok" if orchestrator else "initializing"
    return {
        "status": status,
        "version": app.version,
        "environment": os.getenv("ENV", "development")
    }

@app.get("/api/system_status")
async def get_system_status(user_id: str = Query("user_001")):
    """
    获取系统结构化信息：包含最近的路由决策、用户画像、对话历史、工具调用记录等。
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        rag = orchestrator.emotional_agent.rag_helper
        
        # 1. 用户画像
        profile = orchestrator.user_context_service.get_profile(user_id)
        
        # 2. 简要对话记录 (最近 6 条)
        history = orchestrator.user_context_service.get_recent_history(user_id, limit=6)
            
        # 3. 路由与工具决策分析
        system_state = getattr(orchestrator, "last_system_state", {})
        
        return {
            "status": "success",
            "data": {
                "routing_decision": {
                    "last_input": system_state.get("last_input", ""),
                    "routed_agent": system_state.get("last_route", "unknown")
                },
                "tool_calls_analysis": system_state.get("tool_calls", []),
                "llm_inputs": system_state.get("llm_inputs", []),
                "agent_context": system_state.get("agent_context", {}),
                "context_snapshot": system_state.get("context_snapshot", {}),
                "background_tasks": system_state.get("background_tasks", []),
                "user_profile": profile,
                "recent_chat_history": history
            }
        }
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/planner/status")
async def get_planner_status(elder_user_id: str = Query("user_001")):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    status = orchestrator.background_planner_service.get_status(elder_user_id)
    care_plan = orchestrator.care_plan_service.get_plan(elder_user_id)
    return {
        "status": "success",
        "data": {
            "planner": model_to_dict(status),
            "care_plan": model_to_dict(care_plan),
        },
    }

@app.get("/api/medication/plans")
async def get_medication_plans(
    elder_user_id: str = Query("user_001"),
    include_inactive: bool = Query(False),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    plans = orchestrator.medication_reminder_service.list_plans(
        elder_user_id,
        include_inactive=include_inactive,
    )
    return {"status": "success", "data": [model_to_dict(plan) for plan in plans]}


@app.post("/api/medication/plans")
async def create_medication_plan(request: Request):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Medication plan body must be an object")

    body.setdefault("medication_id", f"med_{uuid.uuid4().hex}")
    plan = MedicationPlan(**body)
    saved = orchestrator.medication_reminder_service.upsert_plan(plan)
    return {"status": "success", "data": model_to_dict(saved)}


@app.patch("/api/medication/plans/{medication_id}")
async def patch_medication_plan(
    medication_id: str,
    request: Request,
    elder_user_id: str = Query("user_001"),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Medication plan body must be an object")

    plans = orchestrator.medication_reminder_service.list_plans(
        elder_user_id,
        include_inactive=True,
    )
    existing = next((plan for plan in plans if plan.medication_id == medication_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="Medication plan not found")

    merged = model_to_dict(existing)
    merged.update(body)
    merged["medication_id"] = medication_id
    merged["elder_user_id"] = elder_user_id
    plan = MedicationPlan(**merged)
    saved = orchestrator.medication_reminder_service.upsert_plan(plan)
    return {"status": "success", "data": model_to_dict(saved)}


@app.get("/api/timed_events/due")
async def get_due_timed_events(
    elder_user_id: str = Query("user_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    events = orchestrator.get_due_timed_events(
        elder_user_id,
        now=parse_optional_datetime(now),
    )
    return {"status": "success", "data": [orchestrator.format_timed_event_response(event) for event in events]}


@app.post("/api/timed_events/{event_id}/ack")
async def acknowledge_timed_event(
    event_id: str,
    request: Request,
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Timed event ack body must be an object")

    ack = TimedEventAck(**body)
    try:
        result = orchestrator.acknowledge_timed_event(
            event_id,
            ack,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@app.post("/api/action_complete")
async def complete_action(payload: ActionCompleteRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        result = orchestrator.complete_action(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = dict(result)
    data["session"] = model_to_dict(data["session"])
    return {"status": "success", "data": data}


@app.get("/api/actions/pending")
async def list_pending_actions(
    elder_user_id: str = Query("user_001"),
    target_channel: str = Query("frontend"),
    limit: Optional[int] = Query(None, ge=1),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        actions = orchestrator.list_pending_actions(
            elder_user_id,
            target_channel=target_channel,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": [model_to_dict(item) for item in actions]}


@app.get("/api/frontend/actions/pending")
async def list_pending_frontend_actions(
    elder_user_id: str = Query("user_001"),
    risk_tier: str = Query("safe"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        actions = orchestrator.list_pending_frontend_actions(
            elder_user_id,
            risk_tier=risk_tier,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": actions}


@app.post("/api/actions/{action_id}/consent")
async def consent_action(action_id: str, payload: ActionConsentRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        result = orchestrator.consent_action(action_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = dict(result)
    data["session"] = model_to_dict(data["session"])
    return {"status": "success", "data": data}


@app.post("/api/photo_library/sync")
async def sync_photo_library(payload: PhotoLibrarySyncRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        result = orchestrator.photo_library_service.sync_photos(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@app.post("/api/photo_library/import")
async def import_photo_library(
    request: Request,
    elder_user_id: str = Query("user_001"),
    file_name: str = Query("photo_library.json"),
    source: str = Query("frontend_album"),
    sync_mode: str = Query("upsert"),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    content = await request.body()
    try:
        result = orchestrator.photo_library_service.import_library_bytes(
            elder_user_id,
            content,
            file_name=file_name,
            source=source,
            sync_mode=sync_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@app.get("/api/photo_library/photos")
async def list_photo_library(
    elder_user_id: str = Query("user_001"),
    query: str = Query(""),
    limit: int = Query(20),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    photos = orchestrator.photo_library_service.search_photos(
        elder_user_id,
        query,
        limit=limit,
    )
    return {"status": "success", "data": [model_to_dict(photo) for photo in photos]}


@app.post("/api/photo_library/caption_pending")
async def caption_pending_photo_library(
    request: Request,
    elder_user_id: str = Query("user_001"),
    limit: int = Query(5),
    force: bool = Query(False),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    body = {}
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    try:
        result = orchestrator.photo_library_service.caption_pending(
            elder_user_id,
            photo_ids=body.get("photo_ids"),
            limit=limit,
            force=force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": model_to_dict(result)}


@app.post("/api/music/library")
async def sync_music_library(payload: MusicLibrarySyncRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        result = orchestrator.music_library_service.sync_library(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@app.get("/api/music/library")
async def list_music_library(
    elder_user_id: str = Query("user_001"),
    include_inactive: bool = Query(False),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    songs = orchestrator.music_library_service.list_records(
        elder_user_id,
        include_inactive=include_inactive,
    )
    return {"status": "success", "data": [model_to_dict(song) for song in songs]}


@app.get("/api/music/library/match")
async def match_music_library(
    elder_user_id: str = Query("user_001"),
    query: str = Query(""),
    limit: int = Query(1),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    result = orchestrator.music_library_service.match_song(
        elder_user_id,
        query,
        limit=limit,
    )
    return {"status": "success", "data": result}


@app.get("/api/family/agent_policy")
async def get_family_agent_policy(
    elder_user_id: str = Query("user_001"),
    child_user_id: str = Query("child_001"),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    policy = orchestrator.family_policy_service.get_policy(elder_user_id, child_user_id)
    return {"status": "success", "data": model_to_dict(policy)}


@app.post("/api/family/agent_policy")
async def update_family_agent_policy(payload: FamilyPolicyUpdateRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    policy = orchestrator.family_policy_service.update_policy_from_payload(
        payload.elder_user_id,
        payload.child_user_id,
        payload.policy,
    )
    return {"status": "success", "data": model_to_dict(policy)}


@app.get("/api/family/topics/available")
async def get_available_family_topics(
    elder_user_id: str = Query("user_001"),
    child_user_id: str = Query("child_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    topics = orchestrator.family_policy_service.available_topics(
        elder_user_id,
        child_user_id,
        now=parse_optional_datetime(now),
    )
    return {"status": "success", "data": [model_to_dict(topic) for topic in topics]}


@app.post("/api/family/topics/{topic_id}/consume")
async def consume_family_topic(
    topic_id: str,
    elder_user_id: str = Query("user_001"),
    child_user_id: str = Query("child_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        topic = orchestrator.family_policy_service.consume_topic(
            elder_user_id,
            child_user_id,
            topic_id,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": model_to_dict(topic)}


@app.post("/api/family/messages")
async def create_family_message(payload: FamilyMessageCreateRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    message = orchestrator.create_family_message(payload)
    data = model_to_dict(message)
    data.pop("content", None)
    return {"status": "success", "data": data}


@app.get("/api/family/alerts")
async def get_family_alerts(
    elder_user_id: str = Query("user_001"),
    child_user_id: str = Query("child_001"),
    limit: int = Query(20),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    alerts = orchestrator.family_policy_service.list_family_alerts(
        elder_user_id,
        limit=limit,
    )
    return {
        "status": "success",
        "data": {
            "elder_user_id": elder_user_id,
            "child_user_id": child_user_id,
            "alerts": [model_to_dict(alert) for alert in alerts],
        },
    }


@app.get("/api/family/elder_summary")
async def get_family_elder_summary(
    elder_user_id: str = Query("user_001"),
    child_user_id: str = Query("child_001"),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    summary = orchestrator.get_family_elder_summary(elder_user_id, child_user_id)
    return {"status": "success", "data": summary}


@app.post("/api/family/chat")
async def family_chat_endpoint(payload: FamilyChatRequest):
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    async def event_generator():
        try:
            async for event_data in orchestrator.process_family_chat_stream(payload):
                yield f"data: {event_data}\n\n"
        except Exception as e:
            logger.error(f"family chat stream failed: {e}")
            error_json = json.dumps({"type": "error", "data": str(e)}, ensure_ascii=False)
            yield f"data: {error_json}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/elder/pending_messages")
async def get_elder_pending_messages(
    elder_user_id: str = Query("user_001"),
    risk_tier: str = Query("safe"),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    messages = orchestrator.get_elder_pending_messages(elder_user_id, risk_tier=risk_tier)
    return {"status": "success", "data": {"messages": messages}}


@app.post("/api/elder/messages/{message_id}/consent")
async def consent_to_elder_message(message_id: str, payload: QuietMessageConsentRequest):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        result = orchestrator.consent_to_elder_message(message_id, payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    data = dict(result)
    data["message"] = model_to_dict(data["message"])
    if data["status"] != "accepted":
        data["message"].pop("content", None)
    return {"status": "success", "data": data}


@app.post("/api/community/announcements")
async def create_community_announcement(
    payload: CommunityAnnouncementCreateRequest,
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        announcement = orchestrator.create_community_announcement(
            payload,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": model_to_dict(announcement)}


@app.get("/api/community/announcements")
async def get_community_announcements(
    community_id: str = Query("community_001"),
    only_active: bool = Query(True),
    now: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    announcements = orchestrator.list_community_announcements(
        community_id,
        only_active=only_active,
        now=parse_optional_datetime(now),
        limit=limit,
    )
    return {"status": "success", "data": [model_to_dict(item) for item in announcements]}


@app.patch("/api/community/announcements/{announcement_id}")
async def update_community_announcement(
    announcement_id: str,
    payload: CommunityAnnouncementUpdateRequest,
    community_id: str = Query("community_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        announcement = orchestrator.update_community_announcement(
            community_id,
            announcement_id,
            payload,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"status": "success", "data": model_to_dict(announcement)}


@app.delete("/api/community/announcements/{announcement_id}")
async def delete_community_announcement(
    announcement_id: str,
    community_id: str = Query("community_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        announcement = orchestrator.delete_community_announcement(
            community_id,
            announcement_id,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"status": "success", "data": model_to_dict(announcement)}


@app.post("/api/community/activities")
async def create_community_activity(
    payload: CommunityActivityCreateRequest,
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        activity = orchestrator.create_community_activity(
            payload,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": model_to_dict(activity)}


@app.get("/api/community/activities")
async def get_community_activities(
    community_id: str = Query("community_001"),
    only_active: bool = Query(True),
    now: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    activities = orchestrator.list_community_activities(
        community_id,
        only_active=only_active,
        now=parse_optional_datetime(now),
        limit=limit,
    )
    return {"status": "success", "data": [model_to_dict(item) for item in activities]}


@app.patch("/api/community/activities/{activity_id}")
async def update_community_activity(
    activity_id: str,
    payload: CommunityActivityUpdateRequest,
    community_id: str = Query("community_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        activity = orchestrator.update_community_activity(
            community_id,
            activity_id,
            payload,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"status": "success", "data": model_to_dict(activity)}


@app.delete("/api/community/activities/{activity_id}")
async def delete_community_activity(
    activity_id: str,
    community_id: str = Query("community_001"),
    now: Optional[str] = Query(None),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        activity = orchestrator.delete_community_activity(
            community_id,
            activity_id,
            now=parse_optional_datetime(now),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"status": "success", "data": model_to_dict(activity)}


@app.get("/api/community/crisis_alerts")
async def get_community_crisis_alerts(
    elder_user_id: Optional[str] = Query(None),
    community_id: str = Query("community_001"),
    group_by: str = Query("elder"),
    limit: int = Query(20),
):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    if elder_user_id:
        alerts = orchestrator.list_community_crisis_alerts(elder_user_id, limit=limit)
        return {"status": "success", "data": {"elder_user_id": elder_user_id, "alerts": alerts}}

    data = orchestrator.list_community_crisis_alerts_by_community(
        community_id,
        group_by=group_by,
        limit=limit,
    )
    return {"status": "success", "data": data}


@app.get("/api/proactive_check")
async def check_proactive_event(user_id: str = Query("user_001"), now: Optional[str] = Query(None)):
    """
    前端定期调用此接口 (如每分钟)，检查是否有主动提问生成
    """
    if not orchestrator:
        return JSONResponse(status_code=503, content={"error": "System not ready"})
        
    event_json = await orchestrator.check_and_generate_proactive_event(
        user_id=user_id,
        now=parse_optional_datetime(now),
    )
    if event_json:
        # event_json is already a JSON string from create_event
        # Parse it to return as normal JSON response
        try:
            event_data = json.loads(event_json)
            return JSONResponse(content=event_data)
        except:
            return JSONResponse(content={"type": "none"})
            
    return JSONResponse(content={"type": "none"})

@app.post("/api/reset_profile")
async def reset_profile(user_id: str = Query("user_001")):
    """
    仅清空用户画像（保留聊天历史和向量库等其他记忆）
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        if hasattr(orchestrator, "user_context_service"):
            default_profile = orchestrator.user_context_service.reset_profile(user_id)
        else:
            default_profile = orchestrator.emotional_agent.rag_helper.reset_profile()
        return {
            "status": "success",
            "message": "User profile has been reset to default.",
            "user_id": user_id,
            "profile": default_profile
        }
    except Exception as e:
        logger.error(f"重置画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reset_memory")
async def reset_memory(
    user_id: str = Query("user_001"),
    include_legacy_rag: bool = Query(False),
):
    """
    Reset one user's current DataStore state.

    Legacy RAG memory is global in the current codebase. It is not reset unless
    include_legacy_rag=true is explicitly provided.
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    try:
        user_id = getattr(user_id, "default", user_id)
        include_legacy_rag = getattr(include_legacy_rag, "default", include_legacy_rag)
        if isinstance(include_legacy_rag, str):
            include_legacy_rag = include_legacy_rag.strip().lower() in {"1", "true", "yes", "on"}
        else:
            include_legacy_rag = bool(include_legacy_rag)

        if hasattr(orchestrator, "reset_user_state"):
            result = orchestrator.reset_user_state(
                user_id,
                include_legacy_rag=include_legacy_rag,
            )
            if inspect.isawaitable(result):
                result = await result
        else:
            normalized_user_id = user_id
            user_context = getattr(orchestrator, "user_context_service", None)
            if user_context is not None and hasattr(user_context, "normalize_user_id"):
                normalized_user_id = user_context.normalize_user_id(user_id)

            data_store = getattr(orchestrator, "data_store", None)
            if data_store is None or not hasattr(data_store, "reset_user_state"):
                if not include_legacy_rag:
                    raise RuntimeError("Current orchestrator does not expose DataStore reset")
                rag = getattr(getattr(orchestrator, "emotional_agent", None), "rag_helper", None)
                if rag is None or not hasattr(rag, "reset_all_memory"):
                    raise RuntimeError("Current orchestrator does not expose DataStore or legacy RAG reset")
                result = {
                    "user_id": normalized_user_id,
                    "data_store": None,
                    "planner": None,
                    "legacy_rag": {
                        "requested": True,
                        "scope": "global",
                        "result": rag.reset_all_memory(),
                    },
                }
                return {
                    "status": "success",
                    "message": "User state has been reset.",
                    "user_id": result.get("user_id", user_id),
                    "data": result,
                }

            result = {
                "user_id": normalized_user_id,
                "data_store": data_store.reset_user_state(normalized_user_id),
                "planner": None,
                "legacy_rag": {
                    "requested": include_legacy_rag,
                    "scope": "not_touched",
                    "result": None,
                },
            }
            if include_legacy_rag:
                rag = getattr(getattr(orchestrator, "emotional_agent", None), "rag_helper", None)
                if rag is not None and hasattr(rag, "reset_all_memory"):
                    result["legacy_rag"] = {
                        "requested": True,
                        "scope": "global",
                        "result": rag.reset_all_memory(),
                    }

        return {
            "status": "success",
            "message": "User state has been reset.",
            "user_id": result.get("user_id", user_id),
            "data": result,
        }

    except Exception as e:
        logger.error(f"Reset memory failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")

if __name__ == "__main__":
    logger.info(f"启动服务: {Config.SERVER_HOST}:{Config.SERVER_PORT}")
    uvicorn.run(
        "src.server:app", 
        host=Config.SERVER_HOST, 
        port=Config.SERVER_PORT, 
        reload=True
    )
