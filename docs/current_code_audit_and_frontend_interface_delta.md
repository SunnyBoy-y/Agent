# 当前代码审计与前端接口变化对照

更新日期：2026-05-16  
审计基准：当前工作区相对 Git HEAD 的全部已修改/新增代码与文档  
验证环境：`conda activate agent`  
完整回归命令：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

最新结果：

```text
120 passed in 11.44s
```

---

## 1. 总体结论

当前代码主体方向与既定文档主线基本一致：系统已经从单轮聊天机器人，演进为“实时陪伴链路 + 后台评估规划链路 + 定时事件链路 + 家庭/社区/前端动作联动链路”的多端系统。

已经落地的关键架构包括：

- `DataStore` + per-user 数据目录；
- `UserContextService` / `ProfileService`；
- `SafetyPolicy`；
- `AssessmentService`；
- `TimedEventService` / `MedicationReminderService`；
- `RelayMessageService`；
- `CarePlanService`；
- `PlanningAgent` / `BackgroundPlannerService`；
- `ActionSessionService`；
- Family / Community 服务和 API；
- Family-side SSE Agent；
- 照片元数据检索契约；
- 情感 Agent 句级安全流式输出；
- 部分 RAGHelper 职责迁移。

代码不是“最小闭环凑测试”的状态。测试覆盖已经横跨服务层、API 层、编排层、后台 planner 并发、action 回调、家庭/社区、照片检索、安全策略等关键路径。

但当前还不是生产接口冻结状态。最需要立刻注意的是：**前端对接文档 `docs/frontend_backend_interface_proposal.md` 仍是“方案草案”，不是完全等价于当前 FastAPI 实现的精确契约**。多个接口的响应包裹结构、字段名和已实现范围与该文档存在偏差。

---

## 2. Git 变更范围

### 2.1 已修改的跟踪文件

主要业务文件：

- `src/server.py`
- `src/orchestrator.py`
- `src/tools/professional_skills.py`
- `src/agents/antifraud_agent.py`
- `src/agents/medical_agent.py`
- `src/agents/mental_health_agent.py`
- `src/agents/proactive_agent.py`
- `src/agents/router_agent.py`

主要文档：

- `docs/frontend_backend_interface_proposal.md`
- `docs/incremental_update_plan.md`
- `docs/index.md`

测试：

- `tests/test_music_intent.py`
- `tests/test_photo_keyword_normalization.py`
- `tests/test_prompt.py`

### 2.2 新增但未跟踪的重要文件

新增业务代码：

- `src/agents/family_agent.py`
- `src/agents/planning_agent.py`
- `src/policies/safety_policy.py`
- `src/schemas/actions.py`
- `src/schemas/community.py`
- `src/schemas/family.py`
- `src/schemas/mental_health.py`
- `src/schemas/planner.py`
- `src/schemas/relay.py`
- `src/schemas/timed_events.py`
- `src/services/action_session_service.py`
- `src/services/assessment_service.py`
- `src/services/background_planner_service.py`
- `src/services/care_plan_service.py`
- `src/services/community_service.py`
- `src/services/context_guard.py`
- `src/services/data_store.py`
- `src/services/family_context_service.py`
- `src/services/family_policy_service.py`
- `src/services/medication_reminder_service.py`
- `src/services/profile_service.py`
- `src/services/relay_message_service.py`
- `src/services/timed_event_service.py`
- `src/services/user_context_service.py`

新增测试：

- `tests/test_action_complete.py`
- `tests/test_agent_safety_convergence.py`
- `tests/test_assessment_service.py`
- `tests/test_background_planner_concurrency.py`
- `tests/test_background_planner_llm_review.py`
- `tests/test_care_plan_service.py`
- `tests/test_community_api.py`
- `tests/test_community_service.py`
- `tests/test_data_store.py`
- `tests/test_family_chat_api.py`
- `tests/test_family_context_service.py`
- `tests/test_family_policy_api.py`
- `tests/test_family_policy_service.py`
- `tests/test_medication_reminder_service.py`
- `tests/test_orchestrator_fast_path.py`
- `tests/test_planner_status_api.py`
- `tests/test_planning_agent_contract.py`
- `tests/test_relay_message_service.py`
- `tests/test_router_context_guard.py`
- `tests/test_safety_policy.py`
- `tests/test_timed_event_api.py`
- `tests/test_user_context_service.py`

新增文档：

- `docs/code_change_summary_and_next_steps.md`
- `docs/gpt55_handoff.md`
- `docs/implementation_progress_status.md`
- `docs/post_target16_next_stage_plan.md`

### 2.3 Git 卫生问题

