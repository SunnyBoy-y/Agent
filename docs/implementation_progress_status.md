# 增量实现进度与恢复状态

更新时间：2026-05-16

本文档用于任务中断后的快速恢复。恢复时先阅读本文，再对照 `docs/incremental_update_plan.md` 继续后续 Target。

## 1. 当前完成范围

Completed Targets 1-16 from `incremental_update_plan.md` in the current implementation pass.

### Target 1：核心 Schema 与 DataStore

新增轻量业务数据读写层，避免继续用 `RAGHelper` 承担普通 JSON/JSONL 文件读写。

新增文件：
- `src/services/data_store.py`
- `src/services/__init__.py`
- `src/schemas/mental_health.py`
- `src/schemas/relay.py`
- `src/schemas/actions.py`
- `src/schemas/timed_events.py`
- `src/schemas/family.py`
- `src/schemas/community.py`
- `src/schemas/__init__.py`
- `tests/test_data_store.py`

关键能力：
- 支持 JSON / JSONL 读写。
- 支持 `data/users/{elder_user_id}/...` 用户隔离路径。
- 使用 `FileLock` 保护并发 JSONL append。
- 防止绝对路径和 `..` 路径逃逸。
- 支持 Pydantic model 写入 JSON。

### Target 2：Profile/UserContext 多用户兼容层

新增按 `user_id` 隔离的画像、短期历史、情绪日志、Agent 状态服务。

新增文件：
- `src/services/profile_service.py`
- `src/services/user_context_service.py`
- `tests/test_user_context_service.py`

已接入接口：
- `POST /api/profile?user_id=elder_001`
- `GET /api/profile?user_id=elder_001`
- `GET /api/system_status?user_id=elder_001`
- `GET /api/proactive_check?user_id=elder_001`
- `POST /api/reset_profile?user_id=elder_001`
- `POST /api/chat` 请求体中的 `user_id`

当前落盘路径示例：

```text
data/users/{user_id}/profile.json
data/users/{user_id}/chat_history.json
data/users/{user_id}/emotion_log.json
data/users/{user_id}/agent_status.json
data/users/{user_id}/mental_assessments.jsonl
```

### Target 3：SafetyPolicy

新增统一安全后处理骨架。

新增文件：
- `src/policies/safety_policy.py`
- `src/policies/__init__.py`
- `tests/test_safety_policy.py`

已覆盖规则：
- 老人端不输出诊断命名，如“抑郁症 / 焦虑症 / 双相情感障碍”。
- 拦截“去医院 / 看医生 / 可以吃某药 / 加量 / 减量 / 停药 / 补服”等医疗或用药建议。
- 允许“按已记录医嘱 / 按记录 / 照护者录入”这类用药提醒措辞。
- `crisis` 回复会补充稳定当下的安全感前缀。
- 拦截自伤方法类细节。

当前接入状态：
- 非 `emotional_agent` 的普通文本输出已走 `SafetyPolicy.sanitize_response()`。
- `emotional_agent` 仍是流式 token 输出，后续需要单独接入流式安全兜底或完成句后安全检查。

### Target 4：AssessmentService 快速风险评估

新增 Python 快速风险评估骨架，并在实时链路开头接入。

新增文件：
- `src/services/assessment_service.py`
- `tests/test_assessment_service.py`

已覆盖规则：
- “活着没意思”
- “不想活了”
- “死了算了”
- “我想去死”
- “不想再撑”

上述表达直接判定为 `crisis`，不被保护性表达降级。

已支持：
- 输出 `MentalRiskAssessment` 结构。
- 记录 `risk_tier`、`primary_state`、`confidence`、`score`、`evidence`、`raw_quotes`、`next_goal`、多端可见性。
- `crisis` 默认生成 family 可见原话摘要，community 只给危机摘要和建议，不暴露老人原话。
- “焦虑导致头疼 / 心慌 / 睡不着”优先识别为 `anxiety`，不走用药建议。
- 每次评估保存到 `data/users/{user_id}/mental_assessments.jsonl`。

### Target 5：TimedEvent 与用药提醒服务

新增不依赖 LLM 的定时事件与用药提醒服务层。

新增文件：
- `src/services/timed_event_service.py`
- `src/services/medication_reminder_service.py`
- `tests/test_medication_reminder_service.py`

