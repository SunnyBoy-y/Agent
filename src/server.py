import sys
import os
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List

# 确保能找到 src 包
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import uvicorn

from src.orchestrator import SystemOrchestrator
from src.config import Config
from src.utils.logger import logger

# 全局 Orchestrator 实例
orchestrator: SystemOrchestrator = None


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
    # 这里可以添加清理逻辑
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

@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    """
    流式响应接口 (SSE)
    """
    message = (payload.message or "").strip()
    context = dict(payload.context or {})

    if payload.user_id:
        context.setdefault("user_id", payload.user_id)

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")

    async def event_generator():
        try:
            async for event_data in orchestrator.process_input_stream(message, context):
                # SSE 格式: data: <json>\n\n
                yield f"data: {event_data}\n\n"
        except Exception as e:
            logger.error(f"流式生成错误: {e}")
            error_json = json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
            yield f"data: {error_json}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/profile")
async def update_profile(request: Request):
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
        for key, value in profile.items():
            # 这里调用同步方法，如果是大量写入可能需要 run_in_executor，但画像更新通常量小
            orchestrator.emotional_agent.rag_helper.update_user_profile(key, value)
            
        return {"status": "success", "message": "Profile updated successfully", "updated_keys": list(profile.keys())}
    except Exception as e:
        logger.error(f"更新画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profile")
async def get_profile():
    """
    获取当前用户画像
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
        
    try:
        return orchestrator.emotional_agent.rag_helper.get_user_profile()
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
async def get_system_status():
    """
    获取系统结构化信息：包含最近的路由决策、用户画像、对话历史、工具调用记录等。
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        rag = orchestrator.emotional_agent.rag_helper
        
        # 1. 用户画像
        profile = rag.get_user_profile()
        
        # 2. 简要对话记录 (最近 6 条)
        history = []
        try:
            with open(rag.memory_file, "r", encoding="utf-8") as f:
                full_history = json.load(f)
                history = full_history[-6:] if len(full_history) >= 6 else full_history
        except Exception:
            pass
            
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
                "user_profile": profile,
                "recent_chat_history": history
            }
        }
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/proactive_check")
async def check_proactive_event():
    """
    前端定期调用此接口 (如每分钟)，检查是否有主动提问生成
    """
    if not orchestrator:
        return JSONResponse(status_code=503, content={"error": "System not ready"})
        
    event_json = await orchestrator.check_and_generate_proactive_event()
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
async def reset_profile():
    """
    仅清空用户画像（保留聊天历史和向量库等其他记忆）
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        rag = orchestrator.emotional_agent.rag_helper
        default_profile = rag.reset_profile()
        return {"status": "success", "message": "User profile has been reset to default."}
    except Exception as e:
        logger.error(f"重置画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reset_memory")
async def reset_memory():
    """
    一键清空所有记忆（短期、中期、生活事件）和用户画像
    慎用！操作不可逆。
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        rag = orchestrator.emotional_agent.rag_helper
        rag.reset_all_memory()
        return {"status": "success", "message": "All memories and profiles have been reset."}
        
    except Exception as e:
        logger.error(f"重置记忆失败: {e}")
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")

if __name__ == "__main__":
    logger.info(f"启动服务: {Config.SERVER_HOST}:{Config.SERVER_PORT}")
    uvicorn.run(
        "src.server:app", 
        host=Config.SERVER_HOST, 
        port=Config.SERVER_PORT, 
        reload=True
    )
