# 代码变更总结、测试方式与下一步计划

更新时间：2026-05-16

本文用于快速了解最近几轮代码增量已经完成了什么、当前新增了哪些能力、如何验证，以及下一步继续做什么。更细的恢复信息见 `docs/implementation_progress_status.md`，完整目标拆分见 `docs/incremental_update_plan.md`。

## 1. 已完成任务

Current implementation coverage: Targets 1-16 are complete in the current pass.

| Target | 已完成内容 | 核心文件 |
| --- | --- | --- |
| Target 1 | 核心 Schema 与 `DataStore` | `src/schemas/*`, `src/services/data_store.py` |
| Target 2 | 多用户画像与上下文隔离 | `profile_service.py`, `user_context_service.py` |
| Target 3 | 统一安全红线后处理 | `src/policies/safety_policy.py` |
| Target 4 | 快速心理风险评估 | `src/services/assessment_service.py` |
| Target 5 | 定时事件与用药提醒服务 | `timed_event_service.py`, `medication_reminder_service.py` |
| Target 6 | 用药/定时事件 HTTP 接口与 `proactive_check` 合流 | `src/server.py`, `src/orchestrator.py` |
| Target 7 | 子女/社区/老人端消息队列 | `src/services/relay_message_service.py` |
| Target 8 | `risk_detail` 与后台 relay 调度接入 Orchestrator | `src/orchestrator.py` |
| Target 9 | `ContextGuard` 与语义路由降噪 | `context_guard.py`, `router_agent.py` |
| Target 10 | `CarePlanService` 与后台 Planner 并发控制 | `care_plan_service.py`, `background_planner_service.py`, `src/server.py` |
| Target 11 | LLM review and constrained ReAct Planner | `planning_agent.py`, `background_planner_service.py`, `src/schemas/planner.py` |
| Target 12 | Durable music action sessions and completion callbacks | `action_session_service.py`, `src/server.py`, `src/orchestrator.py` |
| Target 13 | FamilyPolicy?suggested topics?quiet-message consent | `family_policy_service.py`, `src/schemas/family.py`, `src/server.py` |
| Target 14 | Community announcements/activities and sanitized crisis alerts | `community_service.py`, `src/schemas/community.py`, `src/server.py` |
| Target 15 | Family-side SSE Agent and isolated family memory | `family_context_service.py`, `family_agent.py`, `src/server.py` |
| Target 16 | Agent cleanup, prompt convergence, SafetyPolicy convergence | `antifraud_agent.py`, `medical_agent.py`, `mental_health_agent.py`, `orchestrator.py`, `professional_skills.py` |
| Target 17 | Sentence-level safe emotional streaming | `orchestrator.py`, `test_agent_safety_convergence.py` |
| Target 18 | Photo semantic metadata and retrieval contract | `professional_skills.py`, `test_photo_keyword_normalization.py` |
| Target 19 | Background-agent action contract | `planner.py`, `planning_agent.py`, `background_planner_service.py`, `action_session_service.py`, `orchestrator.py` |
| Target 20 | RAGHelper responsibility migration | `medical_agent.py`, `professional_skills.py`, `orchestrator.py`, `test_agent_safety_convergence.py`, `test_user_context_service.py` |

## 2. 新增功能

### 数据与多用户

- 新增轻量 `DataStore`，用于 JSON/JSONL 业务数据落盘。
- 新业务数据按 `data/users/{user_id}/...` 隔离。
- 新增用户画像、短期聊天历史、情绪日志、Agent 状态的多用户服务。

### 风险评估与安全红线

- 新增 `AssessmentService`，实时链路开头先做快速心理风险评估。
- “活着没意思”“不想活了”等表达直接判定为 `crisis`。
- 新增 `risk_detail` SSE 事件，包含 `assessment_id`、`tier`、`risk_tier`、证据、下一步目标等字段。
- 新增 `SafetyPolicy`，拦截诊断命名、医疗建议、用药调整建议、自伤方法细节。

### 定时事件与用药提醒

- 新增 `MedicationPlan`、`MedicationDoseEvent`、`TimedEvent`。
- 支持用药 due、overdue、expired 状态推进。
- 支持 `taken/snooze/skip/not_sure/missed` 确认。
- 用药提醒只读取已记录医嘱/照护者录入信息，不生成新剂量，不建议补服、加减量、停药、换药。

已实现接口：

```text
GET /api/medication/plans?elder_user_id=elder_001
POST /api/medication/plans
PATCH /api/medication/plans/{medication_id}?elder_user_id=elder_001
GET /api/timed_events/due?elder_user_id=elder_001
POST /api/timed_events/{event_id}/ack
GET /api/proactive_check?user_id=elder_001
```

### 多端消息队列