关键能力：
- `TimedEventService` 管理通用 `TimedEvent` 当前状态与审计日志。
- `MedicationReminderService` 管理 `MedicationPlan`、每日 `MedicationDoseEvent`、due/overdue/expired 状态推进。
- 用药计划保存到 `data/users/{user_id}/medication_plans.json`。
- 用药剂量事件当前状态保存到 `data/users/{user_id}/medication_dose_events.json`。
- 用药剂量事件审计保存到 `data/users/{user_id}/medication_dose_events.jsonl`。
- 通用定时事件当前状态保存到 `data/users/{user_id}/timed_events.json`。
- 通用定时事件审计保存到 `data/users/{user_id}/timed_events.jsonl`。

已覆盖行为：
- 08:00 计划在 08:00-08:30 返回 `medication_due`。
- 08:31 到过期前返回一次 `medication_overdue`。
- 同一状态重复扫描不会重复返回提醒。
- 超过 `expire_at` 后标记 `expired`，`ack=missed`，不再返回提醒。
- `ack=taken` 后不会再返回同一剂量事件。
- `ack=snooze` 后在 snooze 到期前不提醒，到期后可再次提醒。
- 缺少剂量时不编造剂量，只提示按家里保存的医嘱或药盒标签确认。
- 提醒文案不包含“补服/加量/减量/停药/换药/去医院/看医生”等禁用表达。

### Target 6：定时事件接口与 proactive_check 合流

新增 FastAPI 接口，并让主动关怀入口优先返回定时事件。

修改文件：
- `src/server.py`
- `src/orchestrator.py`
- `src/services/timed_event_service.py`

新增测试：
- `tests/test_timed_event_api.py`

已接入接口：
- `GET /api/medication/plans?elder_user_id=elder_001`
- `POST /api/medication/plans`
- `PATCH /api/medication/plans/{medication_id}?elder_user_id=elder_001`
- `GET /api/timed_events/due?elder_user_id=elder_001&now=2026-05-16T08:00:00+08:00`
- `POST /api/timed_events/{event_id}/ack?now=2026-05-16T08:05:00+08:00`
- `GET /api/proactive_check?user_id=elder_001&now=2026-05-16T08:00:00+08:00`

已完成行为：
- `/api/timed_events/due` 会先扫描用药计划，再返回 due/overdue 定时事件。
- `/api/timed_events/{event_id}/ack` 支持 `taken/snooze/skip/not_sure/missed`。
- `ack=taken` 会同步标记同一 `dose_event_id` 对应的 due/overdue timed events。
- `/api/proactive_check` 先查 timed event，再查普通主动关怀。
- `server.py` 保持模块顶层导入 `SystemOrchestrator`，项目启动和验证统一使用 `conda activate agent` 后的完整环境。

### Target 7：RelayMessageService 与危机联动队列

新增多端消息队列服务，先完成服务层落盘和可见性约束，HTTP 接口留到后续目标。

新增文件：
- `src/services/relay_message_service.py`
- `tests/test_relay_message_service.py`

关键能力：
- family/community/elder/frontend relay message 统一保存到 `data/users/{user_id}/relay_messages.json`。
- relay message 审计保存到 `data/users/{user_id}/relay_messages.jsonl`。
- `crisis` 评估可生成 family alert + community SOS。
- medium/high 评估只生成 family alert，不通知 community。
- family alert 可包含风险摘要、建议和老人原话。
- community SOS 只包含危机原因摘要和处理建议，不包含老人原话。
- quiet message 支持 `actor_role` + `direction`。
- relay message 可从 `pending` 更新为 `acknowledged/cancelled/expired` 等状态。
- ack 历史写入 `payload.ack_history`，便于后续审计和前端状态同步。

### Target 8：Orchestrator 接入 risk_detail 与后台调度入口

已把风险评估和 relay 消息联动接入实时编排器的 fast path。

修改文件：
- `src/orchestrator.py`
- `src/server.py`
- `src/services/relay_message_service.py`

新增测试：
- `tests/test_orchestrator_fast_path.py`