当前工作区存在大量已跟踪或未跟踪的 `__pycache__/*.pyc` 文件。  
这不是业务功能 bug，但属于提交卫生问题。后续准备提交前应单独处理：

- 不要把 `.pyc` 当作业务改动提交；
- 如果要清理，先确认哪些 `.pyc` 是历史已跟踪文件，避免误删用户其他改动；
- 建议后续补 `.gitignore` 或单独做一次“生成物清理”提交。

---

## 3. 与文档主体思想的契合度

### 3.1 契合的部分

当前实现符合以下主体思想：

1. **安全规则后端统一判断**  
   `AssessmentService` 先于路由运行，`crisis` 直接输出 `risk_detail`、legacy `risk` 和 `sos`。  
   `SafetyPolicy` 对非 emotional agent 输出、mental/medical/antifraud 输出、planner 文本和 emotional 流式输出做后处理。

2. **实时链路不等待后台 planner**  
   `/api/chat` 中的 fast path 先评估、路由、SSE 返回；后台 relay/planner 通过 fire-and-forget 调度。

3. **旧后台任务不能污染新任务**  
   `BackgroundPlannerService` 通过 per-user task、`latest_turn_id`、`base_care_plan_version`、CAS 和 stale discard 控制旧任务提交。

4. **家庭/社区/老人可见性分层**  
   family alert 保留 family-visible 证据；community alert 强制脱敏；quiet message 正文只在老人同意后返回。

5. **定时用药不由 LLM 临场编造**  
   用药计划和剂量事件由 `MedicationReminderService` / `TimedEventService` 管理；`MedicalAgent.check_medication_reminder()` 已变为兼容 no-op。

6. **动作生命周期可追踪**  
   `music_payload` 和后台 planner 的 schedule_music/schedule_story 会创建 `ActionSession`；前端通过 `/api/action_complete` 回调完成状态闭环。

7. **相册不实时做视觉理解**  
   `search_family_photos` 只基于已有元数据检索，不在请求时调用视觉 captioning 或 embedding 生成。

### 3.2 不完全契合或仍需收口的部分

1. **RAGHelper 职责迁移还没有完全完成**  
   `MedicalAgent` 症状写入已优先走 `UserContextService`，`record_health_complaint` 也不再直接实例化 `RAGHelper`。  
   但 `SystemOrchestrator._build_shared_context()` 仍调用 `emotional_agent.rag_helper.search_comprehensive_memory()`，该历史向量/记忆链路仍可能是全局的，不是 per-user 完全隔离。

2. **`/api/reset_memory` 已收口为 per-user DataStore reset**  
   2026-05-17 已修：`POST /api/reset_memory?user_id=...` 会先取消该用户后台 planner 任务，再删除 `DataStore` 下 `users/{user_id}` 目录。默认不触碰 legacy RAG；只有 `include_legacy_rag=true` 时才调用 `RAGHelper.reset_all_memory()`，且该操作明确标注为全局作用域。  
   仍需注意：历史 RAG 读取链路尚未完全迁移，因此 legacy RAG reset 不是 per-user 精细 reset。

3. **前端接口文档已完成第一轮 contract 收口**  
   `docs/frontend_backend_interface_proposal.md` 已从方案草案改成当前真实 contract，并补充 user_id 边界、相册、用药、action_complete、family/community、reset 等接口细节。后续新增接口仍需同步更新。

4. **聊天中的用药查询已对接 MedicationPlan**  
   2026-05-17 已修：`MedicalAgent` 的 `medication_query` 优先读取 `MedicationReminderService.list_plans(user_id, include_inactive=False)`，只在无计划服务或无 active plan 时回退旧 profile `medications`。

---

## 4. 是否存在“懒加载跳过 test / 奇技淫巧”

结论：没有发现明显为了通过测试而绕过导入、跳过测试、隐藏失败的懒加载手法。

具体判断：

- `src/server.py` 顶层仍直接导入 `SystemOrchestrator`，不是为了避免启动失败而延迟导入。
- `SystemOrchestrator.__init__()` 直接构造主要服务和 Agent，没有用空壳对象绕过依赖。
- `PlanningAgent` 中的 LLM chain 延迟构造属于正常资源初始化；当没有 live LLM 环境时走 deterministic fallback，是产品层安全降级，不是测试逃避。
- 测试使用 mock/stub 来隔离外部 LLM、文件服务、网络视觉服务，是合理的单元/集成测试边界。
- 本轮完整回归已在 `conda activate agent` 环境中执行，结果为 `120 passed`。

需要注意的不是“懒加载”，而是下面这些实际工程风险。

---

## 5. 发现的问题与风险清单

### P0/P1：应优先处理

