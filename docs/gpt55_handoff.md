# GPT-5.5 交接文档


## CURRENT STATE (2026-05-16, after Target 20)

- Repository: `C:\Users\13600\Desktop\realtimeASR\111\Agent`.
- Required test environment: `conda activate agent`; reliable command form is `cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && ..."`.
- Targets 1-20 are complete. `docs/post_target16_next_stage_plan.md` is now fully executed through its final Target20.
- Latest full regression: `132 passed in 11.49s` with the required conda env.
- Target19 focused regression: `12 passed in 2.34s` for planner/action contract tests.
- Target20 focused regression: `22 passed in 1.43s` for Medical/UserContext/photo safety-adjacent tests.
- Target19 changed planner/action plumbing: explicit action contract fields, stable idempotency keys, planner action persistence, and frontend action-session creation for scheduled music/story actions.
- Target20 changed RAGHelper responsibility boundaries: symptom/health-condition writes now go through `UserContextService` / `ProfileService` / `DataStore` when available; `record_health_complaint` no longer imports or instantiates `RAGHelper`.
- Post-audit contract fixes completed on 2026-05-17: `/api/chat` top-level `user_id` is a hard boundary, EmotionalAgent health tool calls inherit session `user_id`, photo keyword normalization preserves entity terms, MedicalAgent medication queries read active `MedicationPlan`, and `/api/reset_memory` now resets per-user `DataStore` state by `user_id` while legacy RAG reset is opt-in via `include_legacy_rag=true`.
- Next step: create a new planning document before starting a new feature phase. Do not continue appending unrelated scope to `post_target16_next_stage_plan.md`.

## 0. 先读这个：用户的硬约束

用户已经明确要求：

1. **不是只做最小闭环，而是把当前阶段做到该阶段的最好。**
2. **不得用任何“懒加载 / 跳过测试 / 只跑少量测试”来回避验证。**
3. **测试环境必须使用 `conda activate agent`。**
4. **继续改代码时，要实时更新文档。**
5. **不要回滚、清理、覆盖用户已有的无关改动。**

尤其第 3 条非常重要。  
在这个仓库里，正确的测试执行方式是：

```powershell
conda activate agent
python -m pytest -q
```

如果当前 PowerShell 里 `conda` 没有正确注入，使用：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

**不要把默认环境、系统 Python、或者别的 conda 环境跑出来的结果当成有效结果。**

---

## 1. 当前项目在做什么

这是一个面向空巢老人的多 Agent 陪伴系统。当前代码正在从“单轮聊天机器人”升级为：

- 前台实时聊天链路；
- 后台评估 / 规划链路；
- 定时事件链路；
- 家庭 / 社区 / 音乐等跨端联动链路。

系统当前已经形成三条主链路：

```text
Fast Path
  用户输入
    -> 风险评估
    -> 路由
    -> SSE 实时回复
    -> risk_detail / risk / sos / music_payload 等事件

Background Path
  每轮评估结果
    -> 后台 planner
    -> CarePlan 版本化更新
    -> relay / quiet_message / 后续动作

Timed Event Path
  MedicationPlan / TimedEvent
    -> due / overdue / expired
    -> proactive_check 或 timed_events 接口暴露
```

项目当前的演进主线不是“多堆几个 Agent”，而是先把：

- 安全；
- 数据落盘；
- 用户隔离；
- 版本并发；
- 动作生命周期；

这些底层骨架打稳，再继续扩展多端功能。

---

## 2. Current progress: Targets 1-16 complete

截至 2026-05-16，已经完成：