关键能力：
- `risk_detail` 增加 `assessment_id` 和 `tier` 字段，兼容原始 `id` 和 `risk_tier`。
- `medium/high/crisis` 评估会调度后台 relay 消息生成。
- `crisis` 当前轮仍立即输出 `risk_detail`、legacy `risk=crisis`、`sos=true`。
- 后台 relay 任务通过 `asyncio.create_task` fire-and-forget，不等待文件写入后再继续 SSE。
- 后台任务成功/失败状态记录在 `last_system_state.background_tasks`。
- 后台 relay 任务异常只记录日志，不中断本轮 SSE。
- `orchestrator.py` 保持顶层导入核心 Agent，不使用懒加载规避依赖。
- legacy `risk` 收敛为只在非 safe 时输出，避免 safe 场景与 `risk_detail` 不一致。

### Target 9：Router 与 ContextGuard 语义降噪

已新增上下文清洗和语义路由降噪层。

新增/修改文件：
- `src/services/context_guard.py`
- `src/agents/router_agent.py`
- `src/orchestrator.py`
- `tests/test_router_context_guard.py`

关键能力：
- `ContextGuard` 会裁剪过期历史、系统噪声和 proactive 消息，避免把系统主动关怀当作老人原话。
- `shared_context` 进入 Agent 前会经过 `ContextGuard.sanitize_context()`。
- “我一紧张就头疼”这类焦虑身体化表达优先路由 `mental_health_agent`。
- “到了吃药时间了吗”进入当前用药/定时事件查询路径，现阶段仍映射到 `medical_agent`，但只允许基于已记录医嘱/计划查询。
- “头疼得厉害，还喘不上气”保留身体紧急风险路径，进入 `medical_agent`，后续仍由 `SafetyPolicy` 禁止医疗建议。
- “我不想活了”直接进入 `mental_health_agent`，不被音乐/闲聊/医疗问答覆盖。

### Target 10：CarePlanService 与后台 Planner 并发控制

已完成 per-user CarePlan、规则版后台 Planner、版本提交与 stale 丢弃闭环。

新增/修改文件：
- `src/schemas/planner.py`
- `src/services/care_plan_service.py`
- `src/services/background_planner_service.py`
- `src/orchestrator.py`
- `src/server.py`
- `tests/test_care_plan_service.py`
- `tests/test_background_planner_concurrency.py`
- `tests/test_planner_status_api.py`
- `tests/test_orchestrator_fast_path.py`

关键能力：
- `CarePlanService` 统一落盘 `care_plan.json` 与 `care_plan_history.jsonl`，支持版本号、`source_turn_id` 与 compare-and-swap。
- 规则版 `BackgroundPlannerService` 已支持 per-user task 隔离、safe/low debounce、high/crisis 抢占、旧任务 `cancel_requested/stale_discarded` 审计。
- 新增 `planner_jobs.jsonl` 与 `planner_status.json`，以及 `GET /api/planner/status`。
- Fast Path 已可读取上轮 CarePlan 引导 follow-up 路由；后台 Planner 继续 fire-and-forget，不阻塞 `/api/chat`。
- 旧 planner 晚到、不同用户并发、crisis 抢占三类冲突均已有回归测试。


### Target 11: LLM review and constrained ReAct Planner

Completed structured LLM review, constrained planner outputs, fallback behavior, and planner audit persistence.

Changed files:
- `src/agents/planning_agent.py`
- `src/schemas/planner.py`
- `src/services/background_planner_service.py`
- `src/orchestrator.py`
- `tests/test_planning_agent_contract.py`
- `tests/test_background_planner_llm_review.py`

Key capabilities:
- Added formal `LLMReview`, `PlannerQueuedAction`, and `PlannerResult` schemas.
- `PlanningAgent` supports live LLM review/planning, while timeouts or missing credentials fall back to deterministic safe planning.
- `crisis` remains hard-rule authoritative; planner attempts to downgrade it are clamped back to `crisis.safety_grounding`.
- Review snapshots are appended to `mental_assessments.jsonl`; queued actions are audited in `planner_actions.jsonl`; planner status now exposes the latest review status and fallback flag.
- Target 10 concurrency guarantees remain intact under Target 11.

## 2. 实时链路当前行为

`SystemOrchestrator.process_input_stream()` 当前流程：