#### 5.1 EmotionalAgent 的 `record_health_complaint` 工具存在多用户写错风险

位置：

- `src/tools/professional_skills.py::record_health_complaint`
- `src/tools/professional_skills.py::record_health_complaint_to_service`
- `src/agents/emotional_agent.py`

当前行为：

- `record_health_complaint(symptom, elder_user_id="user_001")` 默认写入 `user_001`；
- EmotionalAgent 绑定该 tool 时，没有把当前 `session_context["user_id"]` 自动注入 tool；
- LLM 如果只传 `symptom`，健康主诉会写到默认用户 `user_001`。

影响：

- 多用户场景下可能出现健康画像污染；
- Target20 的“健康主诉写入 UserContextService”方向是对的，但 tool 层还没完成上下文绑定。

建议：

- 不要依赖 LLM 主动填写 `elder_user_id`；
- 在 EmotionalAgent tool 执行层拦截 `record_health_complaint`，把 `session_context["user_id"]` 注入；
- 或把 `record_health_complaint` 改为实例化/上下文绑定工具，而不是静态全局 tool。

#### 5.2 `/api/reset_memory` 没有清理新 DataStore 数据

位置：

- `src/server.py`, `/api/reset_memory`

当前行为：

- 只调用 legacy `RAGHelper.reset_all_memory()`；
- 不清理：
  - `data/users/{user_id}/profile.json`
  - `chat_history.json`
  - `mental_assessments.jsonl`
  - `care_plan.json`
  - `planner_jobs.jsonl`
  - `planner_actions.jsonl`
  - `relay_messages.json`
  - `action_sessions.json`
  - family/community 相关数据。

影响：

- 前端或测试人员点击“清空记忆”后，系统新链路状态仍然存在；
- 会造成“看起来 reset 成功，但 planner/family/action 仍然延续旧状态”的问题。

建议：

- 明确拆分接口：
  - `/api/reset_profile?user_id=...`
  - `/api/reset_user_state?user_id=...`
  - `/api/reset_legacy_rag`
- 或让 `/api/reset_memory` 明确只处理 legacy，并在响应中说明不处理 DataStore。

#### 5.3 `/api/chat` 的 `user_id` 合并优先级可能被 context 覆盖

位置：

- `src/server.py`, `chat_endpoint`

当前代码语义：

```python
context = dict(payload.context or {})
if payload.user_id:
    context.setdefault("user_id", payload.user_id)
```

如果请求体同时传：

```json
{
  "user_id": "elder_A",
  "context": {"user_id": "elder_B"}
}
```

最终使用的是 `elder_B`，因为 `setdefault` 不覆盖已有值。

影响：

- 多用户隔离边界不够硬；
- 前端一旦 context 残留旧 user_id，可能写错用户。

建议：

- top-level `payload.user_id` 应强制覆盖 `context.user_id`；
- 或检测冲突并返回 400；
- 前端文档也应规定只传一种来源。

#### 5.4 聊天用药查询没有读取新 MedicationPlan

位置：

- `src/agents/medical_agent.py`
- `src/services/medication_reminder_service.py`

当前行为：

- 用药计划 API 已经写入 `MedicationPlan`；
- 但 `MedicalAgent` 的 `medication_query` 仍读取 `profile["medications"]`。

影响：

- 前端通过 `/api/medication/plans` 创建药物计划后，老人聊天问“现在该吃什么药”，MedicalAgent 可能仍回答“还没记录用药”。

建议：

- `MedicalAgent` 不应直接读旧 profile medications；
- 编排层应把当前 due/plan 摘要注入 shared_context；
- 或让 MedicalAgent 通过服务查询 `MedicationReminderService.list_plans()` / `get_due_timed_events()`。

#### 5.5 相册泛化关键词判断过宽，可能导致精确查询退化成“列出全部”

位置：

- `src/tools/professional_skills.py::search_family_photos`

当前逻辑：

- 只要原始输入包含 `照片`、`看看`、`相册` 等 list-all hint，就把 `search_param` 设为 `""`。

问题例子：

- “看看孙女照片”
- “找一下公园的照片”
- “打开相册里全家福”

这些输入包含泛化词，但实际也包含关键实体。当前实现可能直接请求全部文件，而不是按“孙女/公园/全家福”排序筛选。

影响：

- 前端收到照片列表可能不相关；
- 用户会感觉“相册会打开，但不懂我找什么”。

建议：

- 只有在归一化后没有实体词时才 list-all；
- 对“看看/照片/相册”等词先剔除，再保留剩余关键词；
- 增加测试：`看看孙女照片` 应命中 `孙女` 元数据，而不是全部返回。

### P2：中期应处理

