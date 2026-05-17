# 增量更新计划：实时问答、后台 Agent 与定时事件

更新时间：2026-05-16

本文基于重新遍历源码后的结论重排实施顺序。目标是同时支持三条链路：

- 前台快速问答：`/api/chat` 必须尽快返回首 token，不等待后台长任务。
- 后台 Agent 信息处理：风险评估、CarePlan、ReAct Planner、子女/社区/音乐/活动队列异步运行。
- 定时事件：用药提醒、过时提醒、社区活动过期、动作完成回调都必须可追踪、可确认、可落盘。

## 0. 源码复核结论

当前真实能力：

- `src/server.py` 只有 `/api/chat`、`/api/profile`、`/api/proactive_check`、`/api/system_status`、reset 和 health 等基础接口。
- `/api/chat` 已是 SSE；只有 `EmotionalConnectionAgent` 是真正流式，其他 Agent 由 Orchestrator 切块模拟流式。
- `RouterAgent.route_sync()` 是关键词规则路由，不理解“焦虑导致头疼”等上下文语义。
- `EmotionalConnectionAgent` 有 LangGraph 工具循环，但它是当前轮实时回复，不是后台 Planner。
- 当前没有独立后台 ReAct Planner，没有 per-user 任务取消、stale 丢弃、CarePlan 版本提交。
- `RAGHelper` 同时负责 RAG、画像、聊天历史、情绪日志、主动关怀状态，职责过重。
- `MedicalAgent.check_medication_reminder()` 只是未接入的小时级示例，不能满足用药窗口、过时提醒、确认和遵医嘱剂量。
- `data/users/{elder}` 目录虽然存在，但当前代码主路径仍使用全局 `data/chat_history.json`、`data/user_profile.json` 等文件。

本轮验证：

```powershell
python -m py_compile main.py src\config.py src\orchestrator.py src\server.py src\agents\router_agent.py src\agents\emotional_agent.py src\agents\mental_health_agent.py src\agents\medical_agent.py src\agents\interest_agent.py src\agents\daily_life_agent.py src\agents\antifraud_agent.py src\agents\proactive_agent.py src\tools\professional_skills.py src\utils\rag_helper.py
```

预期：当前源码语法检查通过。

## 1. 总体架构目标

```text
前台实时链路 Fast Path
POST /api/chat
  -> turn_id/user_id
  -> 轻量上下文读取
  -> Python 风险硬规则与快速评分
  -> SafetyPolicy 输入/输出约束
  -> 快速路由或 CarePlan 引导路由
  -> SSE token/action/risk_detail/music_payload/sos
  -> 保存本轮对话
  -> fire-and-forget 调度后台任务

后台处理链路 Background Path
  -> LLM 复核风险证据
  -> CarePlanService 更新下一轮目标
  -> BackgroundPlannerService 规划下一步干预
  -> RelayMessageService 写入子女/社区/老人端队列
  -> FamilyPolicy/Community/Music/TimedEvent 队列消费

定时事件链路 Timed Event Path
  -> MedicationPlan/TimedEvent 落盘
  -> 扫描 due/overdue/expired
  -> 通过 proactive_check 或 timed_events 接口返回提醒
  -> 用户确认 taken/snooze/skip/not_sure
  -> 记录结果，必要时通知子女
```

## 2. 执行原则

- 每个 Target 独立可测，验证通过再进下一步。
- 新业务文件读写走 `DataStore`，不继续堆到 `RAGHelper`。
- `SafetyPolicy` 在所有 Agent 输出后统一兜底。
- `crisis` 硬规则不等待 LLM，不允许被 LLM 降级。
- 定时用药只读取已记录的医嘱/照护者录入信息，不生成新剂量、不建议增减药、不建议补服。
- 第一版后台 Planner 可以先规则实现，接口和版本提交协议稳定后再换 ReAct LLM。

## Target 0：基线与防回归

目标：