- 新增 `RelayMessageService`。
- crisis 会生成 family alert + community SOS。
- family 可见风险摘要、建议和老人原话。
- community 只看危机摘要和建议，不展示老人原话。
- quiet message 支持 `actor_role` + `direction`。
- 消息支持 pending 到 acknowledged/cancelled/expired 等状态更新。

### 编排器与路由

- `Orchestrator` 已接入快速风险评估、`risk_detail`、后台 relay 调度。
- 后台 relay 任务失败只记录日志，不中断当前 SSE。
- `ContextGuard` 会清理过期历史、系统噪声、主动关怀噪声。
- “我一紧张就头疼”优先路由心理支持，不走用药建议。
- “到了吃药时间了吗”进入用药/定时事件查询路径。
- “头疼得厉害，还喘不上气”保留身体紧急风险路径，但仍由安全策略禁止医疗建议。

### CarePlan 与后台 Planner

- 新增 `CarePlanService`，统一管理当前计划、历史版本与 compare-and-swap 提交。
- 新增规则版 `BackgroundPlannerService`，支持 per-user task 隔离、safe/low debounce、high/crisis 抢占、stale 丢弃。
- 新增 `PlanningAgent`，把 LLM 复核与下一步规划收敛为结构化 `LLMReview`、`PlannerResult` 和 queued actions。
- live LLM 不可用、超时或失败时会自动回退到确定性安全规划；`crisis` 仍由硬规则主导，不能被 Planner 降级。
- 新增 `planner_jobs.jsonl` 与 `planner_status.json` 审计/查询落盘，状态中包含最近一次 review 状态和 fallback 标记。
- 新增 `planner_actions.jsonl`，并把 review 快照追加到 `mental_assessments.jsonl`。
- 新增 `GET /api/planner/status`，同时返回 planner 状态与当前 CarePlan。
- Fast Path 已能读取上轮 CarePlan 引导 follow-up 路由，但后台 Planner 不阻塞 SSE 首 token。

## 3. 如何测试

项目启动和测试必须使用 `agent` conda 环境。

如果当前 shell 已能直接使用 `conda`：

```powershell
conda activate agent
python -m pytest -q
```

如果 PowerShell 中 `conda` 不在 PATH，可使用本机已验证路径：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

当前完整回归结果：

```text
110 passed
```

启动服务可使用：

```powershell
conda activate agent
python -m uvicorn src.server:app --host 0.0.0.0 --port 8082
```

## 4. Next step

Target 16 is complete. Next step: create the Post-Target16 next-stage plan before starting the next large feature slice.

### Target 12 delivered

- Added durable action sessions for music flows.
- `music_payload` now carries `action_id`, `action_type`, `music_name`, and `post_reply`.
- Added `POST /api/action_complete`.
- `completed / interrupted / cancelled / failed` are now separated cleanly.
- Duplicate callbacks are idempotent.
- `interrupted` ends a session without counting as a completed intervention.

### Target 13 delivered

- Added `src/services/family_policy_service.py`.
- Extended `src/schemas/family.py` with family policy update, family message creation, and quiet-message consent request schemas.
- Added family policy APIs:
  - `GET /api/family/agent_policy`
  - `POST /api/family/agent_policy`
- Added suggested-topic consumption APIs:
  - `GET /api/family/topics/available`
  - `POST /api/family/topics/{topic_id}/consume`
- Added family/elder quiet-message APIs:
  - `POST /api/family/messages`
  - `GET /api/family/alerts`
  - `GET /api/elder/pending_messages`
  - `POST /api/elder/messages/{message_id}/consent`
- Topic recommendation now respects `status`, `max_consumptions`, `min_interval_hours`, `consumed_count`, and `last_consumed_at`.
- Quiet-message consumption now withholds content until elder consent, suppresses prompts under high/crisis risk, and handles accepted/rejected replay idempotently.
- Added `tests/test_family_policy_service.py` and `tests/test_family_policy_api.py`.

### Target 14 delivered

- Added `src/services/community_service.py`.
- Extended `src/schemas/community.py` with create request schemas and richer announcement/activity fields.
- Added community announcement APIs:
  - `POST /api/community/announcements`
  - `GET /api/community/announcements`
- Added community activity APIs:
  - `POST /api/community/activities`
  - `GET /api/community/activities`
- Added `GET /api/community/crisis_alerts`.
- Announcements and activities are isolated under `data/communities/{community_id}/...`.
- `only_active=true` filters expired items; announcements also respect `valid_from`.
- Activities require `valid_until` and expired activities are not returned to active consumers.
- Community crisis alerts are sanitized so raw elder quotes and family/private content do not leak.
- Added `tests/test_community_service.py` and `tests/test_community_api.py`.

### Target 15 delivered