#### 5.6 `/api/elder/pending_messages` 信任前端传入 `risk_tier`

位置：

- `src/server.py`
- `src/services/family_policy_service.py::pending_quiet_message_prompts`

当前行为：

- 接口参数 `risk_tier` 默认 `safe`；
- 后端根据这个参数决定是否暴露 quiet-message metadata。

影响：

- 如果前端没有传真实风险，或恶意传 `safe`，高风险场景也可能看到 quiet-message prompt；
- 当前只暴露 metadata，不暴露正文，风险有限，但与“后端统一判断风险”的原则不完全一致。

建议：

- standalone API 应默认读取最新 `CarePlan` 或最近 `MentalRiskAssessment`；
- `risk_tier` 可作为调试参数，但生产不应由前端决定。

#### 5.7 `POST /api/family/messages` 只支持 quiet_message，文档中的 elder_note 未实现

位置：

- `src/schemas/family.py::FamilyMessageCreateRequest`
- `src/services/family_policy_service.py::create_quiet_message`
- `docs/frontend_backend_interface_proposal.md`

当前 schema：

```python
message_type: Literal["quiet_message"] = "quiet_message"
```

文档草案中还写了老人对孩子留言 `elder_note` 的设计，但当前 API 会拒绝。

影响：

- 前端如果按文档实现 elder_to_child 留言，会收到 422 或业务错误；
- 需要明确“已实现 quiet_message，elder_note 仍未实现”。

#### 5.8 `GET /api/community/crisis_alerts` 没有 community_id 隔离

位置：

- `src/server.py`
- `src/services/community_service.py::list_crisis_alerts`

当前接口按 `elder_user_id` 查询 community alert，不带 `community_id`。

影响：

- 如果未来一个系统服务多个社区，需要额外 elder-to-community 绑定，否则社区端隔离不完整；
- 当前 demo/单社区阶段可接受，但文档要写清。

#### 5.9 Planner 的 family/community action 内容没有直接进入 relay

位置：

- `src/services/background_planner_service.py::_persist_actions`

当前行为：

- `family_message` / `community_alert` action 会写入 `planner_actions.jsonl`；
- 但实际 relay 创建调用的是 `relay_message_service.create_from_assessment(assessment)`；
- LLM/planner action 的 `content`、`reason_summary` 不直接作为 relay 正文。

影响：

- Planner action contract 被审计保存了，但前端/family/community 看到的是 assessment 派生消息；
- 这可能是安全设计，但要在文档中明确，否则前端会以为 planner action 内容会被消费。

建议：

- 如果保持 assessment-derived relay，应将 planner action 标记为 audit-only / recommendation；
- 如果希望 planner action 生效，应为每种 action 定义审批、安全过滤和落地路径。

#### 5.10 PlanningAgent 对 LLM action 可选契约字段过于严格

位置：

- `src/agents/planning_agent.py::_sanitize_actions`

当前行为：

- 先用 `PlannerQueuedAction(**dict)` 解析；
- 如果 LLM 返回非法 `target_channel` / `visibility_scope`，整个 action 会被丢弃，而不是清洗后使用默认值。

影响：

- live LLM 稍微输出偏差就可能丢动作；
- fallback deterministic 测试不容易覆盖这个问题。

建议：

- 对 LLM 输出先做宽松 dict 清洗；
- 对非法 contract 字段置空，再走 `_finalize_action_contract()`。

#### 5.11 Direct music action session 没有 idempotency key

位置：

- `src/orchestrator.py::_normalize_music_payload`

当前行为：

- background planner schedule_music 会使用 idempotency key；
- 但 EmotionalAgent / direct agent 触发的 `music_payload` 创建 session 时没有 idempotency key。

影响：

- 如果同一轮事件重放或前端重连导致重复触发，可能创建多个 action session；
- 当前影响主要是审计重复，不一定影响播放。

建议：

- direct music session 的 idempotency key 可基于 `elder_user_id + turn_id + music_name + source` 生成。

#### 5.12 EmotionalAgent 句级流式无法撤回已发安全句

位置：

- `src/orchestrator.py::_run_emotional_agent`

当前行为：

- 非 crisis 时，完成句会先经 `SafetyPolicy` 后发给前端；
- final content 到达后，如果 `safe_final_content.startswith(emitted_text)`，只补 suffix；
- 如果 final 与已发内容不一致，不会撤回已发句子。

影响：

- 安全性上每个句子已过 `SafetyPolicy`，风险可控；
- 但语义一致性不保证，极端情况下前端看到的流式文本与 final 不完全一致。

建议：

- 当前可接受；
- 若要求强一致，需要设计 revision/correction event，或只做更粗粒度缓冲。