- 建立当前代码基线。
- 确认可运行测试和依赖缺口。

修改范围：

- 可新增 `docs/test_baseline.md`，不改业务代码。

检查命令：

```powershell
python -m py_compile main.py src\config.py src\orchestrator.py src\server.py src\agents\router_agent.py src\agents\emotional_agent.py src\agents\mental_health_agent.py src\agents\medical_agent.py src\agents\interest_agent.py src\agents\daily_life_agent.py src\agents\antifraud_agent.py src\agents\proactive_agent.py src\tools\professional_skills.py src\utils\rag_helper.py
python -m pytest tests\test_photo_keyword_normalization.py -q
python -m pytest tests\test_agent_resilience_unittest.py tests\test_music_intent.py -q
```

预期结果：

- `py_compile` 通过。
- 如果 pytest 因 `langchain_openai` 缺失失败，记录为环境依赖问题。

本轮实际结果：

- `py_compile` 已通过。
- `tests/test_photo_keyword_normalization.py` 已通过。
- `tests/test_agent_resilience_unittest.py` 与 `tests/test_music_intent.py` 在 collection 阶段失败，原因是当前 Python 环境缺少 `langchain_openai`，不是本轮文档调整导致的业务回归。

失败信号：

- 出现语法错误。
- 已有照片关键词、音乐意图、Orchestrator helper 测试回归。

## Target 1：核心 Schema 与 DataStore

目标：

- 先补统一数据模型和轻量落盘服务。
- 为多用户、风险评估、队列、定时事件、动作回调提供基础。

新增文件：

- `src/services/data_store.py`
- `src/schemas/mental_health.py`
- `src/schemas/relay.py`
- `src/schemas/actions.py`
- `src/schemas/timed_events.py`
- `src/schemas/family.py`
- `src/schemas/community.py`
- `tests/test_data_store.py`

核心模型：

- `MentalRiskAssessment`
- `CarePlan`
- `RelayMessage`
- `ActionSession`
- `TimedEvent`
- `MedicationPlan`
- `MedicationDoseEvent`
- `FamilyPolicy`
- `CommunityActivity`

建议目录：

```text
data/
  users/{elder_user_id}/
    profile.json
    chat_history.json
    emotion_log.json
    agent_status.json
    care_plan.json
    mental_assessments.jsonl
    planner_jobs.jsonl
    timed_events.jsonl
    medication_plans.json
    medication_dose_events.jsonl
    action_sessions.jsonl
    family/{child_user_id}/
      family_chat_history.json
      family_chat_memory.jsonl
  relay_messages.jsonl
  family_alerts.jsonl
  community_alerts.jsonl
  community_activities.jsonl
  community_announcements.jsonl
  music_library.json
```

检查命令：

```powershell
python -m py_compile src\services\data_store.py src\schemas\mental_health.py src\schemas\relay.py src\schemas\actions.py src\schemas\timed_events.py src\schemas\family.py src\schemas\community.py
python -m pytest tests\test_data_store.py -q
```

预期结果：

- JSON/JSONL 读写、append、自动建目录、FileLock 正常。
- 测试只写临时目录，不污染真实 `data/`。

失败信号：

- JSONL 顺序错乱。
- 并发 append 损坏文件。
- 路径未按 `elder_user_id` 隔离。

## Target 2：Profile/UserContext 多用户兼容层

目标：

- 在不大改 `RAGHelper` 的前提下，先为新服务提供 user_id 命名空间。
- 保持旧接口兼容。

新增/修改：

- `src/services/profile_service.py`
- `src/services/user_context_service.py`
- `src/server.py`
- `src/orchestrator.py`
- `tests/test_user_context_service.py`

接口兼容：

```text
GET /api/profile
GET /api/profile?user_id=elder_001
POST /api/profile {"user_id":"elder_001", ...fields}
GET /api/proactive_check
GET /api/proactive_check?user_id=elder_001
POST /api/chat {"message":"...", "user_id":"elder_001"}
```