| Target | 状态 | 关键内容 |
| --- | --- | --- |
| 1 | 完成 | 核心 schema、`DataStore` |
| 2 | 完成 | `ProfileService`、`UserContextService`、`user_id` 隔离 |
| 3 | 完成 | `SafetyPolicy` |
| 4 | 完成 | `AssessmentService` |
| 5 | 完成 | `TimedEventService`、`MedicationReminderService` |
| 6 | 完成 | 用药/定时事件 API 与 `proactive_check` 集成 |
| 7 | 完成 | `RelayMessageService` |
| 8 | 完成 | orchestrator fast path、`risk_detail`、后台 relay 调度 |
| 9 | 完成 | `ContextGuard`、路由保护 |
| 10 | 完成 | `CarePlanService`、`BackgroundPlannerService` 并发控制 |
| 11 | 完成 | `PlanningAgent`、LLM review、受约束 planner 输出 |
| 12 | 完成 | 音乐动作 session、`POST /api/action_complete`、幂等回调 |
| 13 | 完成 | `FamilyPolicyService`、建议话题消费规则、quiet-message 生成/待读/同意或拒绝消费 API |
| 14 | 完成 | `CommunityService`、社区公告/活动 API、`valid_until` 过期过滤、community crisis alert 脱敏读取 |
| 15 | 完成 | `FamilyContextService`、`FamilyAgent`、`POST /api/family/chat` SSE、`GET /api/family/elder_summary`、子女端隔离记忆 |
| 16 | Done | Agent cleanup, prompt convergence, `SafetyPolicy` convergence, emotional streaming safety buffer, split/desensitized `emergency_contact` |

当前全量回归结果：

```text
110 passed
```

最近一轮（Target 12）新增的关键能力：

- `src/services/action_session_service.py`
- `POST /api/action_complete`
- `music_payload` 现在会携带：
  - `action_id`
  - `action_type`
  - `music_name`
  - `post_reply`
- 动作回调支持：
  - `completed`
  - `interrupted`
  - `cancelled`
  - `failed`
- `interrupted` 会结束 session，但 **不会算作完整干预完成**
- 重复回调是幂等的，不会重复写干预日志
- 终态动作会写入：
  - `action_sessions.json`
  - `action_sessions.jsonl`
  - `intervention_log.jsonl`

Target 13 新增的关键能力：

- `src/services/family_policy_service.py`
- `src/schemas/family.py` 扩展：
  - `FamilyPolicyUpdateRequest`
  - `FamilyMessageCreateRequest`
  - `QuietMessageConsentRequest`
- 家庭侧 API：
  - `GET /api/family/agent_policy`
  - `POST /api/family/agent_policy`
  - `GET /api/family/topics/available`
  - `POST /api/family/topics/{topic_id}/consume`
  - `POST /api/family/messages`
  - `GET /api/family/alerts`
- 老人侧 quiet-message API：
  - `GET /api/elder/pending_messages`
  - `POST /api/elder/messages/{message_id}/consent`
- 建议话题现在支持：
  - `active/exhausted/paused` 状态过滤；
  - `max_consumptions` 次数上限；
  - `min_interval_hours` 最小间隔；
  - `last_consumed_at` 与 `consumed_count` 落盘更新。
- quiet message 现在支持：
  - 家庭端创建但不直接暴露给老人；
  - 老人端只先看到 metadata prompt，不暴露正文；
  - 低风险场景允许待读提示；
  - high/crisis 场景抑制待读提示；
  - 老人 `accepted/rejected` 后再消费；
  - `button/semantic/system` consent source；
  - accepted/rejected 重放保持幂等。

Target 14 新增的关键能力：

- `src/services/community_service.py`
- `src/schemas/community.py` 扩展：
  - `CommunityAnnouncementCreateRequest`
  - `CommunityActivityCreateRequest`
  - announcement/activity `valid_until` 过期状态与过滤；
  - announcement `valid_from` 可见窗口；
  - activity `content/time_text/location/tags/priority`。
- 社区侧 API：
  - `POST /api/community/announcements`
  - `GET /api/community/announcements`
  - `POST /api/community/activities`
  - `GET /api/community/activities`
  - `GET /api/community/crisis_alerts`
- 社区公告/活动按 `community_id` 隔离落盘：
  - `data/communities/{community_id}/announcements.json`
  - `data/communities/{community_id}/activities.json`
  - 对应 JSONL audit 文件。
