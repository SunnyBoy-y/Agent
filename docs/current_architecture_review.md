# 当前架构与已实现能力梳理

更新时间：2026-05-14

## 1. 当前定位

当前项目已经是一个面向老年陪伴场景的 Agent 后端雏形，主要能力集中在：

- 通过 FastAPI 提供流式对话接口。
- 用 Orchestrator 统一路由到多个领域 Agent。
- 用 RAGHelper 维护用户画像、短期聊天历史、中期摘要记忆、生活事件和情感日志。
- 用工具事件向前端发出照片、音乐、SOS、表情、动作、风险等级等信号。

从“老年数字人情感陪护系统”的目标看，当前系统已经具备基础陪伴、记忆、照片、音乐、主动问候和一部分风险识别能力，但“心理健康精准识别 + 分级 CBT 干预 + 后台计划闭环”还没有形成稳定机制。

## 2. 服务接口层

入口文件：

- `main.py`：命令行测试入口，直接实例化 `SystemOrchestrator`，模拟对话。
- `src/server.py`：FastAPI 服务入口。

已实现接口：

- `POST /api/chat`：核心 SSE 流式对话接口。
- `GET /api/profile`：读取当前用户画像。
- `POST /api/profile`：更新用户画像字段。
- `GET /health`：健康检查。
- `GET /api/system_status`：查看最近路由、工具调用、画像和最近对话。
- `GET /api/proactive_check`：前端轮询主动关怀事件。
- `POST /api/reset_profile`：重置画像。
- `POST /api/reset_memory`：清空记忆和画像。

当前前端可消费的事件类型包括：

- `token`：回复文本片段。
- `log`：运行日志。
- `step`：路由和 agent 执行阶段。
- `expression`：数字人表情。
- `action`：数字人动作或业务动作。
- `risk`：风险等级。
- `photos` / `photos_result`：相册检索结果。
- `music_payload` / `music`：音乐播放触发。
- `sos`：触发 SOS。
- `proactive_question`：主动关怀。
- `done`：结束标记。

## 3. 编排层

核心文件：`src/orchestrator.py`

`SystemOrchestrator` 当前职责：

- 初始化多个 Agent：
  - `RouterAgent`
  - `EmotionalConnectionAgent`
  - `MedicalAgent`
  - `DailyLifeAgent`
  - `InterestAgent`
  - `MentalHealthAgent`
  - `AntiFraudAgent`
  - `ProactiveAgent`
- 接收用户输入后先构造共享上下文：
  - 用户画像。
  - 最近对话。
  - 综合记忆检索。
  - 情感趋势。
  - agent 状态。
  - 视觉情绪接口结果。
- 用规则路由决定目标 Agent。
- 运行目标 Agent 并把内部结果标准化为 SSE 事件。
- 将完整回复写入记忆。
- 记录最近路由和工具调用，用于 `/api/system_status` 展示。

当前编排特征：

- 已有快速路由，默认不用 LLM 路由，延迟较低。
- 情感 Agent 是唯一流式 Agent，其他 Agent 非流式返回。
- 视觉情绪接口是非阻塞尝试，失败不会阻塞对话。
- 主动问候由独立接口轮询触发，不在对话返回后自动做后台计划。

## 4. Agent 层

### RouterAgent

文件：`src/agents/router_agent.py`

当前使用规则路由：

- 紧急身体不适、摔倒、胸闷等进入 `medical_agent`。
- 药、疼、晕等进入 `medical_agent`。
- 音乐请求进入 `interest_agent`。
- 强诈骗词进入 `antifraud_agent`。
- 明确生活记录进入 `daily_life_agent`。
- 其他内容默认进入 `emotional_agent`。

注意：心理健康相关输入目前大多数会默认进入 `emotional_agent`，`mental_health_agent` 主要通过强制路由或主动关怀间接使用。

### EmotionalConnectionAgent

文件：`src/agents/emotional_agent.py`

当前是核心陪伴 Agent，使用 LangGraph：

- `analyze`：融合文本、语音转写、语音情绪、视觉情绪、画像、历史和记忆。
- `agent`：调用绑定工具的大模型。
- `tools`：执行业务工具。

已绑定工具：

- `search_family_photos`
- `emergency_contact`
- `record_health_complaint`
- `EmotionalStateUpdate`

当前已具备的心理支持能力：

- 根据画像和情感趋势粗略检测 `anxiety` / `depression` / `bipolar` 倾向。
- 在系统提示词中注入简化 CBT 指导。
- 对“焦虑、抑郁、双相高涨期”等有一组关键词兜底风险判断。
- 每轮尽量产出表情、动作、风险等级和画像更新。

局限：

- 不是独立的心理评估模块，风险判断分散在提示词、关键词和工具调用中。
- 没有稳定的“评估证据、置信度、严重程度、下一步目标”结构化输出。
- CBT 还停留在提示词指导，没有形成可追踪的干预步骤和状态机。

### MentalHealthAgent

文件：`src/agents/mental_health_agent.py`

当前能力：

- 针对心理健康倾诉生成共情式回复。
- 对焦虑关键词有固定安抚逻辑，并可能推荐社区活动。
- 支持检索记忆和知识库。

局限：