预期结果：

- 不传 `user_id` 仍走旧全局文件。
- 传 `user_id` 后，新功能读写 `data/users/{user_id}`。
- `context_snapshot.user_id` 正确。

失败信号：

- 旧前端调用报错。
- `user_id` 被误写入画像普通字段。
- 多老人数据串写。

## Target 3：SafetyPolicy 与输出后处理

目标：

- 集中约束医疗、心理、危机、安全表述。
- 先用纯 Python 规则，不依赖 LLM。

新增：

- `src/policies/safety_policy.py`
- `tests/test_safety_policy.py`

硬规则：

- 禁止诊断命名：不对老人说“您是抑郁症/双相障碍/焦虑症”。
- 禁止医疗建议：不说“去医院看看/看医生/吃某药/增减药”。
- 用药只允许“按已记录医嘱/照护者录入提醒”。
- 危机回复使用短句稳定、安全感、联系守护者，不长篇说理。
- 所有 Agent 输出进入前端前都经过 `SafetyPolicy.sanitize_response()`。

检查命令：

```powershell
python -m py_compile src\policies\safety_policy.py
python -m pytest tests\test_safety_policy.py -q
```

预期结果：

- 医疗建议、诊断命名被拦截或替换。
- 用药提醒可保留“按记录/医嘱”表达。

失败信号：

- 老人端仍出现诊断名或就医建议。
- 用药提醒变成药理解释或剂量建议。

## Target 4：AssessmentService 快速风险评估

目标：

- 实现硬规则 + Python 加权评分 + 结构化风险输出。
- 支持前台快速链路，不等待 LLM。

新增：

- `src/services/assessment_service.py`
- `tests/test_assessment_service.py`

核心能力：

- `assess_text(text, context)` 目标 < 50ms。
- “活着没意思”“不想活了”直接 `crisis`。
- 焦虑导致头疼识别为心理优先，不路由到用药建议。
- 输出 `risk_tier`、`primary_state`、`confidence`、`evidence`、`next_goal`、分端措辞。

检查命令：

```powershell
python -m py_compile src\services\assessment_service.py
python -m pytest tests\test_assessment_service.py -q
```

预期结果：

- `crisis` 不可被保护因素降级。
- 社区可见内容只含 crisis 摘要和建议，不含老人原话。
- 子女可见内容可含老人原话、摘要和建议。

失败信号：

- `crisis` 被降级。
- 焦虑头疼被当成普通医疗用药。
- 社区端泄露老人原话。

## Target 5：TimedEvent 与用药提醒服务

目标：

- 把用药提醒从 `MedicalAgent.check_medication_reminder()` 抽成独立服务。
- 支持时间窗口、过时提醒、确认状态、剂量信息按医嘱读取。

新增：

- `src/services/timed_event_service.py`
- `src/services/medication_reminder_service.py`
- `tests/test_medication_reminder_service.py`
- `docs/timed_event_and_medication_reminder_design.md`

核心数据：

```json
{
  "medication_id": "med_001",
  "elder_user_id": "elder_001",
  "name": "药名",
  "dosage_text": "一次1片",
  "instruction_text": "饭后服用",
  "source": "caregiver_prescription_record",
  "schedule": [{"time": "08:00", "label": "早餐后"}],
  "window_before_minutes": 0,
  "window_after_minutes": 30,
  "overdue_after_minutes": 30,
  "expire_after_minutes": 180,
  "status": "active"
}
```

提醒策略：

- 到点：提醒“按记录/医嘱，到了服药时间”，读出药名、剂量、备注。
- 过时：提醒“刚才那次时间已经过了一会儿，我担心您忙忘了，要不要确认一下有没有按记录吃过？”
- 不确定：不建议补服、不建议加量，提示“我先记为未确认，也可以帮您提醒家人看一下记录”。
- 确认：记录 `taken/snoozed/skipped/not_sure/missed`。