- `only_active=true` 默认只返回当前可消费内容：
  - 已过期公告/活动不返回；
  - `valid_from` 未到的公告不返回；
  - 返回结果按 `priority` 与创建时间排序。
- community crisis alerts 只复用 `RelayMessageService` 中 target=`community` 的危机级消息，并强制脱敏：
  - `raw_quotes=[]`
  - `payload.raw_quote_visible=false`
  - 不暴露老人原话或 family/private 消息。

Target 15 新增的关键能力：

- `src/services/family_context_service.py`
  - 聚合子女端可见的父母摘要；
  - 读取 family-visible risk evidence、CarePlan、family alerts、intervention log、family policy；
  - 明确不读取 community-only 队列；
  - 独立写入 `data/users/{elder}/family/{child}/family_chat_history.json`；
  - 独立追加 `data/users/{elder}/family/{child}/family_chat_memory.jsonl`；
  - 不写入老人端 `chat_history.json`。
- `src/agents/family_agent.py`
  - 第一版确定性 family-side SSE Agent；
  - 不依赖 live LLM；
  - 输出 `token`、`family_context`、`done`；
  - 通过 `SafetyPolicy` 兜底，避免诊断命名、医疗建议和内部 Thought 暴露。
- 新增 API：
  - `POST /api/family/chat`
  - `GET /api/family/elder_summary`
- 新增 schema：
  - `FamilyChatRequest`
- 新增测试：
  - `tests/test_family_context_service.py`
  - `tests/test_family_chat_api.py`

---

## 3. Next step

Targets 17-20 are now complete. `docs/post_target16_next_stage_plan.md` has been fully executed, so the next move is to draft a fresh next-stage plan before another large code slice.

Completed Post-Target16 topics:

- Validate the frontend experience after `emotional_agent` switched from raw token passthrough to safety-buffered token output.
- Decide the next background-agent action layer slice.
- Decide whether album/photo search should move into visual caption / embedding retrieval.
- Continue migrating non-RAG responsibilities out of the legacy `RAGHelper`.
- Define the next family/community/elder interface boundaries before adding endpoints.

### Target 16 delivered

- `AntiFraudAgent`: duplicate `arun` removed; intervention text is sanitized through `SafetyPolicy`.
- `MedicalAgent`: prompts now describe health-care recording and known-prescription reminders, not diagnosis/treatment; `check_medication_reminder()` is a compatibility no-op.
- `MentalHealthAgent`: role converged to companion-style support; LLM and direct guidance output both pass through `SafetyPolicy`.
- `SystemOrchestrator._run_emotional_agent()`: raw streaming chunks are buffered, sanitized, then emitted as token events.
- `ProfessionalSkills.emergency_contact`: output is split into family/community/SOS fields; community text is desensitized and no longer fakes 120 or doorstep actions.
- Added `tests/test_agent_safety_convergence.py`.

Verification:

```text
Target 16 focused regression: 23 passed
Full regression: 110 passed in 13.12s
Environment: conda activate agent
```

---

## 4. 接手后应先读哪些文档

建议按这个顺序读，能最快恢复上下文：

### 第一层：恢复现场

1. `docs/implementation_progress_status.md`
   - 当前已经做到哪一步；
   - 最近一次测试结果；
   - 下一步从哪里继续。

2. `docs/code_change_summary_and_next_steps.md`
   - 最近几轮代码改动的压缩总结；
   - 哪些能力已经落地；
   - 哪些仍未开始。

3. `docs/gpt55_handoff.md`
   - 就是本文档；
   - 主要是为了让你拿到仓库后尽快进入状态。

### 第二层：理解系统设计

4. `docs/incremental_update_plan.md`
   - Target 0-16 的完整路线图；
   - Targets 17-20 are complete; create a fresh next-stage plan before more code.

5. `docs/background_planner_concurrency_design.md`
   - 后台 planner 的并发、取消、stale discard、compare-and-swap；
   - 用户之前问过“旧任务污染”，这里是答案的正式设计版本。

6. `docs/mental_health_cbt_closure_design.md`
   - mental-health 闭环、CarePlan、planner、干预阶段设计。

