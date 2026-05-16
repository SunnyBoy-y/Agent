# 文档、接口、落盘与代码修改计划复核

更新时间：2026-05-15

## 1. 复核结论

当前文档的业务方向基本一致：先构建心理风险评估、CarePlan、消息队列、社区联动、音乐回调和子女端 Agent。与代码仓库对比后，需要明确一点：

当前代码仍是“单老人全局文件 + 一个 SSE 聊天接口 + 若干基础状态接口”的架构；文档里的 family/community/music/action_complete/mental_assessments/planner/family_chat 都是拟新增接口。下一步不能直接把所有接口堆进 `server.py` 和 `RAGHelper`，应先补 schema 和轻量数据服务，再逐步接 Orchestrator。

2026-05-16 更新确认：

- 已接受 `actor_role` + `direction`。
- 子女端 Agent 第一版使用 SSE。
- 子女行为规范不支持 `avoid_topics`，改为 `suggested_topics` + 消费次数/频率/状态 + 长期目标。
- 社区活动需要 `valid_until`，不需要 `suitable_states`。
- 社区公告和社区活动前端 UI 分成两个入口。
- 音乐库需要歌曲摘要和标签。
- 音乐被打断也通过 `POST /api/action_complete` 回调，使用 `status=interrupted`。

## 2. 当前真实接口与文档接口匹配度

### 2.1 已实现接口

代码位置：`src/server.py`

| 接口 | 当前状态 | 说明 |
| --- | --- | --- |
| `POST /api/chat` | 已实现 | SSE 流式对话，调用 `SystemOrchestrator.process_input_stream` |
| `GET /api/profile` | 已实现 | 读取全局 `data/user_profile.json` |
| `POST /api/profile` | 已实现 | 更新全局画像 |
| `GET /health` | 已实现 | 服务健康检查 |
| `GET /api/system_status` | 已实现 | 最近路由、工具调用、画像、最近对话 |
| `GET /api/proactive_check` | 已实现 | 主动关怀轮询 |
| `POST /api/reset_profile` | 已实现 | 重置全局画像 |
| `POST /api/reset_memory` | 已实现 | 重置全局记忆、画像、情绪日志和向量库 |

### 2.2 文档中拟新增但尚未实现的接口

这些接口目前只在 `frontend_backend_interface_proposal.md` 中有设计，代码未实现：

| 接口 | 建议状态 | 优先级 |
| --- | --- | --- |
| `POST/GET /api/family/agent_policy` | 拟新增 | P2 |
| `POST /api/family/messages` | 拟新增 | P1 |
| `GET /api/elder/pending_messages` | 拟新增 | P1 |
| `POST /api/elder/messages/{message_id}/consent` | 拟新增 | P1 |
| `GET /api/family/alerts` | 拟新增 | P1 |
| `POST/GET /api/community/announcements` | 拟新增 | P2 |
| `POST/GET /api/community/activities` | 拟新增 | P2 |
| `GET /api/community/crisis_alerts` | 拟新增 | P1 |
| `POST /api/crisis/events` | 拟新增 | P1 |
| `GET /api/mental_assessments` | 拟新增 | P0 |
| `POST/GET/PATCH /api/medication/plans` | 拟新增 | P1 |
| `GET /api/timed_events/due` | 拟新增 | P1 |
| `POST /api/timed_events/{event_id}/ack` | 拟新增 | P1 |
| `POST/GET /api/music/library` | 拟新增 | P2 |
| `POST /api/action_complete` | 拟新增 | P2 |
| `GET /api/planner/status` | 拟新增 | P1 |
| `POST /api/family/chat` | 拟新增 | P2 |
| `GET /api/family/elder_summary` | 拟新增 | P2 |

### 2.3 接口契约需要修正的点

1. `GET` 接口不要设计 JSON body。

已将接口草案中的 `GET` 示例改为 query 参数。后续实现时保持这种形式：

```text
GET /api/mental_assessments?elder_user_id=elder_001&limit=20
```

同理：

```text
GET /api/family/agent_policy?elder_user_id=elder_001&child_user_id=child_001
GET /api/family/alerts?elder_user_id=elder_001&child_user_id=child_001&limit=20
GET /api/community/activities?community_id=community_001
GET /api/medication/plans?elder_user_id=elder_001
GET /api/timed_events/due?elder_user_id=elder_001
GET /api/planner/status?elder_user_id=elder_001
GET /api/family/elder_summary?elder_user_id=elder_001&child_user_id=child_001
```

2. 当前 `user_id` 没有贯穿数据层。

`POST /api/chat` 有 `user_id`，但 `RAGHelper` 始终读写全局文件：