检查命令：

```powershell
python -m py_compile src\services\timed_event_service.py src\services\medication_reminder_service.py src\schemas\timed_events.py
python -m pytest tests\test_medication_reminder_service.py -q
```

预期结果：

- 08:00 计划在 08:00-08:30 返回 due。
- 08:31-11:00 返回 overdue。
- 超过 expire 后标记 expired 或 missed，不再无限重复。
- 提醒文案包含已记录剂量，但不生成新剂量。

失败信号：

- 缺剂量时 Agent 自行编剂量。
- 过时后建议“补吃/加量”。
- 同一次药多次重复生成未去重事件。

## Target 6：定时事件接口与 proactive_check 合流

目标：

- 前端可以拿到用药提醒、过时提醒、社区活动到期提醒等定时事件。
- 第一版不强依赖常驻后台线程，先通过轮询接口稳定交付。

新增/修改：

- `src/server.py`
- `src/services/timed_event_service.py`
- `tests/test_timed_event_api.py`

接口：

```text
GET /api/timed_events/due?elder_user_id=elder_001
POST /api/timed_events/{event_id}/ack
GET /api/medication/plans?elder_user_id=elder_001
POST /api/medication/plans
PATCH /api/medication/plans/{medication_id}
GET /api/proactive_check?user_id=elder_001
```

合流规则：

- `proactive_check` 先查 timed event，再查普通主动关怀。
- 用药 due/overdue 优先级高于普通闲聊式主动关怀。
- crisis 当前轮仍高于所有定时事件。

检查命令：

```powershell
python -m py_compile src\server.py src\services\timed_event_service.py
python -m pytest tests\test_timed_event_api.py -q
```

预期结果：

- 前端轮询能拿到 due/overdue 事件。
- `ack=taken` 后同一事件不再提醒。
- `ack=snooze` 后按 snooze 时间再提醒。

失败信号：

- 普通主动关怀压过用药提醒。
- 已确认事件重复提醒。
- 过期事件仍不断返回。

## Target 7：RelayMessageService 与危机联动队列

目标：

- family alert、community alert、quiet message、elder pending message 全部落盘。
- crisis 默认通知社区管理员。

新增：

- `src/services/relay_message_service.py`
- `tests/test_relay_message_service.py`

检查命令：

```powershell
python -m py_compile src\services\relay_message_service.py
python -m pytest tests\test_relay_message_service.py -q
```

预期结果：

- crisis 生成 family alert + community alert。
- family alert 可含老人原话、摘要和建议。
- community alert 不含老人原话，只含原因摘要和处理建议。
- quiet message 有 `actor_role` + `direction`。

失败信号：

- 角色可见性混乱。
- community alert 泄露老人原话。
- 消息状态无法从 pending 更新。

## Target 8：Orchestrator 接入 risk_detail 与后台调度入口

目标：

- `/api/chat` 开头做快速风险评估。
- SSE 输出兼容旧 `risk`，新增 `risk_detail`。
- 回复结束后调度后台任务，不阻塞首 token。

修改：

- `src/orchestrator.py`
- `src/server.py`
- `tests/test_orchestrator_fast_path.py`

检查命令：

```powershell
python -m py_compile src\orchestrator.py src\server.py
python -m pytest tests\test_orchestrator_fast_path.py -q
```

预期结果：

- 普通输入仍正常流式返回。
- crisis 在 Agent 运行前已有 assessment 和 alerts。
- `risk_detail` 包含 `assessment_id`、`tier`、`next_goal`。
- 后台任务异常不影响本轮 SSE。

失败信号：

- `/api/chat` 首 token 因后台任务明显变慢。
- crisis 仍只走普通聊天。
- `risk_detail` 与 `risk` 不一致。

## Target 9：Router 与 ContextGuard 语义降噪

目标：

- 修复规则路由误判。
- 避免多轮上下文脏数据导致跑偏。

新增/修改：