7. `docs/frontend_backend_interface_proposal.md`
   - 前后端契约；
   - 尤其要看：
     - action completion；
     - family quiet message；
     - community announcement/activity；
     - family-side SSE agent；
     - Agent 清理与 SafetyPolicy 收敛。

8. `docs/event_contract_and_routing_notes.md`
   - SSE 事件语义；
   - `risk_detail`、`music_payload` 等事件的职责。

### 第三层：补背景

9. `docs/interface_data_code_review.md`
10. `docs/risk_fix_priority_plan.md`
11. `docs/todo_discussion.md`
12. `docs/index.md`

---

## 5. 关键代码地图

### 5.1 入口与编排

#### `src/server.py`

FastAPI 入口。当前已包含：

- `/api/chat`
- `/api/profile`
- `/api/system_status`
- `/api/planner/status`
- `/api/medication/plans`
- `/api/timed_events/due`
- `/api/timed_events/{event_id}/ack`
- `/api/action_complete`
- `/api/proactive_check`

#### `src/orchestrator.py`

当前系统的主编排器。重点关注：

- `process_input_stream()`
- `_select_target_agent()`
- `_normalize_music_payload()`
- `complete_action()`
- `check_and_generate_proactive_event()`

它把这些能力串起来：

- `AssessmentService`
- `SafetyPolicy`
- `ContextGuard`
- `TimedEventService`
- `MedicationReminderService`
- `RelayMessageService`
- `CarePlanService`
- `PlanningAgent`
- `BackgroundPlannerService`
- `ActionSessionService`

### 5.2 风险、规划、并发

#### `src/services/assessment_service.py`

- 负责规则优先的风险评估；
- `crisis` 不能被 LLM 降级。

#### `src/policies/safety_policy.py`

- 对危险输出、医疗越界、敏感建议做硬约束。

#### `src/services/care_plan_service.py`

- per-user care plan；
- 有版本号；
- 使用 compare-and-swap 防止旧任务覆盖新计划。

#### `src/services/background_planner_service.py`

- per-user 后台 planner；
- 支持 debounce / cancel / stale discard；
- 记录：
  - `planner_jobs.jsonl`
  - `planner_actions.jsonl`
  - `planner_status.json`

#### `src/agents/planning_agent.py`

- 受约束 planner；
- LLM review 只做结构化复核，不暴露 thought；
- planner 输出只允许结构化字段；
- `crisis` 不能被 planner 降级。

### 5.3 动作、消息、定时事件

#### `src/services/action_session_service.py`

Target 12 新增。  
负责音乐等动作生命周期：

- 创建 session；
- 终态回调；
- 幂等；
- 写 intervention log。

#### `src/services/relay_message_service.py`

- family / community / elder / frontend 的消息队列；
- 已支持 quiet message；
- Target 13 会继续在它周围生长策略层。

#### `src/services/timed_event_service.py`

- 定时事件状态流转。

#### `src/services/medication_reminder_service.py`

- 药物计划、剂次事件、due/overdue/expired。

### 5.4 schema

重点：

- `src/schemas/mental_health.py`
  - `MentalRiskAssessment`
  - `CarePlan`
  - `InterventionLog`

- `src/schemas/planner.py`
  - `PlannerJob`
  - `PlannerStatus`
  - `LLMReview`
  - `PlannerQueuedAction`
  - `PlannerResult`

- `src/schemas/actions.py`
  - `ActionSession`
  - `ActionCompleteRequest`

- `src/schemas/family.py`
  - `SuggestedTopic`
  - `FamilyPolicy`

- `src/schemas/relay.py`
  - `RelayMessage`
  - `RelayAck`

- `src/schemas/timed_events.py`
  - `MedicationPlan`
  - `MedicationDoseEvent`
  - `TimedEvent`
  - `TimedEventAck`

---

## 6. 必须掌握的行为语义

### 6.1 “旧任务污染”是什么意思

用户之前专门问过这个问题。

正确语义是：