- `data/chat_history.json`
- `data/user_profile.json`
- `data/emotion_log.json`
- `data/agent_status.json`

所以多用户、子女端、社区端设计落地前，必须先解决数据命名空间。

3. `action` 事件语义混用。

当前 `action` 同时表示数字人动作和业务动作。文档建议拆成：

- `avatar_action`
- `business_action`

第一版可以兼容保留旧 `action`，新增细分事件。

4. `risk` 事件过于简化。

当前只是字符串。建议保留 `risk` 字符串兼容前端，同时新增：

```json
{
  "type": "risk_detail",
  "data": {
    "tier": "crisis",
    "primary_state": "suicidal_ideation",
    "confidence": 0.95,
    "assessment_id": "assess_001",
    "next_goal": "safety_grounding"
  }
}
```

## 3. 数据落盘复核

### 3.1 当前已有落盘

代码位置：`src/utils/rag_helper.py`

| 文件/存储 | 当前用途 | 问题 |
| --- | --- | --- |
| `data/chat_history.json` | 全量短期对话 | 全局单用户，无角色隔离 |
| `data/user_profile.json` | 老人画像 | 全局单用户，字段不足 |
| `data/emotion_log.json` | 表情和风险日志 | 证据、分数、置信度缺失 |
| `data/agent_status.json` | 主动关怀状态 | 全局单用户 |
| `data/vector_db` | Chroma 知识、记忆、生活事件 | 当前集合无 user_id 隔离 |
| `data/daily_events.json` | 存在但主流程未使用 | 与 Chroma daily_events 通道不一致 |

### 3.2 新功能需要新增的落盘

建议不要全部放进 `RAGHelper`。`RAGHelper` 初始化会加载 embeddings 和 Chroma，作为轻量接口存储不合适。建议新增 `src/services/data_store.py`，只处理 JSON/JSONL + FileLock。

建议结构：

```text
data/
  users/
    elder_001/
      profile.json
      chat_history.json
      emotion_log.json
      agent_status.json
      care_plan.json
      mental_assessments.jsonl
      intervention_log.jsonl
      action_sessions.jsonl
      family/
        child_001/
          family_chat_history.json
          family_chat_memory.jsonl
  relay_messages.jsonl
  family_alerts.jsonl
  community_alerts.jsonl
  community_announcements.jsonl
  community_activities.jsonl
  family_agent_policy.json
  music_library.json
```

第一版也可以不马上迁移旧文件，而是：

- 旧全局文件继续作为 `elder_001` 的兼容数据。
- 新功能先写新文件。
- 后续再做迁移脚本。

### 3.3 风险证据可见性

已确认规则：

- 风险证据允许保存老人原话。
- 子女端可见老人原话、摘要和建议。
- 社区端默认只看原因摘要和解决建议。
- 老人端不展示风险标签和内部证据。

建议在 `mental_assessments.jsonl` 中加可见性字段：

```json
{
  "id": "assess_001",
  "elder_user_id": "elder_001",
  "risk_tier": "crisis",
  "raw_quotes": ["活着没意思"],
  "summary": "老人表达强烈无意义感",
  "family_suggestion": "建议先表达陪伴，不追问刺激性细节。",
  "community_reason_summary": "老人出现危机表达，需要关注当前状态。",
  "visibility": {
    "elder": "none",
    "family": "raw_and_summary",
    "community": "summary_only"
  }
}
```

## 4. 代码修改计划合理性复核

### 4.1 不建议的做法

- 不建议直接在 `server.py` 一次性新增所有接口，文件会失控。
- 不建议继续让 `RAGHelper` 承担 family/community/music/planner 的所有落盘。
- 不建议让 LLM 决定 `crisis` 是否降级。
- 不建议先做子女端 Agent，再做风险评估和权限可见性。
- 不建议现在重写全部 Agent 真流式；先保证事件契约和安全策略。

### 4.2 推荐模块边界

建议新增：

```text
src/schemas/
  mental_health.py
  relay.py
  family.py
  community.py
  actions.py

src/policies/
  safety_policy.py

src/services/
  data_store.py
  assessment_service.py
  care_plan_service.py
  relay_message_service.py
  community_service.py
  action_session_service.py
  family_context_service.py
  context_guard.py

src/agents/
  planning_agent.py
  family_agent.py
```

如果暂时不拆 FastAPI router，可以先把 endpoints 加在 `server.py`，但服务逻辑必须放进 `services/`，避免 `server.py` 变成业务层。

## 5. 最佳实施步骤

### Step 1：补纯 Python schema 和 DataStore

目标：

- 不接 LLM。
- 不改主对话行为。
- 建立文件落盘能力和单元测试。

新增：