- `src/services/context_guard.py`
- `src/agents/router_agent.py`
- `tests/test_router_context_guard.py`

规则示例：

- “我一紧张就头疼” -> 心理/焦虑优先，不进用药。
- “到了吃药时间了吗” -> 用药计划查询/定时事件。
- “头疼得厉害，还喘不上气” -> 身体紧急风险联动，但仍不提供医疗建议。
- “我不想活了” -> crisis，不进普通心理聊天。

检查命令：

```powershell
python -m py_compile src\services\context_guard.py src\agents\router_agent.py
python -m pytest tests\test_router_context_guard.py -q
```

预期结果：

- 上下文只保留最近、安全、相关片段。
- 系统主动关怀文本不会被当作老人原话。
- 规则路由不再把焦虑身体化表达直接导向用药建议。

失败信号：

- 系统上轮提示污染本轮意图。
- 心理危机被音乐/闲聊/医疗问答覆盖。

## Target 10：CarePlanService 与后台 Planner 并发控制

目标：

- 先实现规则版 `BackgroundPlannerService`。
- 解决短间隔输入时旧 Planner 覆盖新计划的问题。

新增：

- `src/services/care_plan_service.py`
- `src/services/background_planner_service.py`
- `tests/test_background_planner_concurrency.py`

核心机制：

- per-user 单飞任务。
- 新 turn 到达时旧任务 `cancel_requested` 或 `stale_discarded`。
- `CarePlan.version + source_turn_id` compare-and-swap。
- `crisis/high` 抢占低优先级 Planner。
- `safe/low` debounce 300-800ms 合并短句。

检查命令：

```powershell
python -m py_compile src\services\care_plan_service.py src\services\background_planner_service.py
python -m pytest tests\test_background_planner_concurrency.py -q
```

预期结果：

- `turn_1` 晚返回不能覆盖 `turn_2`。
- crisis 立即提交，不等待旧 Planner。
- `planner_jobs.jsonl` 记录 queued/running/stale/completed/failed。

失败信号：

- 旧计划覆盖新风险状态。
- 同一老人多个 Planner 同时有效提交。

## Target 11：LLM 复核与 ReAct Planner 接入

目标：

- 在已有规则 Planner 和提交协议稳定后，再接 LLM。
- LLM 只做语义复核、证据整理和下一步目标建议。

新增：

- `src/agents/planning_agent.py`
- `tests/test_planning_agent_contract.py`

约束：

- 不暴露 Thought。
- 不降低 crisis。
- 不覆盖 `SafetyPolicy`。
- 只输出结构化 patch 和 queued_actions。

检查命令：

```powershell
python -m py_compile src\agents\planning_agent.py
python -m pytest tests\test_planning_agent_contract.py -q
```

预期结果：

- Planner 输出稳定符合 schema。
- LLM 超时不会阻塞实时链路。
- 迟到结果版本不匹配时被丢弃。

失败信号：

- LLM 输出自由文本直接写入 CarePlan。
- 内部思考暴露给前端。

## Target 12：音乐/唱歌动作回调

目标：

- 支持唱歌前 token、`music_payload`、播放完成或打断回调。

新增/修改：

- `src/services/action_session_service.py`
- `src/server.py`
- `tests/test_action_complete.py`

接口：

```text
POST /api/action_complete
```

状态：

- `completed`
- `interrupted`
- `cancelled`
- `failed`

预期结果：

- `interrupted` 结束 action session，但不算完整干预完成。
- `completed` 可返回 `post_reply`。
- 重复回调幂等。

失败信号：

- 打断被算作完整疗程完成。
- 重复回调重复写日志。

## Target 13：FamilyPolicy 与悄悄话消费

目标：

- 子女可设置建议话题、消费次数、频率间隔、长期目标。
- 支持按钮确认和语义同意读取悄悄话。

新增：

- `src/services/family_policy_service.py`
- `tests/test_family_policy_service.py`