```text
收到输入
-> 生成 user_id / turn_id
-> AssessmentService.assess_text()
-> SSE 输出 risk_detail
-> 如果非 safe，兼容输出旧 risk
-> 如果 crisis，兼容输出 sos=true
-> crisis/high 强制路由 mental_health_agent
-> 构建 shared_context
-> ContextGuard 清洗过期/系统噪声上下文
-> 执行目标 Agent
-> 非 emotional_agent 输出经 SafetyPolicy 后再分块 token
-> 对话写入 UserContextService
-> fire-and-forget 调度后台 Planner 更新下一轮 CarePlan
```

兼容保留 SSE 事件：
- `risk`
- `sos`
- `token`
- `music_payload`
- `music`
- `action`
- `photos`
- `photos_result`
- `proactive_question`

新增 SSE/JSON 事件：
- `risk_detail`
- `timed_event`

## 3. 已验证命令

已通过：

```powershell
conda activate agent
python -m py_compile src\server.py src\orchestrator.py src\services\timed_event_service.py src\services\medication_reminder_service.py tests\test_timed_event_api.py
python -m pytest tests\test_timed_event_api.py tests\test_medication_reminder_service.py -q
python -m py_compile src\services\relay_message_service.py tests\test_relay_message_service.py
python -m pytest tests\test_relay_message_service.py -q
python -m py_compile src\orchestrator.py src\server.py tests\test_orchestrator_fast_path.py
python -m pytest tests\test_orchestrator_fast_path.py -q
python -m py_compile src\services\context_guard.py src\agents\router_agent.py src\orchestrator.py src\server.py tests\test_router_context_guard.py
python -m pytest tests\test_router_context_guard.py -q
python -m py_compile src\schemas\planner.py src\services\care_plan_service.py src\services\background_planner_service.py tests\test_care_plan_service.py tests\test_background_planner_concurrency.py tests\test_planner_status_api.py
python -m py_compile src\agents\planning_agent.py tests\test_planning_agent_contract.py tests\test_background_planner_llm_review.py
python -m pytest -q
```

最新结果：

```text
110 passed
```

注意：
- 当前必须在 `agent` conda 环境中启动和验证。
- 本机 PowerShell 中 `conda` 不在 PATH；当前验证实际使用 `C:\ProgramData\anaconda3\condabin\conda.bat activate agent` 进入同一个 `agent` 环境。
- 已在 `agent` 环境补充安装 `pytest`，用于后续回归。
- `py_compile` / `pytest` 会生成或修改 `__pycache__`，不要把它们当作业务修改处理。

## 4. 当前工作区变更范围

主要代码变更：
- `src/server.py`
- `src/orchestrator.py`
- `src/agents/proactive_agent.py`
- `src/policies/`
- `src/schemas/`
- `src/services/`

新增测试：
- `tests/test_data_store.py`
- `tests/test_user_context_service.py`
- `tests/test_safety_policy.py`
- `tests/test_assessment_service.py`
- `tests/test_medication_reminder_service.py`
- `tests/test_timed_event_api.py`
- `tests/test_relay_message_service.py`
- `tests/test_orchestrator_fast_path.py`
- `tests/test_router_context_guard.py`
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

生成物：
- `src/**/__pycache__/...`
- `tests/__pycache__/...`

## 5. 当前未完成点

### Target 12: music/singing action completion callbacks

Implemented:
- `src/services/action_session_service.py`
- durable `action_sessions.json` + `action_sessions.jsonl`
- `intervention_log.jsonl` writes for terminal action results
- `POST /api/action_complete`
- music SSE enrichment with `action_id`, `action_type`, `music_name`, and `post_reply`
- idempotent duplicate callback handling

Verified:
- `completed` returns `post_reply`
- `interrupted` ends the session but keeps `completed_intervention=false`
- repeated callbacks do not duplicate audit/intervention writes
- music payloads now create durable action sessions

Target 12 is complete.

### Target 13: FamilyPolicy and quiet-message consumption

Implemented:
- `src/services/family_policy_service.py`
- `src/schemas/family.py` request schemas for family policy update, family message creation, and elder consent.
- Family-side policy APIs:
  - `GET /api/family/agent_policy`
  - `POST /api/family/agent_policy`
- Family-side topic APIs:
  - `GET /api/family/topics/available`
  - `POST /api/family/topics/{topic_id}/consume`
- Family message / alert APIs:
  - `POST /api/family/messages`
  - `GET /api/family/alerts`
