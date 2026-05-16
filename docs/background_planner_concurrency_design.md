# 后台 ReAct Planner 与短间隔输入冲突处理设计

更新时间：2026-05-16

## 1. 源码复核结论

当前源代码里没有独立的“后台 ReAct 架构 Agent”。

已经存在的相关能力：

- `EmotionalConnectionAgent` 使用 LangGraph `StateGraph`、`ToolNode` 和 `astream_events`，这是当前轮对话内的工具调用循环，接近 ReAct，但它服务于 `/api/chat` 的实时流式回复，不是后台计划器。
- `AntiFraudAgent` 使用 LangGraph，但流程是 `analyze_fraud -> generate_intervention` 的固定顺序，不是 ReAct 工具循环；文件中还有重复 `arun` 定义，后续需要清理。
- `ProactiveAgent` 是空闲检测 + 规则模板生成，不是 ReAct Planner。
- `SystemOrchestrator` 当前只用 `asyncio.create_task` 并发预加载共享上下文和视觉接口；`state_lock` 只保护 `last_system_state`，没有 per-user 后台任务队列、取消、版本号或 compare-and-swap 提交机制。

因此，文档里提到的 `BackgroundPlanner` 目前仍是目标设计，不是已实现代码。

## 2. Planner 的职责边界

后台 Planner 不应该抢占实时回复链路。

实时链路负责：

- 快速风险硬规则判断。
- 返回老人端自然语言回复。
- 输出必要 SSE 事件，例如 `risk`、`risk_detail`、`music_payload`、`sos`。
- crisis 硬规则命中时立即写入必要的 family/community alert。

后台 Planner 负责：

- 汇总本轮输入、风险评估、工具结果、历史趋势、家庭建议话题、社区活动和音乐库标签。
- 生成下一轮干预目标，例如继续情绪接纳、转入呼吸引导、安排音乐、提醒前端读悄悄话、降低刺激、触发社区 SOS。
- 更新 `CarePlan`。
- 写入可延迟执行的队列，例如 quiet message、社区活动消费建议、音乐/故事候选。
- 记录 planner job 生命周期，供 `GET /api/planner/status` 查询。

Planner 的输出只能通过结构化结果提交到 `CarePlan` 和队列，不能把内部 Thought 暴露给前端，也不能直接向老人端补发长回复。

## 3. 短间隔输入的冲突原则

如果两次用户输入间隔很短，上一轮后台 Planner 还没跑完，处理原则是：

1. 实时回复永远优先，不等待上一轮 Planner。
2. 每个老人同一时间最多允许一个后台 Planner 处于有效运行态。
3. 新输入到达后，旧 Planner 立即标记为 stale 或 cancel_requested。
4. 如果新输入是 `high` 或 `crisis`，旧任务应尽快取消，并立即启动高优先级 Planner。
5. 如果新输入是 `safe` 或 `low`，可以短暂 debounce，把连续输入合并到最新 turn 后再跑。
6. 旧 Planner 即使最终返回，也必须经过版本校验；只要不是基于最新 `turn_id` 或最新 `care_plan_version`，结果必须丢弃，不能覆盖新计划。

## 4. 推荐任务模型

新增 per-user 任务状态：

```python
planner_tasks: dict[str, asyncio.Task]
planner_latest_turn: dict[str, str]
planner_locks: dict[str, asyncio.Lock]
planner_pending_jobs: dict[str, PlannerJob]
```

新增 PlannerJob 字段：

```json
{
  "job_id": "planner_001",
  "elder_user_id": "elder_001",
  "base_turn_id": "turn_101",
  "base_care_plan_version": 12,
  "priority": "low | medium | high | crisis",
  "status": "queued | running | cancel_requested | stale_discarded | completed | failed",
  "created_at": "2026-05-16T10:20:00+08:00",
  "started_at": null,
  "finished_at": null,
  "latency_ms": null,
  "stale_reason": null
}
```

`CarePlan` 需要增加：

```json
{
  "version": 12,
  "source_turn_id": "turn_101",
  "updated_by": "assessment | planner | action_callback",
  "expires_after_turns": 2
}
```

## 5. 调度伪代码

```python
async def schedule_background_planner(elder_user_id: str, turn_id: str, priority: str):
    old_task = planner_tasks.get(elder_user_id)

    if old_task and not old_task.done():
        mark_cancel_requested(elder_user_id, old_task)
        if priority in {"high", "crisis"}:
            old_task.cancel()

    planner_latest_turn[elder_user_id] = turn_id

    job = PlannerJob(
        elder_user_id=elder_user_id,
        base_turn_id=turn_id,
        base_care_plan_version=care_plan_service.current_version(elder_user_id),
        priority=priority,
        status="queued",
    )
    planner_pending_jobs[elder_user_id] = job

    planner_tasks[elder_user_id] = asyncio.create_task(
        run_latest_planner_job(elder_user_id)
    )
```

