import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:

    # LLM 配置
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    # 路径配置
    # Agent/src/config.py -> Agent/src -> Agent
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    
    # 统一数据存储路径
    VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", os.path.join(DATA_DIR, "vector_db"))
    
    # 向量库集合名称
    COLLECTION_KNOWLEDGE = "knowledge_base" # 知识库
    COLLECTION_MEMORY = "chat_memory"       # 中期记忆(摘要)
    COLLECTION_EVENTS = "daily_events"      # 生活事件(琐事)

    KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", os.path.join(DATA_DIR, "knowledge"))
    CHAT_HISTORY_PATH = os.path.join(DATA_DIR, "chat_history.json")
    
    # 模型配置
    MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")

    # 服务配置
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT = int(os.getenv("SERVER_PORT", "8082"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    EXTERNAL_REQUEST_TIMEOUT = float(os.getenv("EXTERNAL_REQUEST_TIMEOUT", "2.0"))

    # 外部依赖服务
    VISUAL_ANALYSIS_URL = os.getenv("VISUAL_ANALYSIS_URL", "http://localhost:8083/emotions")
    FILE_SERVICE_BASE_URL = os.getenv("FILE_SERVICE_BASE_URL", "http://localhost:8080")
    
    # 安全配置
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
    CORS_ALLOW_CREDENTIALS = _get_bool_env(
        "CORS_ALLOW_CREDENTIALS",
        default="*" not in CORS_ORIGINS
    )

    @classmethod
    def validate(cls):
        # 检查 Key 是否为空或为默认占位符
        invalid_keys = ["your_api_key_here", "your_qwen_api_key_here", ""]
        if not cls.OPENAI_API_KEY or cls.OPENAI_API_KEY in invalid_keys:
            print(f"Warning: OPENAI_API_KEY is not set correctly in .env file (Current: {cls.OPENAI_API_KEY}).")
            return False
        return True

# 确保必要的目录存在
os.makedirs(Config.VECTOR_DB_PATH, exist_ok=True)
os.makedirs(Config.KNOWLEDGE_BASE_PATH, exist_ok=True)