#### 5.13 `_build_context_snapshot()` 存在不可达代码

位置：

- `src/orchestrator.py::_build_context_snapshot`

当前函数在调用 `user_context_service.build_context_snapshot(...)` 后立即 return，后面的手写 snapshot 代码不可达。

影响：

- 不影响运行；
- 属于重构残留，应清理，避免误导后续维护者。

#### 5.14 Family alerts 查询没有按 child_user_id 过滤

位置：

- `src/server.py::get_family_alerts`
- `src/services/family_policy_service.py::list_family_alerts`

当前接口接收 `child_user_id`，但服务层按 elder 的 target=`family` 消息列表返回，不按 child 过滤。

影响：

- 对 system_to_family alert 可能没问题；
- 如果未来 family 端消息需要按子女区分，会暴露边界问题。

---

## 6. 当前前端相关对接文档

最接近前端对接的文档是：

1. `docs/frontend_backend_interface_proposal.md`
   - 主文档；
   - 覆盖 actor_role/direction、family policy、quiet message、community、music/action_complete、medication/timed event、planner status、family chat；
   - 但它是方案草案，不是当前实现的精确 OpenAPI 合同。

2. `docs/event_contract_and_routing_notes.md`
   - 更接近 SSE 事件语义；
   - 应与 `/api/chat`、`/api/family/chat` 的实际 event type 对齐。

3. `docs/timed_event_and_medication_reminder_design.md`
   - 用药/定时事件机制设计；
   - 比前端草案更适合理解 due/ack 状态机。

4. `docs/background_planner_concurrency_design.md`
   - 后台 planner 并发、防旧任务污染的正式设计。

5. `docs/gpt55_handoff.md`
   - 当前上下文恢复入口；
   - 记录了 Target17-20 完成状态和测试环境。

6. 本文档：
   - `docs/current_code_audit_and_frontend_interface_delta.md`
   - 用于把“真实代码接口”和“前端草案文档”之间的差异列清。

---

## 7. 当前真实 API 列表与前端影响

以下来自 `src/server.py` 当前真实路由。

### 7.1 Chat SSE

#### POST `/api/chat`

请求模型：

```json
{
  "message": "string",
  "user_id": "user_001",
  "context": {}
}
```

实际响应：

- `text/event-stream`
- 每条为：

```text
data: {"type":"...","data":...}
```

当前可能出现的事件：

- `log`
- `risk_detail`
- `risk`
- `sos`
- `step`
- `token`
- `action`
- `music_payload`
- `music`
- `photos`
- `photos_result`
- `expression`
- `error`
- `done`

注意：

- 错误事件在 `chat_endpoint` 的异常分支里是 `{"type":"error","content":...}`，字段不是 `data`；
- 前端解析时要兼容 `data` 和 `content`。

`risk_detail` 现在是核心事件，legacy `risk` 只在非 safe 时发。

`music_payload` 典型字段：

```json
{
  "status": "success",
  "intent": "play_music",
  "trigger_music": true,
  "query": "月亮代表我的心",
  "source": "agent",
  "music_name": "月亮代表我的心",
  "post_reply": "这首歌先到这里...",
  "action_id": "action_xxx",
  "action_type": "music"
}
```

`photos` item 当前新增字段：

```json
{
  "url": "...",
  "desc": "...",
  "type": "...",
  "tags": [],
  "description": "...",
  "people": [],
  "location": "...",
  "time_text": "...",
  "caption_source": "...",
  "original_file_name": "...",
  "metadata_available": true
}
```

前端必须注意：当前相册没有真正视觉理解，只展示已有元数据。

### 7.2 Profile / system status

#### POST `/api/profile?user_id=user_001`

请求体为任意 profile object；如果 body 内有 `user_id`，会优先使用 body 的 `user_id`。

实际响应：

```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "user_id": "user_001",
  "updated_keys": ["..."],
  "profile": {}
}
```

#### GET `/api/profile?user_id=user_001`

实际响应不是统一 `status/data` 包裹，而是直接返回 profile object。

前端注意：

- `POST /api/profile` 和 `GET /api/profile` 响应形状不一致。

#### GET `/api/system_status?user_id=user_001`

实际响应：

```json
{
  "status": "success",
  "data": {
    "routing_decision": {},
    "tool_calls_analysis": [],
    "user_profile": {},
    "recent_chat_history": []
  }
}
```

### 7.3 Planner / care plan

#### GET `/api/planner/status?elder_user_id=user_001`

实际响应：

```json
{
  "status": "success",
  "data": {
    "planner": {},
    "care_plan": {}
  }
}
```

与前端草案差异：

