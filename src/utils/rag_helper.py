import os
import json
from datetime import datetime
from typing import List, Dict, Any
from langchain_community.document_loaders import TextLoader, DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from filelock import FileLock
from src.config import Config
from src.utils.logger import logger

class RAGHelper:
    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            openai_api_key=Config.OPENAI_API_KEY,
            openai_api_base=Config.OPENAI_API_BASE,
            model=Config.EMBEDDING_MODEL,
            timeout=120,    
            max_retries=3,  
            check_embedding_ctx_length=False 
        )
        
        # 初始化多向量库 (按集合区分)
        self.vector_stores = {}
        self._init_vector_stores()
        
        # 简单的文件持久化记忆路径
        self.memory_file = Config.CHAT_HISTORY_PATH
        self.profile_file = os.path.join(os.path.dirname(self.memory_file), "user_profile.json")
        self.emotion_file = os.path.join(os.path.dirname(self.memory_file), "emotion_log.json")
        self.agent_status_file = os.path.join(os.path.dirname(self.memory_file), "agent_status.json")
        
        # 定义文件锁
        self.memory_lock = FileLock(f"{self.memory_file}.lock")
        self.profile_lock = FileLock(f"{self.profile_file}.lock")
        self.emotion_lock = FileLock(f"{self.emotion_file}.lock")
        self.agent_status_lock = FileLock(f"{self.agent_status_file}.lock")
        
        self._ensure_memory_file()
        self._ensure_profile_file()
        self._ensure_emotion_file()
        self._ensure_agent_status_file()

    def _build_default_profile(self) -> Dict[str, Any]:
        return {
            "name": "未知",
            "health_condition": [],
            "family_members": [],
            "preferences": [],
            "dialect": "unknown"
        }

    def _normalize_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._build_default_profile()
        if isinstance(profile, dict):
            normalized.update(profile)
        for key in ["health_condition", "family_members", "preferences"]:
            if not isinstance(normalized.get(key), list):
                normalized[key] = []
        if not isinstance(normalized.get("dialect"), str):
            normalized["dialect"] = "unknown"
        if not isinstance(normalized.get("name"), str):
            normalized["name"] = "未知"
        return normalized

    def _ensure_agent_status_file(self):
        """确保 Agent 状态文件存在"""
        with self.agent_status_lock:
            if not os.path.exists(self.agent_status_file):
                default_status = self._build_default_agent_status()
                with open(self.agent_status_file, "w", encoding="utf-8") as f:
                    json.dump(default_status, f, ensure_ascii=False, indent=2)

    def _build_default_agent_status(self) -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "last_user_interaction": now_str,
            "last_proactive_time": "2000-01-01 00:00:00",
            "last_proactive_domain": "",
            "last_proactive_content": "",
            "agent_last_update": {
                "medical": "2000-01-01 00:00:00",
                "daily_life": "2000-01-01 00:00:00",
                "interest": "2000-01-01 00:00:00",
                "mental_health": "2000-01-01 00:00:00",
                "emotional": "2000-01-01 00:00:00"
            }
        }

    def _normalize_agent_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        default_status = self._build_default_agent_status()
        merged = default_status.copy()
        merged.update(status or {})
        merged["agent_last_update"] = {
            **default_status["agent_last_update"],
            **(status.get("agent_last_update", {}) if status else {})
        }
        return merged

    def get_agent_status(self) -> Dict:
        """读取 Agent 状态"""
        try:
            with self.agent_status_lock:
                with open(self.agent_status_file, "r", encoding="utf-8") as f:
                    status = json.load(f)
                    return self._normalize_agent_status(status)
        except Exception:
            return {}

    def update_agent_status(self, user_interaction_time: str = None, agent_type: str = None, touch_user_interaction: bool = True):
        """
        更新 Agent 状态
        :param user_interaction_time: 用户最后交互时间 (YYYY-MM-DD HH:MM:SS)
        :param agent_type: 更新了哪个 Agent 的信息 (medical, daily_life, etc.)
        :param touch_user_interaction: 是否刷新最后用户交互时间
        """
        try:
            with self.agent_status_lock:
                status = {}
                if os.path.exists(self.agent_status_file):
                    with open(self.agent_status_file, "r", encoding="utf-8") as f:
                        status = json.load(f)
                status = self._normalize_agent_status(status)

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if user_interaction_time:
                    status["last_user_interaction"] = user_interaction_time
                elif agent_type and touch_user_interaction:
                    # 如果没有指定时间但指定了 agent，通常意味着用户刚刚和这个 agent 交互了
                    status["last_user_interaction"] = now_str
                    
                if agent_type:
                    status["agent_last_update"][agent_type] = now_str
                
                with open(self.agent_status_file, "w", encoding="utf-8") as f:
                    json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"ERROR: 更新 Agent 状态失败: {e}")

    def update_proactive_status(self, domain: str, content: str):
        """记录主动关怀触发信息，不影响最后用户交互时间。"""
        try:
            with self.agent_status_lock:
                status = {}
                if os.path.exists(self.agent_status_file):
                    with open(self.agent_status_file, "r", encoding="utf-8") as f:
                        status = json.load(f)
                status = self._normalize_agent_status(status)

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                status["last_proactive_time"] = now_str
                status["last_proactive_domain"] = domain
                status["last_proactive_content"] = content
                if domain in status["agent_last_update"]:
                    status["agent_last_update"][domain] = now_str

                with open(self.agent_status_file, "w", encoding="utf-8") as f:
                    json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"ERROR: 更新主动关怀状态失败: {e}")

    def _init_vector_stores(self):
        """初始化多个向量数据库集合"""
        # 1. 知识库集合
        self.vector_stores[Config.COLLECTION_KNOWLEDGE] = Chroma(
            collection_name=Config.COLLECTION_KNOWLEDGE,
            persist_directory=Config.VECTOR_DB_PATH,
            embedding_function=self.embeddings
        )
        # 2. 中期记忆(对话摘要)集合
        self.vector_stores[Config.COLLECTION_MEMORY] = Chroma(
            collection_name=Config.COLLECTION_MEMORY,
            persist_directory=Config.VECTOR_DB_PATH,
            embedding_function=self.embeddings
        )
        # 3. 生活事件(琐事)集合
        self.vector_stores[Config.COLLECTION_EVENTS] = Chroma(
            collection_name=Config.COLLECTION_EVENTS,
            persist_directory=Config.VECTOR_DB_PATH,
            embedding_function=self.embeddings
        )

    def get_vector_store(self, collection_name: str):
        """获取指定的向量库集合"""
        return self.vector_stores.get(collection_name)

    def _ensure_memory_file(self):
        """确保对话历史文件存在"""
        with self.memory_lock:
            if not os.path.exists(self.memory_file):
                os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
                with open(self.memory_file, "w", encoding="utf-8") as f:
                    json.dump([], f)
                return

            needs_rewrite = False
            raw_history: Any = []
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    raw_history = json.load(f)
            except Exception:
                needs_rewrite = True

            normalized = self._normalize_history_records(raw_history)
            if not isinstance(raw_history, list):
                needs_rewrite = True
            elif len(normalized) != len(raw_history):
                needs_rewrite = True

            if needs_rewrite:
                with open(self.memory_file, "w", encoding="utf-8") as f:
                    json.dump(normalized, f, ensure_ascii=False, indent=2)
                logger.warning("检测到 chat_history.json 非标准格式，已自动修复为列表结构。")

    def _normalize_history_records(self, raw_history: Any) -> List[Dict[str, Any]]:
        """
        兼容历史格式漂移：
        - 正常格式：list[dict]
        - 误写格式：dict（将自动修复为 list）
        """
        def is_valid_record(item):
            return isinstance(item, dict) and "role" in item and "content" in item

        if isinstance(raw_history, list):
            return [item for item in raw_history if is_valid_record(item)]
        
        if isinstance(raw_history, dict):
            # 常见错误：{"history": [...]} 或 {"messages": [...]} 这类包装结构
            for key in ("history", "messages", "records"):
                nested = raw_history.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if is_valid_record(item)]
            # 常见错误：把单条消息写成了整个 history
            if is_valid_record(raw_history):
                return [raw_history]
            return []
        return []

    def _ensure_profile_file(self):
        """确保用户画像文件存在"""
        with self.profile_lock:
            if not os.path.exists(self.profile_file):
                default_profile = self._build_default_profile()
                with open(self.profile_file, "w", encoding="utf-8") as f:
                    json.dump(default_profile, f, ensure_ascii=False, indent=2)

    def _ensure_emotion_file(self):
        """确保情感日志文件存在"""
        with self.emotion_lock:
            if not os.path.exists(self.emotion_file):
                with open(self.emotion_file, "w", encoding="utf-8") as f:
                    json.dump([], f)
                return

            needs_rewrite = False
            raw_logs: Any = []
            try:
                with open(self.emotion_file, "r", encoding="utf-8") as f:
                    raw_logs = json.load(f)
            except Exception:
                needs_rewrite = True

            logs = self._normalize_emotion_logs(raw_logs)
            if not isinstance(raw_logs, list):
                needs_rewrite = True
            elif len(logs) != len(raw_logs):
                needs_rewrite = True

            if needs_rewrite:
                with open(self.emotion_file, "w", encoding="utf-8") as f:
                    json.dump(logs, f, ensure_ascii=False, indent=2)
                logger.warning("检测到 emotion_log.json 非标准格式，已自动修复为列表结构。")

    def _normalize_emotion_logs(self, raw_logs: Any) -> List[Dict[str, Any]]:
        if isinstance(raw_logs, list):
            return [item for item in raw_logs if isinstance(item, dict)]
        if isinstance(raw_logs, dict):
            nested = raw_logs.get("logs")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return []

    def log_emotion(self, emotion: str, risk_level: str):
        """记录情感状态和风险等级"""
        try:
            with self.emotion_lock:
                entry = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "emotion": emotion,
                    "risk_level": risk_level
                }
                
                # 读取现有日志
                logs = []
                if os.path.exists(self.emotion_file):
                    with open(self.emotion_file, "r", encoding="utf-8") as f:
                        try:
                            logs = self._normalize_emotion_logs(json.load(f))
                        except Exception:
                            logs = []
                
                # 追加新日志 (保留最近 50 条)
                logs.append(entry)
                if len(logs) > 50:
                    logs = logs[-50:]
                    
                with open(self.emotion_file, "w", encoding="utf-8") as f:
                    json.dump(logs, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"ERROR: 记录情感日志失败: {e}")

    def get_emotion_trend(self) -> str:
        """分析最近的情感趋势"""
        try:
            with self.emotion_lock:
                with open(self.emotion_file, "r", encoding="utf-8") as f:
                    logs = self._normalize_emotion_logs(json.load(f))
            
            if not logs:
                return "暂无情感记录"
                
            # 简单统计最近 5 次的风险等级
            recent_logs = logs[-5:]
            high_risk_count = sum(1 for log in recent_logs if log.get("risk_level") == "high")
            medium_risk_count = sum(1 for log in recent_logs if log.get("risk_level") == "medium")
            
            trend = "情绪稳定"
            if high_risk_count >= 1:
                trend = "极度危险 (检测到高风险信号)"
            elif medium_risk_count >= 2:
                trend = "情绪波动较大，需关注"
            
            return f"最近5次状态: {[log.get('risk_level') for log in recent_logs]} | 趋势评估: {trend}"
        except Exception:
            return "情感趋势分析失败"

    def get_user_profile(self) -> Dict:
        """读取用户画像"""
        try:
            with self.profile_lock:
                with open(self.profile_file, "r", encoding="utf-8") as f:
                    return self._normalize_profile(json.load(f))
        except Exception as exc:
            logger.warning(f"读取用户画像失败，返回默认画像: {exc}")
            return self._build_default_profile()

    def update_user_profile(self, key: str, value: Any):
        """更新用户画像字段"""
        try:
            with self.profile_lock:
                profile = self._build_default_profile()
                try:
                    with open(self.profile_file, "r", encoding="utf-8") as f:
                        profile = self._normalize_profile(json.load(f))
                except Exception as exc:
                    logger.warning(f"读取画像原始文件失败，使用默认画像继续更新: {exc}")
                
                # 对于列表类型的字段（如健康状况），支持追加或重置
                if key in ["health_condition", "family_members", "preferences"]:
                    if isinstance(value, list):
                        # 如果传入的是列表，直接覆盖（允许消除症状）
                        profile[key] = value
                    elif value not in profile[key]:
                        # 如果传入的是单项，则追加
                        profile[key].append(value)
                
                elif isinstance(value, dict) and key in profile and isinstance(profile[key], dict):
                    # 如果是字典，进行合并
                    profile[key].update(value)
                else:
                    profile[key] = value
                
                with open(self.profile_file, "w", encoding="utf-8") as f:
                    json.dump(profile, f, ensure_ascii=False, indent=2)
                logger.info(f"用户画像已更新 [{key}]: {value}")
        except Exception as e:
            logger.error(f"更新用户画像失败: {e}")
            raise

    def reset_profile(self) -> Dict[str, Any]:
        default_profile = self._build_default_profile()
        with self.profile_lock:
            with open(self.profile_file, "w", encoding="utf-8") as f:
                json.dump(default_profile, f, ensure_ascii=False, indent=2)
        logger.info("用户画像已重置")
        return default_profile

    def reset_all_memory(self) -> Dict[str, Any]:
        default_profile = self._build_default_profile()
        default_status = self._build_default_agent_status()

        with self.memory_lock:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False)
        with self.profile_lock:
            with open(self.profile_file, "w", encoding="utf-8") as f:
                json.dump(default_profile, f, ensure_ascii=False, indent=2)
        with self.emotion_lock:
            with open(self.emotion_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False)
        with self.agent_status_lock:
            with open(self.agent_status_file, "w", encoding="utf-8") as f:
                json.dump(default_status, f, ensure_ascii=False, indent=2)

        for name, store in self.vector_stores.items():
            try:
                if hasattr(store, "get") and hasattr(store, "delete"):
                    all_ids = store.get().get("ids", [])
                    if all_ids:
                        store.delete(ids=all_ids)
            except Exception as exc:
                logger.warning(f"清空向量集合 {name} 失败: {exc}")

        logger.info("系统记忆已重置")
        return {
            "profile": default_profile,
            "agent_status": default_status
        }

    def index_exists(self, collection_name=Config.COLLECTION_KNOWLEDGE):
        try:
            store = self.get_vector_store(collection_name)
            if not store: return False
            count = store._collection.count()
            return count > 0
        except Exception:
            return False

    def load_and_index_documents(self, force=False):
        """加载知识库文件并重建索引 (知识库集合)"""
        if self.index_exists(Config.COLLECTION_KNOWLEDGE) and not force:
            print("DEBUG: 检测到知识库已存在数据，跳过重新索引。")
            return "Skipped indexing (already exists)."

        if not os.path.exists(Config.KNOWLEDGE_BASE_PATH):
            os.makedirs(Config.KNOWLEDGE_BASE_PATH)
            return "Knowledge base directory created, but is empty."

        # 加载所有 .txt 文件
        print(f"DEBUG: 正在从 {Config.KNOWLEDGE_BASE_PATH} 加载文件...")
        txt_loader = DirectoryLoader(Config.KNOWLEDGE_BASE_PATH, glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"})
        txt_documents = txt_loader.load()

        # 加载所有 .pdf 文件
        pdf_loader = DirectoryLoader(Config.KNOWLEDGE_BASE_PATH, glob="**/*.pdf", loader_cls=PyPDFLoader)
        pdf_documents = pdf_loader.load()

        documents = txt_documents + pdf_documents
        print(f"DEBUG: 已加载 {len(documents)} 个文档")

        if not documents:
            return "No documents found in knowledge base."

        # 文本分割
        print("DEBUG: 正在进行文本分割...")
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        splits = text_splitter.split_documents(documents)
        print(f"DEBUG: 分割完成，共生成 {len(splits)} 个片段")

        # 存入向量库 (Knowledge Collection)
        print("DEBUG: 正在调用 Embedding API 生成向量并存入数据库...")
        store = self.get_vector_store(Config.COLLECTION_KNOWLEDGE)
        store.add_documents(documents=splits)
        
        print("DEBUG: 向量数据库构建完成")
        return f"Indexed {len(splits)} chunks from {len(documents)} files."

    def add_memory(self, user_input: str, agent_response: str):
        """
        保存对话记录（短期/长期记忆）
        1. 存入 JSON 文件作为完整的对话历史（Context Window）
        2. 每 5 轮对话触发一次摘要，存入向量数据库作为中期记忆
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. 存入 JSON (完整历史 - 短期记忆)
        entry = {
            "timestamp": timestamp,
            "role": "user",
            "content": user_input
        }
        resp_entry = {
            "timestamp": timestamp,
            "role": "assistant",
            "content": agent_response
        }
        
        current_history = []
        try:
            with self.memory_lock:
                with open(self.memory_file, "r+", encoding="utf-8") as f:
                    try:
                        raw_history = json.load(f)
                    except Exception:
                        raw_history = []
                    current_history = self._normalize_history_records(raw_history)
                    current_history.append(entry)
                    current_history.append(resp_entry)
                    
                    # 限制最大短期记忆长度，防止无限增长导致 OOM 和 Token 超限
                    if len(current_history) > 100:
                        # 总是保留偶数条（一问一答），保留最后 60 条
                        current_history = current_history[-60:]
                        
                    f.seek(0)
                    json.dump(current_history, f, ensure_ascii=False, indent=2)
                    f.truncate()
        except Exception as e:
            print(f"ERROR: 写入历史文件失败: {e}")

        # 2. 触发中期记忆摘要 (每 5 轮 = 10 条消息)
        if len(current_history) % 10 == 0 and len(current_history) > 0:
            self._summarize_and_store_memory(current_history[-10:])

    def _summarize_and_store_memory(self, recent_messages: List[Dict]):
        """
        [中期记忆] 对最近的对话进行摘要，并存入向量库 (Memory Collection)
        """
        print("DEBUG: 触发中期记忆摘要...")
        
        # 简单拼接最近对话
        conversation_text = ""
        for msg in recent_messages:
            role = "老人" if msg["role"] == "user" else "小暖"
            conversation_text += f"{role}: {msg['content']}\n"
            
        try:
            # 初始化 LLM 用于生成摘要
            llm = ChatOpenAI(
                openai_api_key=Config.OPENAI_API_KEY,
                openai_api_base=Config.OPENAI_API_BASE,
                model_name=Config.MODEL_NAME,
                temperature=0.3
            )
            
            prompt = f"""
            请根据以下对话片段，提取**老人（用户）**提供的关键信息。
            
            **严格遵守以下原则**：
            1. **只记录老人提到的事实**（如健康状况、亲友姓名、生活琐事、情感需求）。
            2. **忽略并过滤**“小暖”（AI）为了活跃气氛而提到的虚构人物、故事或比喻。不要把AI编造的内容当成事实记录！
            3. 如果老人没有提供新信息，仅摘要对话主题（如“进行了关于天气的闲聊”）。
            4. **禁止输出分析过程、列表符号或Markdown格式**。直接输出一段自然语言描述，像这样：“老人提到孙子叫阿伟，今天因为想念孙子感到难过，想看阿伟的照片。”
            
            对话内容：
            {conversation_text}
            """
            
            response = llm.invoke([HumanMessage(content=prompt)])
            summary = response.content
            
            # 存入向量库 (Memory Collection)
            store = self.get_vector_store(Config.COLLECTION_MEMORY)
            store.add_texts(
                texts=[summary], # 存入摘要而非原文
                metadatas=[{
                    "type": "chat_summary", 
                    "timestamp": datetime.now().strftime("%Y-%m-%d"),
                    "original_snippet": conversation_text[:200] # 保留部分原文用于参考
                }]
            )
            print(f"DEBUG: 已将对话摘要存入向量库 (中期记忆): {summary}")
            
        except Exception as e:
            print(f"ERROR: 生成摘要或添加向量索引失败: {e}")

    def add_daily_event(self, content: str):
        """
        [生活事件] 将日常事件存入向量库 (Events Collection)
        支持语义检索，例如“我上周去了哪”可以检索到“去公园散步”
        """
        try:
            store = self.get_vector_store(Config.COLLECTION_EVENTS)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            store.add_texts(
                texts=[content],
                metadatas=[{
                    "type": "daily_event",
                    "timestamp": timestamp,
                    "date": date_str
                }]
            )
            print(f"DEBUG: 已将生活事件存入向量库: {content}")
        except Exception as e:
            print(f"ERROR: 存储生活事件失败: {e}")

    def search_daily_events(self, query: str, k=5) -> List[str]:
        """
        [生活事件] 语义检索生活事件
        """
        try:
            store = self.get_vector_store(Config.COLLECTION_EVENTS)
            # 使用相似度搜索
            docs = store.similarity_search(query, k=k)
            
            results = []
            for doc in docs:
                # 格式化输出: [2023-10-01] 去了公园
                date = doc.metadata.get("date", "未知日期")
                results.append(f"[{date}] {doc.page_content}")
            
            return results
        except Exception as e:
            print(f"ERROR: 检索生活事件失败: {e}")
            return []

    def search_comprehensive_memory(self, query: str, k=3) -> str:
        """
        [综合记忆检索] 同时搜索知识库、对话摘要(中期记忆)和生活事件
        返回格式化的上下文字符串
        """
        results = []
        
        # 1. 搜索知识库 (Knowledge)
        try:
            store_know = self.get_vector_store(Config.COLLECTION_KNOWLEDGE)
            if store_know:
                docs = store_know.similarity_search(query, k=k)
                if docs:
                    results.append("【相关知识库】:\n" + "\n".join([d.page_content for d in docs]))
        except Exception as e:
            print(f"WARN: Knowledge search failed: {e}")

        # 2. 搜索中期记忆 (Chat Summaries)
        try:
            store_mem = self.get_vector_store(Config.COLLECTION_MEMORY)
            if store_mem:
                docs = store_mem.similarity_search(query, k=k)
                if docs:
                    summaries = []
                    for d in docs:
                        ts = d.metadata.get("timestamp", "")
                        summaries.append(f"[{ts}] {d.page_content}")
                    results.append("【历史对话记忆】:\n" + "\n".join(summaries))
        except Exception as e:
            print(f"WARN: Memory search failed: {e}")

        # 3. 搜索生活事件 (Daily Events)
        try:
            events = self.search_daily_events(query, k=k)
            if events:
                results.append("【生活事件记录】:\n" + "\n".join(events))
        except Exception as e:
            print(f"WARN: Event search failed: {e}")

        return "\n\n".join(results)

    def get_recent_history(self, limit=10) -> List[Dict]:
        """获取最近的对话历史 (用于 Context Window)"""
        try:
            with self.memory_lock:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    history = self._normalize_history_records(json.load(f))
                    return history[-limit:] # 返回最后 N 条
        except Exception:
            return []

    def get_retriever(self, k=3, collection_name=Config.COLLECTION_KNOWLEDGE):
        """获取检索器"""
        store = self.get_vector_store(collection_name)
        if store:
            return store.as_retriever(search_kwargs={"k": k})
        return None