- 每次用户输入都立即由前台链路处理；
- 后台 planner 可以并发启动；
- 如果新一轮输入已经来了，旧 planner 即使晚完成，也不能覆盖新一轮对应的 care plan；
- 这靠：
  - per-user 串行状态；
  - `latest_turn_id`
  - `base_care_plan_version`
  - compare-and-swap
  - stale discard

所以：

- 第一次对话处理完；
- 第二次启动；
- 第三次又很快启动；
- 那么后台最终只允许与“最新有效 turn”一致的结果提交。

### 6.2 音乐动作现在是什么状态

音乐不是“发了个 SSE 就结束”了。

当前已经是：

```text
music_payload
  -> action_id
  -> frontend playback
  -> POST /api/action_complete
  -> ActionSession terminal state
  -> intervention_log
```

区分：

- `completed`
  - 可返回 `post_reply`
  - `completed_intervention=true`

- `interrupted`
  - 结束 session
  - `completed_intervention=false`

这点不要改坏。

### 6.3 当前相册能力是什么

用户前面也问过相册相关问题，结论如下：

- 目前没有独立的“相册 REST API”；
- 当前相册能力来自工具层：
  - `src/tools/professional_skills.py::search_family_photos`
- 通过外部文件服务接口搜索：
  - `/api/file/search`
  - `/api/file/download/{uuid}`
- `src/orchestrator.py` 会把结果以 SSE 事件发出去：
  - `photos`
  - `photos_result`

当前相册功能：

- 依赖文件名 / tag 等元数据；
- **不能真正理解图片像素内容**；
- 没有视觉 captioning / embedding 检索；
- 与后台 agent 暂时不冲突，因为它目前还是当前轮用户触发工具，不是后台自治动作。

如果以后要做“后台主动挑照片干预”，不要让它和现有相册链路各长一套，应该统一到动作层 / 媒体层。

---

## 7. 当前测试状态与推荐验证顺序

### 7.1 当前全量回归

必须在 `agent` 环境中执行：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

当前结果：

```text
110 passed
```