- 草案示例里是直接返回 `elder_user_id/care_plan/planner_status`；
- 当前实现包在 `data.planner` 和 `data.care_plan` 下。

### 7.4 Medication / timed events

#### GET `/api/medication/plans`

Query：

- `elder_user_id`
- `include_inactive=false`

实际响应：

```json
{
  "status": "success",
  "data": []
}
```

#### POST `/api/medication/plans`

请求体核心字段：

```json
{
  "elder_user_id": "user_001",
  "name": "药名",
  "dosage_text": "一次1片",
  "instruction_text": "早餐后",
  "schedule": [{"time": "08:00", "label": "早餐后"}],
  "status": "active"
}
```

如果不传 `medication_id`，后端生成。

实际响应：

```json
{
  "status": "success",
  "data": {}
}
```

#### PATCH `/api/medication/plans/{medication_id}?elder_user_id=user_001`

请求体为局部字段；后端 merge 旧 plan。

#### GET `/api/timed_events/due`

Query：

- `elder_user_id`
- `now` 可选 ISO datetime

实际响应：

```json
{
  "status": "success",
  "data": []
}
```

与前端草案差异：

- 草案写的是 `{"events":[...]}`；
- 真实实现是 `{"status":"success","data":[...]}`。

#### POST `/api/timed_events/{event_id}/ack`

请求体：

```json
{
  "elder_user_id": "user_001",
  "ack": "taken | snooze | skip | not_sure | missed",
  "snooze_minutes": 10,
  "text": "我吃过了"
}
```

实际响应：

```json
{
  "status": "success",
  "data": {}
}
```

### 7.5 Action completion

#### POST `/api/action_complete`

请求体：

```json
{
  "action_id": "action_xxx",
  "elder_user_id": "user_001",
  "action_type": "music",
  "status": "completed | interrupted | cancelled | failed",
  "music_name": "string",
  "played_seconds": 42,
  "total_seconds": 180,
  "interrupt_reason": "user_skip",
  "payload": {}
}
```

实际响应：

```json
{
  "status": "success",
  "data": {
    "session": {},
    "post_reply": "...",
    "next_turn_goal": "...",
    "care_plan_patch": {},
    "completed_intervention": true,
    "idempotent_replay": false
  }
}
```

与前端草案差异：

- 草案把 `post_reply` 放在顶层；
- 真实实现放在 `data.post_reply`。

### 7.6 Family policy / quiet message

#### GET `/api/family/agent_policy`

Query：

- `elder_user_id`
- `child_user_id`

响应：

```json
{"status":"success","data":{}}
```

#### POST `/api/family/agent_policy`

请求体：

```json
{
  "elder_user_id": "user_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "policy": {}
}
```

#### GET `/api/family/topics/available`

Query：

- `elder_user_id`
- `child_user_id`
- `now` 可选

#### POST `/api/family/topics/{topic_id}/consume`

Query：

- `elder_user_id`
- `child_user_id`
- `now` 可选

#### POST `/api/family/messages`

当前只支持 quiet message。

请求体：

```json
{
  "elder_user_id": "user_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "direction": "child_to_elder",
  "message_type": "quiet_message",
  "content": "正文",
  "title": "",
  "priority": "low | normal | high",
  "payload": {}
}
```

实际响应：

```json
{
  "status": "success",
  "data": {
    "...": "RelayMessage fields, but content removed"
  }
}
```

安全点：

- 创建后响应会 `pop("content")`，不会把正文直接给老人端。

未实现：

- 文档里的 `elder_note` 尚未实现。

#### GET `/api/family/alerts`

Query：

- `elder_user_id`
- `child_user_id`
- `limit=20`

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "...",
    "child_user_id": "...",
    "alerts": []
  }
}
```

注意：

- 当前 `child_user_id` 主要用于响应回显，不参与过滤。

#### GET `/api/elder/pending_messages`

Query：

- `elder_user_id`
- `risk_tier=safe`

响应：

```json
{
  "status": "success",
  "data": {
    "messages": []
  }
}
```

注意：

- 该接口只返回 metadata prompt，不返回 quiet-message 正文；
- 但 `risk_tier` 当前由前端传入，后续应改为后端推导。

#### POST `/api/elder/messages/{message_id}/consent`

请求体：

```json
{
  "elder_user_id": "user_001",
  "consent": "accepted | rejected",
  "source": "button | semantic | system",
  "raw_text": "可以，读吧"
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "id": "...",
    "status": "accepted | rejected",
    "content": "仅 accepted 时有正文",
    "message": {},
    "idempotent_replay": false
  }
}
```

### 7.7 Family-side Agent

#### POST `/api/family/chat`

请求体：

```json
{
  "elder_user_id": "user_001",
  "child_user_id": "child_001",
  "message": "我妈最近状态怎么样？",
  "context": {}
}
```

响应：

- `text/event-stream`

事件：

- `token`
- `family_context`
- `done`
- `error`

#### GET `/api/family/elder_summary`

Query：

- `elder_user_id`
- `child_user_id`

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "...",
    "summary": {},
    "visible_evidence": [],
    "family_alerts": [],
    "recent_interventions": [],
    "policy": {}
  }
}
```