- Elder quiet-message APIs:
  - `GET /api/elder/pending_messages`
  - `POST /api/elder/messages/{message_id}/consent`

Verified:
- `max_consumptions` 达到后不再推荐。
- `min_interval_hours` 未到时不推荐。
- topic consume 会更新 `consumed_count` 和 `last_consumed_at`，达到上限后变为 `exhausted`。
- quiet message pending prompt 只暴露 metadata，不提前泄露正文。
- high/crisis 风险场景抑制 quiet-message prompt。
- accepted consent 返回正文并标记 relay message acknowledged。
- rejected consent 不返回正文并标记 cancelled。
- accepted/rejected 重放保持幂等。
- semantic consent 可识别常见中文同意/拒绝表达。

Target 13 is complete.

### Target 14: community announcements/activities with `valid_until`

Implemented:
- `src/services/community_service.py`
- `src/schemas/community.py` request schemas and expanded announcement/activity fields.
- Community announcement APIs:
  - `POST /api/community/announcements`
  - `GET /api/community/announcements`
- Community activity APIs:
  - `POST /api/community/activities`
  - `GET /api/community/activities`
- Community crisis alert API:
  - `GET /api/community/crisis_alerts`
- `SystemOrchestrator` now owns `CommunityService` and exposes wrapper methods.

Verified:
- announcements and activities are isolated by `community_id`.
- `only_active=true` filters expired items.
- announcements respect both `valid_from` and `valid_until`.
- activities require `valid_until`; expired activities are marked `expired` and not returned as active.
- active community items are sorted by priority.
- community crisis alerts are read from `RelayMessageService` target=`community` only.
- community crisis alert output does not expose elder raw quotes or family/private messages.

Target 14 is complete.

### Target 15: family-side SSE Agent and isolated family memory

Implemented:
- `src/services/family_context_service.py`
- `src/agents/family_agent.py`
- `src/schemas/family.py` now includes `FamilyChatRequest`.
- Family-side chat API:
  - `POST /api/family/chat`
- Family-side summary API:
  - `GET /api/family/elder_summary`
- `SystemOrchestrator` now owns `FamilyContextService` and `FamilyAgent`.

Verified:
- family summary exposes family-visible risk evidence, CarePlan, family alerts, family policy, and recent interventions.
- family summary does not include community-only crisis payloads.
- family chat streams `token`, `family_context`, and `done` SSE events.
- family chat history is written only to `data/users/{elder}/family/{child}/family_chat_history.json`.
- family memory is appended only to `data/users/{elder}/family/{child}/family_chat_memory.jsonl`.
- family chat does not write to elder `chat_history.json`.
- family agent output avoids diagnosis naming, medical advice, and internal Thought exposure.

Target 15 is complete.

### Target 16: Agent cleanup and prompt/safety convergence

Implemented:
- `src/agents/antifraud_agent.py`: removed the duplicate `async arun()` and added `SafetyPolicy` sanitization for intervention text.
- `src/agents/medical_agent.py`: converged prompts to health-care recording / known-prescription reminders, added `_finalize_response()`, and made `check_medication_reminder()` a compatibility no-op. Medication timing remains owned by `MedicationReminderService` and `TimedEventService`.
- `src/agents/mental_health_agent.py`: changed the role to companion-style mental support and sanitizes both LLM output and direct anxiety guidance.
- `src/orchestrator.py`: shares one `SafetyPolicy` with Medical/Mental/AntiFraud agents and buffers `emotional_agent` raw streaming chunks before safe token emission.
- `src/tools/professional_skills.py`: `emergency_contact` now separates family/community/SOS payloads, avoids fake 120/doorstep actions, and keeps community output desensitized.
- `tests/test_agent_safety_convergence.py`: added regression coverage for the above behavior.

