# 定时事件与用药提醒设计

更新时间：2026-05-16

## 1. 设计目标

定时事件不能依赖普通聊天 Agent 临时判断。它需要独立服务，支持：

- 固定时间范围触发，例如 08:00-08:30。
- 过时提醒，例如 08:30 后仍未确认，提醒老人“是不是忘记了”。
- 确认、稍后提醒、不确定、跳过等状态。
- 过期后停止无限重复提醒。
- 用药剂量只读取已记录医嘱或照护者录入信息，不由 LLM 推断。

## 2. 当前源码缺口

当前 `MedicalAgent.check_medication_reminder()` 只是示例：

- 未接入 `server.py` 或 `proactive_check`。
- 只按当前小时字符串匹配。
- 没有 `user_id`。
- 没有 due/overdue/expired 状态。
- 没有用户确认。
- 没有剂量、饭前饭后、时间窗口、漏服处理。
- 没有防止重复提醒。

因此应废弃它作为调度入口，保留 `MedicalAgent` 只做已知用药计划查询和安全关怀表达。

## 3. 数据模型

### MedicationPlan

```json
{
  "medication_id": "med_001",
  "elder_user_id": "elder_001",
  "name": "药名",
  "dosage_text": "一次1片",
  "instruction_text": "早餐后服用",
  "source": "caregiver_prescription_record",
  "schedule": [
    {
      "time": "08:00",
      "label": "早餐后"
    }
  ],
  "window_before_minutes": 0,
  "window_after_minutes": 30,
  "overdue_after_minutes": 30,
  "expire_after_minutes": 180,
  "start_date": "2026-05-16",
  "end_date": null,
  "status": "active",
  "created_at": "2026-05-16T08:00:00+08:00",
  "updated_at": "2026-05-16T08:00:00+08:00"
}
```

### MedicationDoseEvent

```json
{
  "event_id": "dose_20260516_0800_med_001",
  "elder_user_id": "elder_001",
  "medication_id": "med_001",
  "scheduled_at": "2026-05-16T08:00:00+08:00",
  "window_start": "2026-05-16T08:00:00+08:00",
  "window_end": "2026-05-16T08:30:00+08:00",
  "overdue_at": "2026-05-16T08:30:00+08:00",
  "expire_at": "2026-05-16T11:00:00+08:00",
  "status": "pending",
  "notify_count": 0,
  "last_notified_at": null,
  "ack": null
}
```

### TimedEvent

```json
{
  "event_id": "event_001",
  "elder_user_id": "elder_001",
  "event_type": "medication_due | medication_overdue | community_activity | quiet_message | action_followup",
  "priority": "low | medium | high | crisis",
  "scheduled_at": "2026-05-16T08:00:00+08:00",
  "valid_until": "2026-05-16T11:00:00+08:00",
  "status": "pending | delivered | acknowledged | snoozed | expired | cancelled",
  "payload": {}
}
```

## 4. 触发状态

| 状态 | 时间条件 | 老人端表达 |
| --- | --- | --- |
| `due` | `window_start <= now <= window_end` | “叮咚，到您按记录吃药的时间了。” |
| `overdue` | `window_end < now <= expire_at` 且未确认 | “刚才那次提醒时间过了一会儿，我担心您忙忘了，要不要确认一下？” |
| `expired` | `now > expire_at` 且未确认 | 不再反复打扰，可记为未确认，必要时给子女端摘要 |
| `acknowledged` | 老人确认已吃 | 记录结果，不再提醒 |
| `snoozed` | 老人选择稍后提醒 | 生成下一次提醒时间 |

## 5. 用药文案红线

允许：

- “按家里记录/医嘱，到了服药时间。”
- “记录里是：药名，一次多少，饭前/饭后。”
- “您要是已经吃过了，我帮您记一下。”
- “如果不确定，我先记为未确认，也可以提醒家里人帮您看一下记录。”

禁止：

- 自行生成剂量。
- 建议补服。
- 建议加量、减量、停药、换药。
- 解释药理或诊断。
- 说“去医院/看医生”。

剂量缺失时：

- 不猜测。
- 只说“我这边没有看到具体剂量，先按家里保存的医嘱或药盒标签确认一下。”

## 6. 接口建议

```text
GET /api/medication/plans?elder_user_id=elder_001
POST /api/medication/plans
PATCH /api/medication/plans/{medication_id}
GET /api/timed_events/due?elder_user_id=elder_001
POST /api/timed_events/{event_id}/ack
```

`POST /api/timed_events/{event_id}/ack`：

```json
{
  "elder_user_id": "elder_001",
  "ack": "taken | snooze | skip | not_sure",
  "snooze_minutes": 10,
  "text": "我吃过了"
}
```

## 7. 与 proactive_check 的关系

第一版建议不直接启常驻调度线程，而是让前端继续轮询：

```text
GET /api/proactive_check?user_id=elder_001
```

后端处理顺序：

1. 扫描 timed events。
2. 如果有 medication due/overdue，优先返回。
3. 没有定时事件时，再走普通主动关怀。

第二版再在 FastAPI lifespan 中启后台扫描器：

```text
startup -> asyncio.create_task(timed_event_scheduler.run())
shutdown -> cancel scheduler task
```

即使启后台扫描器，接口扫描也要保留，防止进程重启或任务取消导致漏提醒。

## 8. 与后台 Planner 的关系

TimedEventService 只判断“时间到了没有”和“是否确认”。

BackgroundPlanner 负责判断：

- 当前心理状态是否适合直接提醒。
- 是否需要把提醒语气改为更温和。
- overdue 多次未确认后是否生成子女端 quiet message。
- crisis 当前轮是否暂缓普通用药提醒。

原则：

- crisis > 用药提醒 > 普通主动关怀。
- 用药提醒不能被普通闲聊覆盖。
- 用药提醒不能改变风险等级，只作为干预/生活支持事件进入 CarePlan。

## 9. 测试建议

```powershell
python -m py_compile src\services\timed_event_service.py src\services\medication_reminder_service.py src\schemas\timed_events.py
python -m pytest tests\test_medication_reminder_service.py tests\test_timed_event_api.py -q
```

必须覆盖：

- 到点提醒。
- 过时提醒。
- 过期停止。
- 确认后不重复提醒。
- snooze 后重新提醒。
- 缺剂量不编剂量。
- 文案不出现补服、加量、停药、换药、去医院等禁用表达。