### 7.8 Community

#### POST `/api/community/announcements`

请求体：

```json
{
  "community_id": "community_001",
  "actor_role": "community_admin",
  "title": "标题",
  "content": "正文",
  "tags": [],
  "valid_from": "2026-05-16T08:00:00+08:00",
  "valid_until": "2026-05-17T08:00:00+08:00",
  "priority": 1
}
```

响应：

```json
{"status":"success","data":{}}
```

#### GET `/api/community/announcements`

Query：

- `community_id`
- `only_active=true`
- `now` 可选
- `limit` 可选

响应：

```json
{"status":"success","data":[]}
```

#### POST `/api/community/activities`

请求体：

```json
{
  "community_id": "community_001",
  "title": "合唱活动",
  "content": "",
  "time_text": "下午三点",
  "location": "活动室",
  "tags": [],
  "valid_until": "2026-05-16T16:00:00+08:00",
  "priority": 1
}
```

注意：

- `valid_until` 必填。

#### GET `/api/community/activities`

Query 同 announcements。

#### GET `/api/community/crisis_alerts`

Query：

- `elder_user_id`
- `limit=20`

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "...",
    "alerts": []
  }
}
```

注意：

- 不带 `community_id`；
- 返回内容会强制脱敏 raw quote。

### 7.9 Proactive / reset

#### GET `/api/proactive_check`

Query：

- `user_id`
- `now` 可选

实际响应：

- 如果有 timed event 或 proactive event，直接返回 event JSON；
- 如果没有：

```json
{"type":"none"}
```

注意：

- 该接口没有统一 `status/data` 包裹。

#### POST `/api/reset_profile?user_id=user_001`

重置 profile，当前会走 `UserContextService.reset_profile()`。

#### POST `/api/reset_memory`

见风险 5.2：当前只重置 legacy RAGHelper，不重置新 DataStore 链路。

---

## 8. 前端文档应优先修正的接口差异

建议下一轮先更新 `docs/frontend_backend_interface_proposal.md`，至少修以下点：

1. 明确它从“方案草案”升级为“当前实现契约”还是继续保留 proposal 身份。
2. 所有 REST 响应统一写真实形状：
   - 多数新接口是 `{"status":"success","data":...}`；
   - `GET /api/profile` 和 `/api/proactive_check` 是例外。
3. `/api/timed_events/due` 示例从 `{"events":[...]}` 改为 `{"status":"success","data":[...]}`。
4. `/api/action_complete` 示例把 `post_reply`、`next_turn_goal` 放到 `data` 下。
5. `/api/planner/status` 示例改为 `data.planner` + `data.care_plan`。
6. `/api/family/messages` 明确当前只支持 `quiet_message`，不支持 `elder_note`。
7. `/api/community/crisis_alerts` 明确当前按 `elder_user_id` 查询，不按 `community_id`。
8. `/api/chat` 补齐当前所有 SSE event type，尤其：
   - `risk_detail`
   - `music_payload`
   - `photos`
   - `photos_result`
   - `expression`
   - `done`
9. 相册章节需要补充 Target18 新字段，并明确“不做实时视觉理解”。
10. 标注 `record_health_complaint` 多用户上下文绑定未完成，避免前端误以为健康主诉工具已完全 per-user。

---

## 9. 建议下一步

建议下一步不是继续扩功能，而是先做一个“接口契约收口 + 高优 bug 修复”小阶段：

1. 修 `user_id` 硬边界：
   - `/api/chat` top-level user_id 覆盖或冲突 400；
   - EmotionalAgent 工具调用注入当前 user_id；
   - reset_memory 明确 per-user 或 legacy-only。

2. 修相册关键词退化：
   - 泛化词只在无实体关键词时触发 list-all；
   - 增加 `看看孙女照片`、`找公园照片` 测试。

3. 修聊天用药查询与 MedicationPlan 的割裂：
   - shared_context 注入当前用药计划/due 事件；
   - MedicalAgent 只基于已记录计划回答。

4. 更新前端接口文档：
   - 把当前真实 API 响应形状写准确；
   - 明确未实现项；
   - 给前端一个可以直接开发的 event/REST contract。

5. 再跑完整回归：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

当前基线是 `120 passed in 11.44s`。


---

## 10. 2026-05-17 修复进展

本轮按“接口契约收口 + 高优 bug 修复”执行，已处理：

1. `/api/chat` 的 `user_id` 边界改为硬校验：顶层 `user_id` 与 `context.user_id` 冲突时返回 400，不再静默采用 context。
2. EmotionalAgent 的 `record_health_complaint` 工具调用会在 ToolNode 执行前绑定当前 `session_context.user_id`，避免默认写入 `user_001`。
3. `MedicalAgent` 的 `medication_query` 已优先读取 `MedicationReminderService` 中的 `MedicationPlan`，旧 `profile.medications` 只作为兼容回退。
4. 相册搜索修正泛化词退化：`看看孙女照片` 这类带实体的请求会保留 `孙女` 作为检索词，不再直接退化成 list-all。
5. `docs/frontend_backend_interface_proposal.md` 已从 proposal 改为当前真实接口 contract，覆盖 REST 响应包裹、SSE 事件、未实现项与前端接入顺序。

新增/更新测试：

- `tests/test_chat_user_id_contract.py`
- `tests/test_agent_safety_convergence.py`
- `tests/test_photo_keyword_normalization.py`

回归要求保持不变：必须使用 `conda activate agent`。

完整回归结果：

```text
126 passed in 12.03s
```

---

## 2026-05-17 追加修复：reset_memory 收口

本轮继续执行“接口契约收口 + 高优 bug 修复”，完成 `/api/reset_memory` 的语义修正：

- `src/services/data_store.py` 新增 `DataStore.reset_user_state(elder_user_id)`，只删除 `users/{elder_user_id}`，并保留其他用户与全局 community 数据。
- `src/services/background_planner_service.py` 新增 `cancel_user_jobs()`，reset 前取消该用户仍在运行的 planner 任务，并记录 `cancelled_by_user_state_reset`，防止旧任务 reset 后回写。
- `src/orchestrator.py` 新增 `SystemOrchestrator.reset_user_state()`，统一编排 planner cancel、DataStore reset、可选 legacy RAG reset、last_system_state 清理。
- `src/server.py` 更新 `POST /api/reset_memory`：现在接受 `user_id` 与 `include_legacy_rag`；默认只做 per-user DataStore reset，不再无条件全局清空 legacy RAG。
- `docs/frontend_backend_interface_proposal.md` 已同步真实 contract。

新增/更新测试：

- `tests/test_data_store.py::test_reset_user_state_removes_only_target_user_directory`
- `tests/test_background_planner_concurrency.py::test_cancel_user_jobs_marks_job_as_user_state_reset`
- `tests/test_reset_memory_api.py`

验证命令仍必须使用：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

本轮验证结果：

```text
132 passed in 11.49s
```
## 2026-05-17 implementation update: photo/music local library contracts

本轮已按“接口契约收口 + 高优 bug 修复”后的下一步实现相册和音乐本地库，不再停留在文档建议：

- 新增 `PhotoLibraryService` 本地相册索引能力：manifest 同步、JSON/SQLite/DB 原始文件缓存导入、关键词查询、Qwen 兼容视觉 caption 缓存。
- 新增 `MusicLibraryService`：前端同步歌名、描述、标签、`playable_ref` 后，后台可按语义匹配要播放的歌曲。
- `ProfessionalSkills.search_family_photos` 已优先查 `data/users/{elder_user_id}/photo_library/`，无结果再 fallback 到原文件服务。
- `ProfessionalSkills.play_music` 和 orchestrator 的 `music_payload` 已接本地音乐库，命中时带回 `music_id`、`playable_ref`、`music_description`。
- `EmotionalConnectionAgent` 工具调用会把当前会话 `user_id` 注入相册/音乐工具，避免默认用户污染。
- 新增 API：
  - `POST /api/photo_library/sync`
  - `POST /api/photo_library/import`
  - `GET /api/photo_library/photos`
  - `POST /api/photo_library/caption_pending`
  - `POST /api/music/library`
  - `GET /api/music/library`
  - `GET /api/music/library/match`
- 前端 contract 详见 `docs/frontend_photo_music_profile_proactive_integration.md`。

已完成 focused regression：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q tests\test_photo_library_service.py tests\test_music_library_service.py tests\test_photo_music_api.py tests\test_photo_music_tool_local_library.py"
# 11 passed in 12.24s

cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q tests\test_music_intent.py tests\test_photo_keyword_normalization.py tests\test_orchestrator_fast_path.py"
# 13 passed in 3.75s
```

Final verification:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
# 143 passed in 10.51s
```