- Added `src/services/family_context_service.py`.
- Added `src/agents/family_agent.py`.
- Extended `src/schemas/family.py` with `FamilyChatRequest`.
- Added `POST /api/family/chat` SSE endpoint.
- Added `GET /api/family/elder_summary`.
- `FamilyContextService` aggregates family-visible profile, CarePlan, risk evidence, family alerts, family policy, and intervention logs.
- Family chat history is isolated under `data/users/{elder}/family/{child}/family_chat_history.json`.
- Family chat memory is isolated under `data/users/{elder}/family/{child}/family_chat_memory.jsonl`.
- Family chat does not write to elder `chat_history.json`.
- Family-side context excludes community-only crisis payloads and internal Thought.
- FamilyAgent streams `token`, `family_context`, and `done` events and applies `SafetyPolicy`.
- Added `tests/test_family_context_service.py` and `tests/test_family_chat_api.py`.

### Target 16 delivered

- Removed duplicate `AntiFraudAgent.arun` and added intervention-level `SafetyPolicy` sanitization.
- `MedicalAgent` no longer owns medication timer scheduling; `check_medication_reminder()` is now a compatibility no-op and timing remains in `MedicationReminderService` / `TimedEventService`.
- Medical prompts now focus on health-care recording and known-prescription reminders, not diagnosis, treatment, hospital advice, or medication adjustment.
- `MentalHealthAgent` now presents as a companion-style support assistant, not a clinical diagnosis role, and sanitizes both LLM and direct guidance output.
- `emotional_agent` streaming no longer emits raw model chunks directly; orchestrator buffers model text, runs `SafetyPolicy`, then emits safe token events.
- `ProfessionalSkills.emergency_contact` now separates `family_message`, `community_message`, `recommended_channels`, and frontend SOS intent; community output is desensitized and no longer fakes 120/doorstep actions.
- Added `tests/test_agent_safety_convergence.py`.
- Target 16 focused regression: `23 passed`.
- Full regression: `110 passed in 13.12s` with `conda activate agent`.

### Next step

- Target20 is complete. `docs/post_target16_next_stage_plan.md` is fully executed; draft a new next-stage plan before more code.

## 5. 当前注意事项

- 不使用懒加载规避依赖；正常启动路径就是 `conda activate agent` 后启动。
- `emotional_agent` 流式 token 还没有逐句安全后处理，这是后续安全闭环重点。
- `MedicalAgent` 提示词仍需 Target 16 收敛。
- `__pycache__` 是测试和编译生成物，不属于业务改动。


### Target 17 delivered

- `SystemOrchestrator._run_emotional_agent()` now emits completed non-crisis sentence segments after `SafetyPolicy` sanitization instead of waiting for the entire model response.
- Crisis emotional turns remain fully buffered so safety framing is applied once and cannot be fragmented.
- Final model output remains a sanitized reconciliation path.
- Added tests for safe completed-sentence streaming and crisis full buffering.
- Focused regression: `16 passed in 1.81s`.
- Full regression: `112 passed in 10.48s` with `conda activate agent`.

### Current next step

- Target18 should implement the photo semantic metadata/retrieval contract in `docs/post_target16_next_stage_plan.md`.
- Supersedes older notes claiming `emotional_agent` still lacks sentence-level safety; that gap is now closed by Target17.
- Supersedes older notes claiming MedicalAgent still needs Target16 convergence; Target16 is complete.


### Target 18 delivered

- `search_family_photos` now ranks by existing semantic metadata instead of only filename/tags.
- Supported metadata fields: `description`, `caption`, `tags`, `people`, `location`, `time_text`, `taken_at`, `event`, `album`.
- Non-empty searches merge direct file-service results with an all-record fallback and filter weak local matches; no request-time vision captioning is introduced.
- Photo payload now carries `description`, `people`, `location`, `time_text`, `caption_source`, `original_file_name`, and `metadata_available` while preserving legacy fields.
- Fixed the photo keyword test stubs so they do not poison later `langchain_core` imports in combined test runs.
- Focused regression: `17 passed in 1.29s`.
- Full regression: `115 passed in 11.24s` with `conda activate agent`.

### Current next step

- Target19: background-agent action expansion with explicit action contracts, idempotency, consent/approval requirements, and visibility boundaries.


### Target 19 delivered

- Planner actions now have explicit action contracts: target channel, consent/approval requirements, visibility scope, idempotency key, and optional action session id.
- Background planner persists those contracts and creates durable frontend action sessions for scheduled music/story actions.
- Action session creation is idempotent by `idempotency_key`.
- Focused regression: `12 passed in 2.34s`.
- Full regression after Target19: `118 passed in 8.42s`.

### Target 20 delivered

- Health-condition writes from `MedicalAgent` now use injected `UserContextService` when available.
- `record_health_complaint` no longer uses `RAGHelper`; it writes through `UserContextService` and returns structured JSON.
- Legacy direct construction remains compatible through fallback behavior.
- Focused regression: `22 passed in 1.43s`.
- Final full regression: `120 passed in 8.90s` with `conda activate agent`.

### Current next step

- The Post-Target16 plan is finished. Create a new next-stage plan before making additional code changes.
