# Elderly Companion Agent System (Backend)

这是一个专为“空巢老人陪伴系统”设计的后端智能体架构。它基于 **LangGraph** 构建，集成了多模态情感计算、RAG（检索增强生成）、反诈检测以及数字人驱动能力，旨在为老人提供温暖、安全、个性化的智能陪伴。

## 📁 项目结构与功能说明

本项目核心代码位于 `Agent/src` 目录下，采用分层架构设计：

```
Agent/
├── data/                  # 数据存储 (向量库、日志、画像、聊天记录)
├── src/
│   ├── agents/            # 智能体核心逻辑
│   │   ├── emotional_agent.py  # 情感连接智能体
│   │   └── antifraud_agent.py  # 反诈检测智能体
│   ├── tools/             # 工具/技能库
│   │   └── professional_skills.py
│   ├── utils/             # 通用工具类
│   │   ├── rag_helper.py       # RAG与记忆管理
│   │   └── logger.py           # 日志模块
│   ├── config.py          # 全局配置
│   ├── orchestrator.py    # 系统编排器 (中枢)
│   └── server.py          # API 服务入口
├── .env                   # 环境变量配置
└── requirements.txt       # 依赖列表
```

### 1. 核心服务层

#### `src/server.py`
**功能**：FastAPI 应用程序入口。
- **接口**：
  - `POST /api/chat`: 核心对话接口，支持 **SSE (Server-Sent Events)** 流式响应，实时推送思考过程、回复文本、表情指令等。
  - `GET /api/profile`: 获取当前老人的用户画像。
  - `POST /api/profile`: 更新老人的用户画像（支持批量更新）。
  - `GET /health`: 健康检查。
- **职责**：处理 HTTP 请求，生命周期管理，跨域配置，请求日志追踪。

#### `src/orchestrator.py`
**功能**：系统编排器 (System Orchestrator)。
- **职责**：
  - 初始化并管理各个 Agent 实例（单例模式）。
  - `process_input_stream`: 协调输入处理流程，将多模态数据（文本、语音转录、视觉情感）分发给 Agent。
  - **流式事件处理**：将 Agent 的内部运行状态转换为前端可消费的 SSE 事件流（Token, Log, Expression, Action, Risk）。
  - **实时过滤器**：包含括号/动作描述过滤逻辑，确保输出给用户的只有纯净的口语文本。

### 2. 智能体层 (Agents)

#### `src/agents/emotional_agent.py`
**功能**：**情感连接智能体 (EmotionalConnectionAgent)** —— 系统的核心大脑。
- **架构**：基于 **LangGraph** 的状态机 (StateGraph)。
- **流程**：`Analyze` (输入分析) -> `Retrieve` (记忆/知识检索) -> `Agent` (模型生成) <-> `Tools` (工具调用)。
- **关键特性**：
  - **双模型策略**：
    - `llm`: 低温模型，负责精准的工具调用决策。
    - `final_llm`: 适温模型，负责生成富有情感的最终回复，并绑定 `EmotionalStateUpdate` 工具以输出元数据。
  - **动态人设**：根据用户画像自动切换方言风格（东北、北京、川渝、江南等）。
  - **记忆注入**：自动加载短期对话历史、中期摘要记忆和长期用户画像。

#### `src/agents/antifraud_agent.py`
**功能**：**反诈检测智能体 (AntiFraudAgent)** —— 财产安全卫士。
- **职责**：实时分析对话内容，识别潜在的诈骗风险。
- **流程**：`analyze_fraud` (风险识别) -> `generate_intervention` (生成干预策略)。
- **输出**：风险等级 (Safe/Low/Medium/High) 及相应的干预动作（如通知子女、阻断通话）。

### 3. 工具与技能层 (Tools)

#### `src/tools/professional_skills.py`
**功能**：定义 Agent 可调用的具体技能 (LangChain Tools)。
- **包含技能**：
  - `search_family_photos`: 搜索家庭相册（模拟），用于回应老人的思念。
  - `emergency_contact`: 紧急联系人/社区报警，支持分级预警（Low/Medium/High）。
  - `record_health_complaint`: 记录健康主诉（如头疼、腿疼）到用户画像。

### 4. 基础设施层 (Utils & Config)

#### `src/utils/rag_helper.py`
**功能**：RAG (检索增强生成) 与数据持久化助手。
- **职责**：
  - **向量数据库**：管理 ChromaDB 实例，处理文档加载 (`load_and_index_documents`) 和检索。
  - **记忆系统**：
    - 短期记忆：读写 `chat_history.json`。
    - 中期记忆：生成对话摘要并存入向量库。
    - 长期记忆/画像：读写 `user_profile.json`，支持增量更新。
  - **情感日志**：记录并分析情感趋势 (`emotion_log.json`)。

#### `src/config.py`
**功能**：配置管理。
- **职责**：加载环境变量，定义路径常量，配置 LLM 参数（支持 OpenAI 接口兼容模型，如通义千问 Qwen）。

## 🚀 启动说明

1.  **环境准备**：
    确保已安装 Python 3.10+ 及依赖：
    ```bash
    pip install -r requirements.txt
    ```

2.  **配置**：
    在 `Agent/.env` 文件中配置 API Key 和其他参数。

3.  **运行服务**：
    ```bash
    python src/server.py
    ```
    服务默认启动在 `http://0.0.0.0:8001`。