检查命令：

```powershell
python -m py_compile src\services\family_policy_service.py
python -m pytest tests\test_family_policy_service.py -q
```

预期结果：

- `max_consumptions` 达到后不再推荐。
- `min_interval_hours` 未到时不推荐。
- 老人拒绝后本轮不再读。
- 低风险时可在回复末尾自然嵌入悄悄话提示。

失败信号：

- 子女策略覆盖安全策略。
- 同一悄悄话反复打扰老人。

## Target 14：社区公告/活动接口

目标：

- 社区公告和社区活动分开管理。
- 活动支持 `valid_until`，过期不消费。

新增：

- `src/services/community_service.py`
- `tests/test_community_service.py`

接口：

```text
POST/GET /api/community/announcements
POST/GET /api/community/activities
GET /api/community/crisis_alerts
```

预期结果：

- Planner 只消费未过期活动。
- 社区端只看到 crisis。
- 公告和活动在 UI 上可分入口。

失败信号：

- 过期活动仍被推荐。
- 普通低风险心理状态泄露给社区端。

## Target 15：子女端 SSE Agent

目标：

- 子女可通过聊天框询问父母情况。
- 子女记忆和老人记忆隔离。

新增：

- `src/agents/family_agent.py`
- `src/services/family_context_service.py`
- `tests/test_family_context_service.py`

接口：

```text
POST /api/family/chat
GET /api/family/elder_summary
```

预期结果：

- 子女端可读 family-visible 风险摘要、CarePlan、建议。
- 不写入老人 `chat_history.json`。
- 不暴露内部 ReAct 思考。

失败信号：

- 子女对话污染老人端上下文。
- 子女端获得社区-only 或内部 Thought。

## Target 16：Agent 清理与提示词收敛

目标：

- 清理已知代码债。
- 让所有 Agent 接入统一 SafetyPolicy 和新服务。

修改：

- `src/agents/antifraud_agent.py`
- `src/agents/medical_agent.py`
- `src/agents/mental_health_agent.py`
- `src/agents/emotional_agent.py`
- `src/tools/professional_skills.py`

检查点：

- 删除重复 `AntiFraudAgent.arun`。
- `MedicalAgent` 不再承担定时用药提醒调度。
- “家庭医生助手/健康建议”提示词改成“健康关怀记录与已知医嘱提醒”，并由 SafetyPolicy 兜底。
- `ProfessionalSkills.emergency_contact` 的对外措辞分成 family/community/sos，不默认说医疗建议。

检查命令：

```powershell
python -m py_compile src\agents\antifraud_agent.py src\agents\medical_agent.py src\agents\mental_health_agent.py src\agents\emotional_agent.py src\tools\professional_skills.py src\orchestrator.py tests\test_agent_safety_convergence.py
python -m pytest tests\test_agent_safety_convergence.py tests\test_safety_policy.py tests\test_agent_resilience_unittest.py tests\test_prompt.py -q
```

预期结果：

- 所有 Agent 输出都经过统一安全后处理。
- 反诈异步入口唯一。
- 用药提醒由 TimedEventService 负责。

失败信号：

- Agent 绕过 SafetyPolicy。
- MedicalAgent 仍直接给医疗建议。


Completion record (2026-05-16):

- `AntiFraudAgent` duplicate `arun` removed.
- `MedicalAgent` no longer owns medication timer scheduling; prompt and output are converged through `SafetyPolicy`.
- `MentalHealthAgent` is now companion-style support and uses `SafetyPolicy`.
- `emotional_agent` raw streaming chunks are buffered and sanitized before token emission.
- `ProfessionalSkills.emergency_contact` now splits family/community/SOS output and desensitizes community text.
- Target 16 focused regression: `23 passed`.
- Full regression: `110 passed in 13.12s`, environment: `conda activate agent`.

## 推荐执行顺序

