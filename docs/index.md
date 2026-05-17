# Agent 后端设计文档索引

更新时间：2026-05-16

本目录用于索引当前 `docs` 下的设计、复核、实施计划文档。阅读顺序建议按“现状 -> 目标机制 -> 接口契约 -> 风险修复 -> 增量实施”推进。

## 1. 推荐阅读顺序

1. `current_architecture_review.md`
   - 当前代码架构、已实现接口、Agent 分层、工具、记忆与测试现状。
   - 适合先了解项目已有能力和当前缺口。

2. `gap_analysis_and_evolution_plan.md`
   - 项目背景与当前实现之间的差距。
   - 包含感知、认知、干预、后台计划、红线策略等演进方向。

3. `mental_health_cbt_closure_design.md`
   - 心理健康精准识别、风险评分、LLM 复核、分级 CBT 状态机、后台 ReAct Planner。
   - 是心理健康闭环的核心机制文档。

4. `frontend_backend_interface_proposal.md`
   - 前后端接口草案。
   - 包含子女端、老人端、社区端、危机事件、风险证据、音乐库、音乐完成回调、子女 Agent SSE 等接口。

5. `event_contract_and_routing_notes.md`
   - 当前 SSE 事件字段来源和接口使用说明。
   - 解释 `expression/action/risk/photos/music/sos/proactive_question` 的生成位置。

6. `interface_data_code_review.md`
   - 文档接口与当前代码匹配度复核。
   - 明确哪些接口已实现、哪些是拟新增、数据如何落盘、代码实施顺序。

7. `risk_fix_priority_plan.md`
   - 当前代码风险修复优先级。
   - 把 `RAGHelper` 职责过重、`user_id` 缺失、医疗红线、联动队列缺失等问题拆为 P0/P1/P2。

8. `incremental_update_plan.md`
   - 增量修改目标点、检查命令和预期结果。
   - 后续实际改代码时，建议每完成一个目标点就按该文档验证；新版计划已把实时问答、后台 Agent 和定时事件拆成三条链路。

9. `gpt55_handoff.md`
   - 给下一个 GPT-5.5 接手用的交接文档。
   - 覆盖项目目标、用户规则、当前代码状态、必须阅读的上下文、测试方式和下一步 Target 11。

10. `implementation_progress_status.md`
   - 当前代码落地进度和任务中断后的恢复状态。
   - 适合继续开发前确认已完成 Target、测试命令和下一步起点。

11. `code_change_summary_and_next_steps.md`
   - 最近几轮代码变更的简明汇总。
   - 包含已完成任务、新增功能、测试方式和下一步计划。

12. `todo_discussion.md`
   - 与产品/实现相关的待确认事项。
   - 记录已确认规则和剩余开放问题。

13. `timed_event_and_medication_reminder_design.md`
   - 定时事件、用药提醒、过时提醒、确认状态和按医嘱剂量提醒的专门设计。
   - 明确用药提醒只读取已记录医嘱/照护者录入信息，不生成新剂量或医疗建议。

## 2. 文档职责表

| 文档 | 主要用途 | 状态 |
| --- | --- | --- |
| `current_architecture_review.md` | 当前架构与已实现能力 | 现状说明 |
| `gap_analysis_and_evolution_plan.md` | 缺口分析与演进路线 | 方案草案 |
| `mental_health_cbt_closure_design.md` | 心理风险评估、CBT、Planner | 核心机制设计 |
| `frontend_backend_interface_proposal.md` | 多角色接口与事件契约 | 接口草案 |
| `event_contract_and_routing_notes.md` | 当前事件字段与路由说明 | 现状 + 过渡说明 |
| `interface_data_code_review.md` | 接口、落盘、代码计划复核 | 实施前复核 |
| `risk_fix_priority_plan.md` | 当前代码风险修复优先级 | 修复计划 |
| `incremental_update_plan.md` | 增量代码修改与检查预测 | 执行计划 |
| `gpt55_handoff.md` | 下一个 GPT-5.5 接手说明 | 交接文档 |
| `implementation_progress_status.md` | 当前代码落地状态与恢复步骤 | 恢复状态 |
| `code_change_summary_and_next_steps.md` | 已完成任务、功能、测试和下一步计划 | 实施总结 |
| `todo_discussion.md` | 业务规则待确认与已确认项 | 协作 TODO |
| `timed_event_and_medication_reminder_design.md` | 定时事件与用药提醒 | 核心机制设计 |

## 3. 当前关键结论

- 当前代码真实实现的核心接口已包括 `/api/chat`、画像、系统状态、主动关怀、用药/定时事件、`/api/planner/status` 和重置接口。
- family/community/music/action_complete/mental_assessments/family_chat 等接口仍是拟新增接口。
- 新增业务落盘不应继续堆到 `RAGHelper`，应先新增轻量 `DataStore`。
- 当前 `MedicalAgent.check_medication_reminder()` 只是未接入的小时级示例，不能满足时间窗口、过时提醒、确认状态和按医嘱剂量提醒。
- 下一步最稳的代码顺序是：
  1. `DataStore` + schemas。
  2. `user_id`/UserContext 兼容层。
  3. `SafetyPolicy` + Python 风险评估。
  4. `TimedEventService` + 用药提醒。
  5. 接入 Orchestrator 输出 `risk_detail` 并调度后台任务。
  6. CarePlan/后台 Planner。
  7. LLM 复核、消息联动和多端接口。

## 4. 当前开放问题

最新开放问题以 `todo_discussion.md` 为准。当前主要剩余：

- 社区消息是否永远不展示老人原话，还是允许特殊授权。
- 音乐被打断后，前端是否能提供 `played_seconds` 和 `interrupt_reason`。
- 子女建议话题的消费记录是否需要在子女端 UI 可编辑重置。
- 是否同意按 `risk_fix_priority_plan.md` 的 P0 顺序先实施。
- 用药提醒默认窗口、过期时间和多次未确认后是否通知子女端，需要在 `timed_event_and_medication_reminder_design.md` 基础上确认。

## 5. 最新源码复核补充

- `background_planner_concurrency_design.md`
  - 复核当前源码是否已有后台 ReAct Planner。
  - 明确结论：当前没有独立后台 ReAct Planner；情感 Agent 的 LangGraph 工具循环只服务当前轮实时回复。
  - 设计两次输入间隔很短时的任务取消、stale 丢弃、版本号提交和 crisis 抢占策略。
<!-- Recovery note: Targets 1-20 are complete. docs/post_target16_next_stage_plan.md is complete. Current full regression: 120 passed in conda env agent. -->


## Current recovery pointer

- `post_target16_next_stage_plan.md` - next-stage plan after Target17; Targets 17-20 are complete; this plan is fully executed and the next step is a fresh planning document.
- `frontend_photo_music_profile_proactive_integration.md` - frontend integration notes for local photo-library caching + Qwen visual captions, simple music-library sync, and special handling for `GET /api/profile` / `GET /api/proactive_check`.

## Final frontend integration contract

- `final_frontend_integration_contract.md` - 当前后端完整前端对接文档。覆盖 `/api/chat` SSE、profile/proactive 特殊响应、相册本地缓存与 Qwen 视觉 caption、音乐库、用药/定时事件、家庭/悄悄话、社区、主动检查和重置接口。前端最终联调以此文档为主。