- 路由层很少自然命中它。
- 只粗分 `anxiety` / `lonely` / `general`。
- 没有抑郁、双相躁期、自伤意念等分级流程。

### MedicalAgent

文件：`src/agents/medical_agent.py`

当前能力：

- 识别身体紧急情况。
- 查询用药计划。
- 记录症状到画像。
- 返回 `sos`、`action`、`risk_level`。

需要重点确认：

- 当前有“家庭医生助手”定位和健康建议表达，可能与项目背景中的“严禁医疗建议”规则冲突。
- `emergency_contact` 当前 high 级别会模拟拨打 120，这也需要和业务红线重新对齐。

### InterestAgent

文件：`src/agents/interest_agent.py`

当前能力：

- 处理戏曲、书法、园艺、下棋等兴趣话题。
- 识别音乐请求并通过 `play_music` 返回 `music_payload`。

局限：

- 只有“播放前回复”，缺少“唱歌结束后的结束语/话题引导”回调机制。
- 音乐 payload 还没有标准字段承载歌曲名、歌手、前置话术、后置话术、干预目的等。

### DailyLifeAgent

文件：`src/agents/daily_life_agent.py`

当前能力：

- 判断记录生活事件或查询历史事件。
- 事件存入 Chroma 的 `daily_events` 集合。

局限：

- `data/daily_events.json` 存在但当前主逻辑未使用，数据通道需要统一。

### AntiFraudAgent

文件：`src/agents/antifraud_agent.py`

当前能力：

- 用 LangGraph 做诈骗风险分析。
- 输出风险等级、诈骗类型、关键词、置信度和干预文本。

当前风险点：

- `AntiFraudAgent.arun` 重复定义了一次，虽不一定影响运行，但需要清理。
- `SystemOrchestrator._run_specific_agent` 当前对 `antifraud_agent` 分支缺少 return，命中反诈路由时会导致上层 `result.get(...)` 报错。

### ProactiveAgent

文件：`src/agents/proactive_agent.py`

当前能力：

- 基于空闲时间触发主动问候。
- 根据最近对话、画像、情感趋势选择焦虑、健康、家庭、兴趣、生活或一般陪伴策略。

局限：

- 当前是“空闲触发模板”，不是“每轮对话后后台规划下一步行为目标”。
- 社区活动只是硬编码在部分模板或 context 中，没有正式消费队列。

## 5. 工具层

文件：`src/tools/professional_skills.py`

已实现工具：

- `search_family_photos`：调用文件服务搜索相册/视频。
- `emergency_contact`：返回 SOS 触发结果和模拟通知动作。
- `play_music`：返回音乐播放触发 payload。
- `record_health_complaint`：写入健康主诉。

与目标背景的差距：

- 缺少讲故事工具。
- 缺少正式用药提醒事件工具。
- 缺少子女消息联动工具的消息生成/排队/状态确认。
- 缺少社区管理员 SOS 消息和社区活动消费队列。
- 缺少工具执行后的回调闭环，例如音乐结束、故事结束、消息是否送达。

## 6. 记忆与数据层

文件：`src/utils/rag_helper.py`

当前数据文件：

- `data/chat_history.json`：短期聊天历史，目前有 66 条。
- `data/user_profile.json`：画像，字段包含 `name`、`health_condition`、`family_members`、`preferences`、`dialect`、`mental_health_tendency`。
- `data/emotion_log.json`：情绪日志，目前有 13 条。
- `data/agent_status.json`：主动关怀和各 agent 更新时间。
- `data/vector_db`：Chroma 向量库。

当前向量集合：

- `knowledge_base`：知识库。
- `chat_memory`：中期对话摘要。
- `daily_events`：生活事件。

局限：

- 用户画像字段不足以承载风险历史、干预计划、联系人授权、用药计划、社区活动偏好等。
- 情绪日志只有 `emotion` 和 `risk_level`，缺少证据、置信度、症状维度、干预步骤和结果。
- 没有独立的计划状态存储，例如 `care_plan.json` 或数据库表。

## 7. 测试与验证

已有测试/检查：

- 音乐路由与 payload。
- 照片关键词归一化。
- 画像和记忆重置。
- 手工系统检查脚本。
- SOS 和主动关怀检查脚本。

本次本地验证结果：

- `python -m py_compile ...` 语法编译通过。
- `python -m pytest tests\test_agent_resilience_unittest.py tests\test_music_intent.py tests\test_photo_keyword_normalization.py -q` 未能执行，原因是当前 Python 环境缺少 `langchain_openai` 依赖。

验证缺口：

- 缺少心理风险识别案例集。
- 缺少“活着没意思/不想活了”等安全红线测试。
- 缺少 CBT 分级干预流程测试。
- 缺少前端事件契约测试。
- 缺少后台计划/社区活动队列测试。

## 8. 补充说明索引

本文件只描述当前实现。针对用户澄清后的机制设计，参见：

- `docs/mental_health_cbt_closure_design.md`：心理健康精准识别、分级 CBT 干预、后台 Planner 和风险分级。
- `docs/event_contract_and_routing_notes.md`：事件字段生成位置、接口使用、共享上下文、上下文脏数据和路由规则例子。
- `docs/frontend_backend_interface_proposal.md`：子女/老人/社区/危机/证据/音乐库/Planner 状态接口草案。