1. Target 0：基线与防回归。
2. Target 1：核心 Schema 与 DataStore。
3. Target 2：Profile/UserContext 多用户兼容层。
4. Target 3：SafetyPolicy。
5. Target 4：AssessmentService。
6. Target 5：TimedEvent 与用药提醒服务。
7. Target 6：定时事件接口与 proactive_check 合流。
8. Target 7：RelayMessageService。
9. Target 8：Orchestrator 快速链路接入 risk_detail 和后台调度入口。
10. Target 9：Router 与 ContextGuard。
11. Target 10：CarePlanService 与后台 Planner 并发控制。
12. Target 11：LLM 复核与 ReAct Planner。
13. Target 12：音乐/唱歌动作回调。
14. Target 13：FamilyPolicy 与悄悄话消费。
15. Target 14：社区公告/活动接口。
16. Target 15：子女端 SSE Agent。
17. Target 16：Agent 清理与提示词收敛。

这个顺序的关键点是：先把“安全、数据、时间、风险”四个基础打稳，再接后台 ReAct 和多端接口。这样前台快速问答不会被后台 Agent、定时扫描或 LLM 复核拖慢。


## Target 17: sentence-level safe emotional streaming

Status: complete.

Purpose:
- Preserve Target16 safety while improving perceived latency for `emotional_agent` output.

Implemented:
- `src/orchestrator.py` buffers raw emotional stream chunks and flushes only completed sentence segments after `SafetyPolicy` sanitization.
- Sentence boundaries: Chinese/ASCII sentence punctuation (`!`, `?`, semicolon, Chinese full-stop/exclamation/question, and newline).
- `crisis` risk tier remains fully buffered so the crisis-safe prefix is emitted once.
- Final model output is still sanitized and used as the reconciliation path.

Tests:
- `tests/test_agent_safety_convergence.py::test_emotional_stream_flushes_completed_safe_sentences`
- `tests/test_agent_safety_convergence.py::test_emotional_crisis_stream_stays_fully_buffered`

Verification:
- Focused regression: `16 passed in 1.81s`.
- Full regression: `112 passed in 10.48s`.
- Environment: `conda activate agent`.

## Target 18: photo semantic metadata and retrieval contract

Status: complete.

Purpose:
- Make album/photo retrieval capable of using descriptions/tags when they exist without pretending the current system can infer image content by itself.

Planned scope:
- Reinspect existing photo routes, tool functions, and storage shape.
- Normalize metadata fields: `description`, `tags`, `people`, `location`, `time_text`, `caption_source`.
- Keep request-time chat free of lazy vision inference; background enrichment may write captions later.
- Add deterministic fixture tests for semantic and fallback retrieval.


Execution order update:
18. Target 17: sentence-level safe emotional streaming.
19. Target 18: photo semantic metadata and retrieval contract.


Target 18 completion record:
- `search_family_photos` now uses existing semantic photo metadata for local ranking and output.
- No request-time vision inference was added.
- Focused regression: `17 passed in 1.29s`.
- Full regression: `115 passed in 11.24s`.
- Environment: `conda activate agent`.

## Target 19: background-agent action expansion

Status: recommended next target.

Purpose:
- Convert background-agent outputs into explicit action contracts with idempotency, approval/consent requirements, and elder/family/community visibility boundaries.


Target 19 completion record:
- Planner queued actions now include explicit action-contract fields.
- Background planner persists action contracts and creates idempotent frontend action sessions for scheduled music/story actions.
- Focused regression: `12 passed in 2.34s`.
- Full regression after Target19: `118 passed in 8.42s`.

Target 20 completion record:
- Medical symptom-report persistence and `record_health_complaint` now write through typed UserContext/Profile/DataStore services when available.
- `record_health_complaint` no longer imports or instantiates `RAGHelper`.
- Focused regression: `22 passed in 1.43s`.
- Final full regression: `120 passed in 8.90s`.
- The Post-Target16 plan is complete; draft a new plan before further scope.