Verified:
- Target 16 focused regression: `23 passed`.
- Full regression: `110 passed in 13.12s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Target 16 is complete.

Next-stage note:
- The formal incremental plan currently ends at Target 16. Before the next large code slice, draft a Post-Target16 plan covering frontend latency after emotional safety buffering, background-agent action expansion, photo caption/embedding retrieval, and further `RAGHelper` responsibility migration.


### Target 17: sentence-level safe emotional streaming

Implemented:
- `src/orchestrator.py`: `emotional_agent` no longer has to wait for the whole model response on non-crisis turns. It buffers raw chunks, detects completed sentence boundaries, sanitizes each completed segment through `SafetyPolicy`, and then emits SSE `token` events.
- Crisis turns intentionally remain fully buffered so crisis-safe language is applied once and cannot be fragmented or repeated.
- Final model output still goes through `SafetyPolicy` as the authoritative reconciliation path.
- `tests/test_agent_safety_convergence.py`: added coverage for completed-sentence flushing and crisis full buffering.

Verified:
- Focused command: `python -m py_compile src\orchestrator.py tests\test_agent_safety_convergence.py && python -m pytest tests\test_agent_safety_convergence.py tests\test_safety_policy.py -q`.
- Focused result: `16 passed in 1.81s`.
- Full command: `python -m pytest -q`.
- Full result: `112 passed in 10.48s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Target 17 is complete.

Next-stage note:
- Continue with Target18 from `docs/post_target16_next_stage_plan.md`: photo semantic metadata and retrieval contract.


### Target 18: photo semantic metadata and retrieval contract

Implemented:
- `src/tools/professional_skills.py`: added explicit photo semantic metadata fields and local scoring helpers.
- `search_family_photos` now searches existing file-service records by semantic metadata when present: `description`, `caption`, `tags`, `people`, `location`, `time_text`, `taken_at`, `event`, and `album`.
- Non-empty photo searches now merge direct search results with an all-record fallback, then rank/filter locally. This lets uploaded captions/tags work without request-time vision inference.
- Photo result payload keeps legacy fields and adds `description`, `people`, `location`, `time_text`, `caption_source`, `original_file_name`, and `metadata_available`.
- `tests/test_photo_keyword_normalization.py`: added deterministic semantic ranking, fallback, and output-contract coverage; also avoids poisoning real `langchain_core` imports during combined test runs.

Verified:
- Focused command: `python -m py_compile src\tools\professional_skills.py tests\test_photo_keyword_normalization.py && python -m pytest tests\test_photo_keyword_normalization.py tests\test_prompt.py tests\test_agent_safety_convergence.py -q`.
- Focused result: `17 passed in 1.29s`.
- Full command: `python -m pytest -q`.
- Full result: `115 passed in 11.24s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Target 18 is complete.

Next-stage note:
- Continue with Target19 from `docs/post_target16_next_stage_plan.md`: background-agent action expansion and explicit action contracts.


### Target 19: background-agent action contract

Implemented:
- `PlannerQueuedAction` now carries explicit contract fields: `target_channel`, `consent_required`, `approval_required`, `visibility_scope`, `idempotency_key`, and `action_session_id`.
- `PlanningAgent` finalizes missing action-contract fields deterministically and prevents planner actions from remaining ambiguous.
- `BackgroundPlannerService` persists the full action contract and creates durable `ActionSession` records for scheduled frontend music/story actions.
- `ActionSessionService.create_session()` is idempotent by `idempotency_key`.
- `SystemOrchestrator` shares its `ActionSessionService` with the background planner.

Verified:
- Focused result: `12 passed in 2.34s`.
- Full result after Target19: `118 passed in 8.42s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Target 19 is complete.

### Target 20: RAGHelper responsibility migration

Implemented:
- `MedicalAgent` can now record symptom-report health conditions through `UserContextService` / `ProfileService` / `DataStore` when that service is injected.
- `SystemOrchestrator` now injects `UserContextService` into `MedicalAgent`.
- `ProfessionalSkills.record_health_complaint` no longer imports or instantiates `RAGHelper`; it writes through `UserContextService` and returns structured JSON.
- Legacy isolated MedicalAgent tests still pass because `MedicalAgent` falls back to `RAGHelper` when no user-context service is present.