```python
async def run_latest_planner_job(elder_user_id: str):
    async with planner_locks[elder_user_id]:
        job = planner_pending_jobs.pop(elder_user_id)

        if job.priority in {"safe", "low"}:
            await asyncio.sleep(0.3)  # debounce，避免连续短句导致重复规划

        if planner_latest_turn[elder_user_id] != job.base_turn_id:
            mark_stale(job, "newer_turn_arrived_before_start")
            return

        result = await planner_agent.arun(job)

        committed = care_plan_service.compare_and_swap(
            elder_user_id=elder_user_id,
            expected_version=job.base_care_plan_version,
            patch=result.care_plan_patch,
            source_turn_id=job.base_turn_id,
        )

        if not committed:
            mark_stale(job, "care_plan_version_changed")
            return

        relay_message_service.append_actions(result.queued_actions)
        mark_completed(job)
```

注意：LLM 请求或工具调用不一定能被 `task.cancel()` 立刻打断，所以版本校验是硬兜底，不能只依赖取消。

## 6. 提交规则

Planner 结果提交时必须满足：

- `base_turn_id == latest_turn_id`，或者结果是更高优先级的 `crisis` 保护性补丁。
- `base_care_plan_version == current_care_plan_version`。
- 输出通过 `SafetyPolicy`。
- 不包含医疗建议、诊断命名、内部 Thought。
- 不覆盖用户刚刚产生的新风险评估。

推荐实现 `compare_and_swap`：

```python
def compare_and_swap(elder_user_id, expected_version, patch, source_turn_id) -> bool:
    current = load_care_plan(elder_user_id)
    if current.version != expected_version:
        return False
    new_plan = current.apply_patch(patch)
    new_plan.version += 1
    new_plan.source_turn_id = source_turn_id
    save_care_plan(elder_user_id, new_plan)
    return True
```

## 7. 不同风险等级的冲突策略

| 新输入风险 | 上一轮 Planner 未完成时的处理 |
| --- | --- |
| `crisis` | 立即取消或标记旧任务 stale；同步写 family/community alert；启动 crisis Planner；旧结果禁止覆盖 |
| `high` | 取消旧低优先级任务；启动 high Planner；旧结果必须版本校验 |
| `medium` | 标记旧任务 cancel_requested；新任务排队或替换 pending job；以最新 turn 为准 |
| `low` | debounce 300-800ms，合并连续输入；旧任务返回后若版本不一致则丢弃 |
| `safe` | 可以不启动 LLM Planner，只用规则更新轻量 CarePlan；有 pending job 时合并到最新上下文 |

## 8. 数据落盘

建议新增：

```text
data/users/{elder_user_id}/care_plan.json
data/users/{elder_user_id}/planner_jobs.jsonl
data/users/{elder_user_id}/planner_status.json
data/users/{elder_user_id}/mental_assessments.jsonl
```

`planner_jobs.jsonl` 记录完整生命周期，便于排查“为什么下一轮目标改变了”。

`planner_status.json` 只保存最新状态，供前端或管理端快速查询：

```json
{
  "elder_user_id": "elder_001",
  "status": "idle | running | cancel_requested | stale_discarded | failed",
  "latest_turn_id": "turn_102",
  "running_job_id": null,
  "last_completed_job_id": "planner_099",
  "last_error": null,
  "updated_at": "2026-05-16T10:20:01+08:00"
}
```

## 9. 增量实现顺序

1. 先实现 `CarePlanService` 的版本号和 compare-and-swap。
2. 再实现规则版 `BackgroundPlannerService`，不接 LLM，只根据 assessment 和 plan 生成下一步目标。
3. 接入 `SystemOrchestrator`：每轮回复结束后 fire-and-forget 调度 Planner，但不阻塞 SSE。
4. 新增 `GET /api/planner/status`。
5. 加入 per-user task 管理、debounce、stale discard。
6. 最后把 Planner 内核替换为 ReAct LLM，并保留同样的提交协议。

## 10. 检查结果预测

新增测试建议：

```powershell
python -m py_compile src\services\care_plan_service.py src\services\background_planner_service.py
python -m pytest tests\test_background_planner_concurrency.py -q
```

预期结果：

- 连续触发 `turn_1`、`turn_2`，`turn_1` 的 Planner 结果不会覆盖 `turn_2`。
- `turn_1=low` 正在运行时，`turn_2=crisis` 会立即让 `turn_1` stale，并提交 crisis CarePlan。
- 旧 Planner 晚返回时，`planner_jobs.jsonl` 记录 `stale_discarded`。
- `GET /api/planner/status` 能看到最新 `latest_turn_id` 和最近一次完成/丢弃状态。
- `/api/chat` 首 token 不等待 Planner 完成。