### 7.2 最近一轮 Target 15 的重点测试

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m py_compile src\schemas\family.py src\services\family_context_service.py src\agents\family_agent.py src\orchestrator.py src\server.py tests\test_family_context_service.py tests\test_family_chat_api.py && python -m pytest tests\test_family_context_service.py tests\test_family_chat_api.py -q"
```

当前结果：

```text
6 passed
```

### 7.3 建议接手后先跑的验证

先跑：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

如果还是：

```text
110 passed
```

Continue with the Post-Target16 next-stage plan.

不要因为“只改 Agent 提示词或清理代码”就省掉全量回归。用户明确要求不要偷这个懒。

---

## 8. 当前已有的重要测试文件

建议至少认识这些：

- `tests/test_data_store.py`
- `tests/test_user_context_service.py`
- `tests/test_safety_policy.py`
- `tests/test_assessment_service.py`
- `tests/test_medication_reminder_service.py`
- `tests/test_timed_event_api.py`
- `tests/test_relay_message_service.py`
- `tests/test_orchestrator_fast_path.py`
- `tests/test_router_context_guard.py`
- `tests/test_care_plan_service.py`
- `tests/test_background_planner_concurrency.py`
- `tests/test_planner_status_api.py`
- `tests/test_planning_agent_contract.py`
- `tests/test_background_planner_llm_review.py`
- `tests/test_action_complete.py`
- `tests/test_music_intent.py`
- `tests/test_family_policy_service.py`
- `tests/test_family_policy_api.py`
- `tests/test_community_service.py`
- `tests/test_community_api.py`
- `tests/test_family_context_service.py`
- `tests/test_family_chat_api.py`

Target 16 added `tests/test_agent_safety_convergence.py`; continue preserving:

- `tests/test_agent_resilience_unittest.py`
- `tests/test_safety_policy.py`
- 视实际修改补充 MedicalAgent / antifraud / tool safety 回归测试

---

## 9. 当前工作区注意事项

当前工作区是脏的，不是“只剩你这轮改动”：

- 有多份 `__pycache__`
- 有未跟踪的新 schema / service / test 文件
- 有历史文档改动
- 有用户之前已有的修改

**不要为了“看起来干净”就清理、reset、revert。**

尤其不要误删：

- `docs/code_change_summary_and_next_steps.md`
- `docs/implementation_progress_status.md`
- `docs/gpt55_handoff.md`
- `src/agents/planning_agent.py`
- `src/services/*`
- `src/schemas/*`
- 新增测试文件

如果要做提交前清理，必须先重新判断哪些是这轮真正应提交的，哪些只是 pycache。

---

## 10. 文档中还存在的现实问题

部分旧文档存在明显编码污染 / mojibake。  
当前最重要的几份“恢复文档”已经足够用，但如果后续继续大规模修文档，建议：

1. 优先保证内容正确；
2. 再统一做编码清理；
3. 不要在实现 Target 13 的同时把整个 docs 目录顺手翻新，否则容易把任务拖散。

---

## 11. 如果只剩几分钟，怎么最快恢复上下文

按下面顺序执行：

```powershell
git status --short
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

然后依次打开：

1. `docs/implementation_progress_status.md`
2. `docs/code_change_summary_and_next_steps.md`
3. `docs/incremental_update_plan.md`
4. `docs/frontend_backend_interface_proposal.md`
5. `src/schemas/family.py`
6. `src/services/relay_message_service.py`
7. `src/server.py`
8. `src/services/community_service.py`
9. `src/services/family_context_service.py`
10. `src/agents/family_agent.py`

接着从：

```text
Post-Target16: next-stage planning and feature slicing
```

继续。

---

## 12. 最后一条交接判断

当前项目已经越过“能不能做”的阶段，进入“要把系统事实做干净”的阶段。  
后续每一轮都应继续维持这个原则：

- 前台链路快；
- 后台链路稳；
- 危机信号硬；
- 数据落盘真；
- 动作状态可追；
- 文档与代码同步。

如果继续保持这个标准，Target 16 之后的故事干预和后续多端协作都会顺很多。


## Target 17 delivered - sentence-level safe emotional streaming

Implemented:
- `src/orchestrator.py`: `emotional_agent` stream chunks now accumulate into a sentence buffer and only completed sentence segments are emitted after `SafetyPolicy.sanitize_response()`.
- Crisis turns remain fully buffered, preserving one crisis-safe prefix and avoiding repeated per-sentence safety boilerplate.
- Final model output remains the authoritative sanitized reconciliation path.
- `tests/test_agent_safety_convergence.py`: added regressions for completed-sentence streaming and crisis buffering.

Verification:
- Focused: `16 passed in 1.81s`.
- Full: `112 passed in 10.48s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Next recommended target:
- Target18: implement the photo semantic metadata/retrieval contract described in `docs/post_target16_next_stage_plan.md`.


## Target 18 delivered - photo semantic metadata and retrieval contract

Implemented:
- `src/tools/professional_skills.py`: added semantic photo fields, local semantic scoring, metadata availability detection, and normalized photo result construction.
- `search_family_photos` now merges direct search results with an all-record fallback for non-empty queries, ranks by existing metadata, filters weak matches, and does not call any vision model at request time.
- Result payload keeps legacy `url` / `desc` / `type` / `tags` and adds `description`, `people`, `location`, `time_text`, `caption_source`, `original_file_name`, and `metadata_available`.
- `tests/test_photo_keyword_normalization.py`: added semantic scoring, fallback search, output contract tests, and fixed optional stubs so this test does not overwrite real `langchain_core` modules during broader test runs.

Verification:
- Focused: `17 passed in 1.29s`.
- Full: `115 passed in 11.24s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Next recommended target:
- Target19: background-agent action expansion with explicit action contracts, idempotency, approval/consent requirements, and elder/family/community visibility boundaries.


## Target 19 delivered - background-agent action contract

Implemented:
- `src/schemas/planner.py`: extended `PlannerQueuedAction` with explicit action contract fields.
- `src/agents/planning_agent.py`: finalizes target channel, consent/approval requirements, visibility scope, and stable idempotency key for every queued planner action.
- `src/services/background_planner_service.py`: persists the full contract and creates durable `ActionSession` records for `schedule_music` / `schedule_story` frontend actions.
- `src/services/action_session_service.py`: `create_session()` now reuses existing sessions with the same idempotency key.
- `src/orchestrator.py`: passes the shared action-session service into the background planner.

Verification:
- Focused: `12 passed in 2.34s`.
- Full after Target19: `118 passed in 8.42s`.

## Target 20 delivered - RAGHelper responsibility migration

Implemented:
- `src/agents/medical_agent.py`: symptom reports write through injected `UserContextService` when available; legacy direct construction still falls back to `RAGHelper`.
- `src/orchestrator.py`: constructs and injects `UserContextService` into `MedicalAgent`.
- `src/tools/professional_skills.py`: `record_health_complaint` now writes through `UserContextService` and returns structured JSON; no `RAGHelper` import/instantiation remains in that tool.
- `tests/test_agent_safety_convergence.py` and `tests/test_user_context_service.py`: added regression coverage for typed-service health-condition writes.

Verification:
- Focused: `22 passed in 1.43s`.
- Final full: `120 passed in 8.90s`.

Post-Target16 document status:
- `docs/post_target16_next_stage_plan.md` is complete through its final Target20. The next agent should draft a fresh next-stage plan before new code scope.

## 2026-05-17 photo/music local library implementation

User request: implement the documented photo local cache + Qwen vision caption path, and music library contract where the frontend only provides song name/description and backend agent decides when to play.

Implemented files:

- `src/schemas/photo_library.py`
- `src/schemas/music_library.py`
- `src/services/photo_library_service.py`
- `src/services/music_library_service.py`
- `src/tools/professional_skills.py`
- `src/agents/emotional_agent.py`
- `src/agents/interest_agent.py`
- `src/orchestrator.py`
- `src/server.py`

Implemented behavior:

- `PhotoLibraryService` stores per-user photo manifests at `data/users/{elder_user_id}/photo_library/photos.json`.
- `POST /api/photo_library/import` stores raw imported JSON/SQLite/DB bytes under `data/users/{elder_user_id}/photo_library/imports/{import_id}/...` before parsing.
- Qwen vision captioning is behind `PhotoLibraryService.caption_pending()`. It uses the configured OpenAI-compatible Qwen model at runtime; tests inject a fake captioner.
- `search_family_photos` now searches the local per-user photo library first when `username` is available, then falls back to the original file service.
- `MusicLibraryService` stores per-user songs at `data/users/{elder_user_id}/music_library/songs.json`.
- `play_music` and orchestrator `_normalize_music_payload()` now enrich music actions with local-library `music_id`, `playable_ref`, and `music_description`.
- Tool calls inherit active session user boundaries:
  - `search_family_photos` gets `username=session_context.user_id`.
  - `play_music` gets `elder_user_id=session_context.user_id` when called as a tool.
  - InterestAgent passes `context.user_id` into `play_music`.

New API endpoints:

- `POST /api/photo_library/sync`
- `POST /api/photo_library/import`
- `GET /api/photo_library/photos`
- `POST /api/photo_library/caption_pending`
- `POST /api/music/library`
- `GET /api/music/library`
- `GET /api/music/library/match`

Tests added:

- `tests/test_photo_library_service.py`
- `tests/test_music_library_service.py`
- `tests/test_photo_music_api.py`
- `tests/test_photo_music_tool_local_library.py`

Verification so far:

- Focused new photo/music tests: `11 passed in 12.24s`.
- Existing music/photo/orchestrator regressions: `13 passed in 3.75s`.
- Required environment command:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

Docs updated:

- `docs/frontend_photo_music_profile_proactive_integration.md`
- `docs/frontend_backend_interface_proposal.md`
- `docs/current_code_audit_and_frontend_interface_delta.md`

Final full verification for 2026-05-17 photo/music local library stage:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
# 143 passed in 10.51s
```