Verified:
- Focused result: `22 passed in 1.43s`.
- Final full result: `120 passed in 8.90s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.

Target 20 is complete.

Post-Target16 plan status:
- `docs/post_target16_next_stage_plan.md` is complete through Target20.
- Before further feature work, create a new next-stage plan document.

## 6. 推荐恢复步骤

如果任务中断，按以下顺序恢复：

1. 打开本文档确认当前完成范围。
2. 运行：

```powershell
git status --short
conda activate agent
python -m pytest -q
```

3. Current baseline is `120 passed` on 2026-05-16. The Post-Target16 plan is complete; create a new next-stage plan before more code.
4. Continue preserving the Target 10/11/12/13/14/15/16 regression guarantees:
- 同一用户连续快速输入时，旧 planner 结果不能覆盖新 care plan。
- 不同用户后台任务互不影响。
- CarePlan 写入有版本号和来源 turn_id。
- Family quiet-message 正文只能在老人同意后返回，拒绝时不能泄露。
- Topic 消费次数和间隔规则不能被后续 community 改动破坏。
- Community 公告/活动过期后不能被 active 列表消费。
- Community crisis alerts 不能泄露老人原话或 family/private 内容。
- Family chat 不能写入老人端 `chat_history.json`。
- Family-side context 不能读取 community-only 内容，不能暴露内部 Thought。

## 7. 恢复时的关键约束

- `crisis` 硬规则不能等待 LLM，也不能被 LLM 降级。
- “活着没意思”继续直接判 `crisis`。
- 新业务 JSON/JSONL 落盘继续走 `DataStore`。
- 不要把普通文件读写继续堆进 `RAGHelper`。
- 老人端不输出诊断命名和医疗建议。
- 子女端可以看到倾向、摘要、建议和老人原话。
- 社区端只接收 `crisis` 摘要和建议，默认不展示老人原话。

## 8. 2026-05-17 post-audit contract fixes

Implemented after the current code audit:

- `/api/chat` now treats top-level `user_id` as the hard boundary and rejects conflicting `context.user_id`.
- EmotionalAgent health complaint tool calls inherit the session `user_id` unless the tool call explicitly provides an elder user id.
- Photo keyword normalization now preserves entity terms like granddaughter/person terms in specific photo requests instead of downgrading to list-all.
- MedicalAgent medication queries now read active `MedicationPlan` records through `MedicationReminderService` before falling back to legacy profile medications.
- `/api/reset_memory` now resets one user's `DataStore` state by `user_id`, cancels that user's background planner work first, and only touches legacy global RAG when `include_legacy_rag=true`.
- `docs/frontend_backend_interface_proposal.md` has been updated to the current backend contract.

Verified:

- Focused reset/DataStore/planner regression: `17 passed in 20.71s`.
- Focused legacy/reset API regression: `12 passed in 1.43s`.
- Full regression: `132 passed in 11.49s`.
- Environment: `conda activate agent` via `C:\ProgramData\anaconda3\condabin\conda.bat`.


## 2026-05-17 Photo/Music local library stage

Implemented:

- Per-user local photo library cache and search service.
- Raw JSON/SQLite/DB photo library import cache under `data/users/{elder_user_id}/photo_library/imports/`.
- Optional Qwen-compatible vision caption generation via `caption_pending`; tests use a fake captioner.
- Per-user music library service with semantic matching over `name`, `description`, `aliases`, `mood_tags`, `scene_tags`.
- New FastAPI endpoints:
  - `POST /api/photo_library/sync`
  - `POST /api/photo_library/import`
  - `GET /api/photo_library/photos`
  - `POST /api/photo_library/caption_pending`
  - `POST /api/music/library`
  - `GET /api/music/library`
  - `GET /api/music/library/match`
- Agent integration:
  - photo tool prefers local cache before external file service;
  - music tool and orchestrator enrich `music_payload` with `music_id` / `playable_ref` / `music_description`;
  - active `user_id` is injected into photo/music tool calls.

Focused verification:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q tests\test_photo_library_service.py tests\test_music_library_service.py tests\test_photo_music_api.py tests\test_photo_music_tool_local_library.py"
# 11 passed in 12.24s

cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q tests\test_music_intent.py tests\test_photo_keyword_normalization.py tests\test_orchestrator_fast_path.py"
# 13 passed in 3.75s
```

Final verification for this stage:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
# 143 passed in 10.51s
```

## 2026-05-17 final frontend integration document

Created `docs/final_frontend_integration_contract.md` as the frontend-facing final contract. It covers all current `src/server.py` routes and the recommended integration order, including special handling for `GET /api/profile` and `GET /api/proactive_check`.

Verification after final frontend document:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
# 143 passed in 11.91s
```