- `src/services/data_store.py`
- `src/schemas/mental_health.py`
- `src/schemas/relay.py`
- `src/schemas/actions.py`

测试：

- JSON/JSONL append/read。
- FileLock 写入。
- 可见性字段。

### Step 2：实现 SafetyPolicy 和 RiskAssessment 规则层

目标：

- Python 硬规则 + 加权评分。
- “活着没意思”直接 `crisis`。
- `crisis` 默认生成 family alert + community alert 草稿。

新增：

- `src/policies/safety_policy.py`
- `src/services/assessment_service.py`

测试：

- 危机短语不能降级。
- 焦虑导致头疼不路由到用药建议。
- 医疗红线词过滤。

### Step 3：接入 Orchestrator，但先不启用 LLM 复核阻塞

目标：

- `/api/chat` 开头同步运行 Python 评估。
- 输出 `risk` 和 `risk_detail`。
- 写 `mental_assessments.jsonl`。
- 路由用 `assessment + care_plan + router`。

注意：

- LLM 复核并发启动，超时不阻塞回复。
- `crisis` 立即走安全稳定流程。
- 低风险时才允许嵌入悄悄话提醒。

### Step 4：实现定时事件与用药提醒

目标：

- 将 `MedicalAgent.check_medication_reminder()` 替换为独立 `TimedEventService` 和 `MedicationReminderService`。
- 支持到点窗口、过时提醒、确认、稍后提醒和过期停止。
- 用药提醒只读取已记录医嘱/照护者录入剂量，不生成新剂量。

新增接口：

- `POST/GET/PATCH /api/medication/plans`
- `GET /api/timed_events/due`
- `POST /api/timed_events/{event_id}/ack`

测试：

- 到点、过时、过期、确认后不重复提醒。
- 缺剂量时不编剂量。
- 文案不出现补服、加量、停药、换药、去医院等禁用表达。

### Step 5：实现消息与联动接口

目标：

- 悄悄话按钮/语义同意。
- 子女风险提醒。
- 社区 crisis 默认通知。

新增接口：

- `POST /api/family/messages`
- `GET /api/elder/pending_messages`
- `POST /api/elder/messages/{message_id}/consent`
- `GET /api/family/alerts`
- `GET /api/community/crisis_alerts`

### Step 6：实现 CarePlan 和 Planner 状态

目标：

- 保存 `data/users/{elder}/care_plan.json`。
- 提供 `GET /api/planner/status`。
- Planner 先用规则实现，再接 ReAct LLM。
- 当前源码尚无独立后台 ReAct Planner；实现前必须补 per-user 任务管理、stale 丢弃和 `CarePlan.version` compare-and-swap，详见 `background_planner_concurrency_design.md`。

### Step 7：实现音乐完成回调和干预日志

目标：

- `music_payload` 增加 `action_id`、`music_name`、`post_reply`。
- `POST /api/action_complete` 更新 `intervention_log` 和 `care_plan`。

### Step 8：实现子女端 Agent

目标：

- `POST /api/family/chat`
- 子女记忆与老人记忆隔离。
- 子女 Agent 可读老人评估摘要、CarePlan、子女可见证据，但不能污染老人端对话记忆。

### Step 9：再考虑真流式改造和多用户迁移

目标：

- 各 Agent 支持 `astream_run`。
- 全局数据迁移到 `data/users/{elder_user_id}`。
- Chroma metadata 增加 `elder_user_id` 过滤。

## 6. 当前文档需要同步修订的点

已在 `frontend_backend_interface_proposal.md` 中同步：

- 悄悄话同时支持按钮和语义确认。
- 低风险时可在对话末尾自然嵌入悄悄话提醒。
- `crisis` 默认通知社区管理员。
- 风险证据老人原话仅子女端可见。
- 音乐完成回调接口。
- 子女端 Agent 聊天框和记忆隔离。

仍建议后续补充：

- 将所有 GET 接口示例改成 query 参数。
- 明确每个拟新增接口对应的数据文件。
- 给每个接口标注“已实现/拟新增”。

## 7. 当前代码已知风险

- `RAGHelper` 多处实例化，新增轻量接口时不应复用它做简单文件读写。
- `POST /api/profile` 没有 user_id，未来多用户会冲突。
- `/api/proactive_check` 没有 user_id，也会多用户冲突。
- `AntiFraudAgent.arun` 在文件中重复定义，需要清理。
- `MedicalAgent` 仍有“家庭医生助手/健康建议”提示词，需被 `SafetyPolicy` 约束。
- 反诈、心理、医疗消息联动目前还没有真正落盘队列。

详细修复优先级与方案见：

- `docs/risk_fix_priority_plan.md`

增量目标点、检查命令和预期结果见：

- `docs/incremental_update_plan.md`
