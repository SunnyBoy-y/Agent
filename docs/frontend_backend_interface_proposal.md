# 前后端接口真实契约（当前实现版）

更新日期：2026-05-17  
适用代码：`src/server.py` 当前 FastAPI 实现  
状态：本文件从“方案草案”收口为“当前真实 contract”。若后续接口变化，先改代码和测试，再同步本文档。

> 重要：当前大部分新接口返回 `{"status":"success","data":...}`，但历史接口 `/api/profile` 的 GET 和 `/api/proactive_check` 仍不是统一包裹。前端不能假设所有接口都同形。

---

## 0. 通用约定

### 0.1 用户身份边界

`POST /api/chat` 使用请求体顶层 `user_id` 作为硬边界：

```json
{
  "message": "hello",
  "user_id": "elder_001",
  "context": {}
}
```

如果 `context.user_id` 同时存在且与顶层 `user_id` 不一致，后端返回 400：

```json
{
  "status": "error",
  "code": 400,
  "message": "Conflicting user_id between request body and context"
}
```

前端规则：**不要在 `context` 里重复塞 `user_id`**；如果历史组件已经这样做，必须保证它与顶层一致。

### 0.2 时间格式

支持 ISO datetime，例如：

```text
2026-05-17T08:00:00+08:00
```

### 0.3 SSE 格式

SSE 均为：

```text
data: {"type":"token","data":"..."}\n\n
```

错误事件存在历史兼容差异：部分错误使用 `data` 字段，`/api/chat` 流内异常可能使用 `content` 字段。前端解析 error 时应兼容两者。

---

## 1. 老人端主聊天

### POST `/api/chat`

请求：

```json
{
  "message": "今天心里有点闷",
  "user_id": "elder_001",
  "context": {
    "turn_id": "turn_001",
    "visual_analysis": null,
    "audio_transcript": null
  }
}
```

响应：`text/event-stream`

可能事件：

| type | data | 说明 |
| --- | --- | --- |
| `log` | string | 调试/过程日志 |
| `risk_detail` | object | 每轮必发的结构化风险评估 |
| `risk` | string | legacy 风险事件，仅非 safe 时发 |
| `sos` | boolean | 危机或紧急事件触发 |
| `step` | object | router/agent 状态 |
| `token` | string | 回复文本分片 |
| `action` | string/object | 数字人动作或业务动作 |
| `music_payload` | object | 前端音乐播放指令 |
| `music` | boolean | legacy 音乐触发标记 |
| `photos` | array | 相册结果，有照片时发 |
| `photos_result` | object | 相册空结果/错误结果 |
| `expression` | string | 表情元数据 |
| `error` | string/object | 流内错误 |
| `done` | `"stop"` | 本轮结束 |

`risk_detail` 示例：

```json
{
  "type": "risk_detail",
  "data": {
    "id": "assess_xxx",
    "assessment_id": "assess_xxx",
    "tier": "safe | low | medium | high | crisis",
    "risk_tier": "safe | low | medium | high | crisis",
    "primary_state": "anxiety",
    "confidence": 0.72,
    "score": 30,
    "evidence": [],
    "raw_quotes": [],
    "next_goal": "..."
  }
}
```

`music_payload` 示例：

```json
{
  "type": "music_payload",
  "data": {
    "status": "success",
    "intent": "play_music",
    "trigger_music": true,
    "query": "月亮代表我的心",
    "source": "agent",
    "music_name": "月亮代表我的心",
    "post_reply": "这首歌先到这里。您现在心里有没有松一点？",
    "action_id": "action_xxx",
    "action_type": "music"
  }
}
```

前端收到 `music_payload` 后播放音乐，播放结束/中断后调用 `POST /api/action_complete`。

`photos` item 当前契约：

```json
{
  "url": "http://.../api/file/download/{uuid}",
  "desc": "照片描述或文件名",
  "type": "image/jpeg",
  "tags": ["孙女", "公园"],
  "description": "孙女在公园里笑着拍照",
  "people": ["孙女"],
  "location": "公园",
  "time_text": "去年春天",
  "caption_source": "family_upload",
  "original_file_name": "granddaughter.jpg",
  "metadata_available": true
}
```

相册说明：当前不会实时理解图片像素；只基于文件服务已有字段、caption、tags、people、location、time_text 等元数据检索。

---

## 2. 用户画像与系统状态

### POST `/api/profile?user_id=elder_001`

请求体为 profile 局部更新 object：

```json
{
  "name": "张阿姨",
  "preferences": ["老歌"],
  "health_condition": ["腿疼"]
}
```

响应：

```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "user_id": "elder_001",
  "updated_keys": ["name", "preferences", "health_condition"],
  "profile": {}
}
```

### GET `/api/profile?user_id=elder_001`

响应：直接返回 profile object，**不包 `status/data`**。

### GET `/api/system_status?user_id=elder_001`

响应：

```json
{
  "status": "success",
  "data": {
    "routing_decision": {
      "last_input": "...",
      "routed_agent": "mental_health_agent"
    },
    "tool_calls_analysis": [],
    "user_profile": {},
    "recent_chat_history": []
  }
}
```

---

## 3. Planner / CarePlan

### GET `/api/planner/status?elder_user_id=elder_001`

响应：

```json
{
  "status": "success",
  "data": {
    "planner": {
      "elder_user_id": "elder_001",
      "status": "idle | queued | running | cancel_requested | stale_discarded | completed | failed",
      "latest_turn_id": "turn_xxx",
      "running_job_id": null,
      "last_completed_job_id": null,
      "last_discarded_job_id": null,
      "last_error": null,
      "last_review_status": "completed | timeout | failed | skipped | pending",
      "last_used_fallback": false,
      "updated_at": "..."
    },
    "care_plan": {
      "elder_user_id": "elder_001",
      "risk_tier": "safe | low | medium | high | crisis",
      "active_domain": "...",
      "current_stage": "...",
      "next_turn_goal": "...",
      "target_agent": "mental_health_agent",
      "version": 1
    }
  }
}
```

后台 planner 的行动不会阻塞 `/api/chat`；旧任务通过 turn/version 检查防止覆盖新 CarePlan。

---

## 4. 用药计划与定时事件

### GET `/api/medication/plans`

Query：

```text
elder_user_id=elder_001&include_inactive=false
```

响应：

```json
{
  "status": "success",
  "data": []
}
```

### POST `/api/medication/plans`

请求：

```json
{
  "elder_user_id": "elder_001",
  "name": "药名",
  "dosage_text": "一次1片",
  "instruction_text": "早餐后服用",
  "source": "caregiver_prescription_record",
  "schedule": [
    {"time": "08:00", "label": "早餐后"}
  ],
  "window_before_minutes": 0,
  "window_after_minutes": 30,
  "overdue_after_minutes": 30,
  "expire_after_minutes": 180,
  "status": "active"
}
```

`medication_id` 可不传，后端会生成。

响应：

```json
{
  "status": "success",
  "data": {
    "medication_id": "med_xxx",
    "elder_user_id": "elder_001"
  }
}
```

### PATCH `/api/medication/plans/{medication_id}?elder_user_id=elder_001`

请求体为局部字段，后端 merge 已有 plan。

### GET `/api/timed_events/due`

Query：

```text
elder_user_id=elder_001&now=2026-05-17T08:00:00+08:00
```

响应：

```json
{
  "status": "success",
  "data": [
    {
      "event_id": "dose_...",
      "elder_user_id": "elder_001",
      "event_type": "medication_due | medication_overdue",
      "priority": "high",
      "status": "pending",
      "scheduled_at": "...",
      "display_text": "叮咚，到您按记录吃药的时间了...",
      "payload": {
        "medication_id": "med_xxx",
        "dose_event_id": "dose_xxx",
        "name": "药名",
        "dosage_text": "一次1片",
        "instruction_text": "早餐后服用",
        "status": "due"
      }
    }
  ]
}
```

### POST `/api/timed_events/{event_id}/ack?now=...`

请求：

```json
{
  "elder_user_id": "elder_001",
  "ack": "taken | snooze | skip | not_sure | missed",
  "snooze_minutes": 10,
  "text": "我吃过了"
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "event_id": "...",
    "ack": "taken",
    "timed_events": [],
    "dose_event": {}
  }
}
```

聊天侧说明：当前 `MedicalAgent` 的用药查询已读取 `MedicationPlan`，不会只看旧 profile `medications`。

---

## 5. 前端动作完成回调

### POST `/api/action_complete`

请求：

```json
{
  "action_id": "action_xxx",
  "elder_user_id": "elder_001",
  "action_type": "music | story | medication | community_activity | quiet_message | other",
  "status": "completed | interrupted | cancelled | failed",
  "music_name": "月亮代表我的心",
  "played_seconds": 42,
  "total_seconds": 180,
  "interrupt_reason": "user_skip | user_close | system_interrupt | unknown",
  "finished_at": "2026-05-17T10:30:00+08:00",
  "payload": {}
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "session": {
      "action_id": "action_xxx",
      "elder_user_id": "elder_001",
      "action_type": "music",
      "status": "completed",
      "completed_intervention": true
    },
    "post_reply": "这首歌先到这里。您现在心里有没有松一点？",
    "next_turn_goal": "gently check whether the music helped",
    "care_plan_patch": {},
    "completed_intervention": true,
    "idempotent_replay": false
  }
}
```

语义：

- `completed`：动作完整完成，可播 `post_reply`。
- `interrupted`：动作终止但不算干预完成，通常不返回 `post_reply`。
- 重复 terminal 回调幂等，不重复写 intervention log。

---

## 6. 家庭端策略、悄悄话与提醒

### GET `/api/family/agent_policy`

Query：

```text
elder_user_id=elder_001&child_user_id=child_001
```

响应：

```json
{"status":"success","data":{}}
```

### POST `/api/family/agent_policy`

请求：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "policy": {
    "preferred_tone": "温和、慢一点",
    "suggested_topics": [],
    "preferred_actions": [],
    "routine_goals": []
  }
}
```

### GET `/api/family/topics/available`

Query：

```text
elder_user_id=elder_001&child_user_id=child_001&now=2026-05-17T10:00:00+08:00
```

响应：

```json
{"status":"success","data":[]}
```

### POST `/api/family/topics/{topic_id}/consume`

Query：

```text
elder_user_id=elder_001&child_user_id=child_001&now=...
```

响应：

```json
{"status":"success","data":{}}
```

### POST `/api/family/messages`

当前实现：**只支持 `quiet_message`**。文档旧版提到的 `elder_note` 还没有实现。

请求：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "direction": "child_to_elder",
  "message_type": "quiet_message",
  "title": "女儿的话",
  "content": "妈，今天降温了，记得加件衣服。",
  "priority": "low | normal | high",
  "payload": {}
}
```

响应会移除正文：

```json
{
  "status": "success",
  "data": {
    "id": "msg_xxx",
    "target": "elder",
    "display_type": "quiet_message",
    "status": "pending",
    "payload": {
      "content_visible_after_consent": true
    }
  }
}
```

### GET `/api/elder/pending_messages`

Query：

```text
elder_user_id=elder_001&risk_tier=safe
```

响应：

```json
{
  "status": "success",
  "data": {
    "messages": [
      {
        "id": "msg_xxx",
        "from_display": "女儿",
        "message_type": "quiet_message",
        "prompt_text": "家人有句话想跟您说，您要不要听？",
        "status": "pending",
        "priority": "normal",
        "created_at": "..."
      }
    ]
  }
}
```

注意：当前 `risk_tier` 仍由前端传入，后续应改为后端从最新风险状态推导。

### POST `/api/elder/messages/{message_id}/consent`

请求：

```json
{
  "elder_user_id": "elder_001",
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
    "id": "msg_xxx",
    "status": "accepted",
    "content": "妈，今天降温了，记得加件衣服。",
    "message": {},
    "idempotent_replay": false
  }
}
```

拒绝时不返回正文。

### GET `/api/family/alerts`

Query：

```text
elder_user_id=elder_001&child_user_id=child_001&limit=20
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "child_user_id": "child_001",
    "alerts": []
  }
}
```

当前 `child_user_id` 主要用于响应回显，服务层未按 child 过滤。

---

## 7. 家庭端 Agent

### POST `/api/family/chat`

请求：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "message": "我妈最近状态怎么样？",
  "context": {}
}
```

响应：`text/event-stream`

事件：

| type | 说明 |
| --- | --- |
| `token` | 家庭端 Agent 文本 |
| `family_context` | 可公开给子女端的上下文摘要 |
| `done` | 结束 |
| `error` | 错误 |

家庭端记忆写入：

```text
data/users/{elder_user_id}/family/{child_user_id}/family_chat_history.json
data/users/{elder_user_id}/family/{child_user_id}/family_chat_memory.jsonl
```

不会写入老人端 `chat_history.json`。

### GET `/api/family/elder_summary`

Query：

```text
elder_user_id=elder_001&child_user_id=child_001
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "summary": {
      "risk_tier": "medium",
      "primary_state": "low_mood",
      "recent_trend": "...",
      "care_plan_stage": "...",
      "suggested_family_action": "..."
    },
    "visible_evidence": [],
    "family_alerts": [],
    "recent_interventions": [],
    "policy": {}
  }
}
```

---

## 8. 社区端

### POST `/api/community/announcements`

请求：

```json
{
  "community_id": "community_001",
  "actor_role": "community_admin",
  "title": "停水通知",
  "content": "明天上午九点到十一点检修管道。",
  "tags": ["notice", "water"],
  "valid_from": "2026-05-17T00:00:00+08:00",
  "valid_until": "2026-05-18T12:00:00+08:00",
  "priority": 5
}
```

响应：

```json
{"status":"success","data":{}}
```

### GET `/api/community/announcements`

Query：

```text
community_id=community_001&only_active=true&now=2026-05-17T10:00:00+08:00&limit=20
```

响应：

```json
{"status":"success","data":[]}
```

### POST `/api/community/activities`

请求：

```json
{
  "community_id": "community_001",
  "title": "合唱活动",
  "content": "下午一起唱老歌",
  "time_text": "今天下午三点",
  "location": "社区活动室",
  "tags": ["music", "social"],
  "valid_until": "2026-05-17T16:00:00+08:00",
  "priority": 3
}
```

`valid_until` 必填。

### GET `/api/community/activities`

Query：

```text
community_id=community_001&only_active=true&now=...&limit=20
```

响应：

```json
{"status":"success","data":[]}
```

### GET `/api/community/crisis_alerts`

Query：

```text
elder_user_id=elder_001&limit=20
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "alerts": []
  }
}
```

当前不带 `community_id`。返回内容会强制移除老人原话：

```json
{
  "raw_quotes": [],
  "payload": {
    "visibility": "community_crisis_summary",
    "raw_quote_visible": false
  }
}
```

---

## 9. 主动检查与重置

### GET `/api/proactive_check`

Query：

```text
user_id=elder_001&now=2026-05-17T08:00:00+08:00
```

有定时事件时：

```json
{
  "type": "timed_event",
  "data": {}
}
```

无事件时：

```json
{"type":"none"}
```

注意：该接口不包 `status/data`。

### POST `/api/reset_profile?user_id=elder_001`

响应：

```json
{
  "status": "success",
  "message": "User profile has been reset to default.",
  "user_id": "elder_001",
  "profile": {}
}
```

### POST `/api/reset_memory`

Query：

```text
user_id=elder_001&include_legacy_rag=false
```

响应：

```json
{
  "status": "success",
  "message": "User state has been reset.",
  "user_id": "elder_001",
  "data": {
    "user_id": "elder_001",
    "data_store": {
      "user_id": "elder_001",
      "path": "users/elder_001",
      "existed": true,
      "files_removed": 8,
      "dirs_removed": 1
    },
    "planner": {
      "elder_user_id": "elder_001",
      "cancelled_tasks": 0,
      "active_job_id": null,
      "reason": "cancelled_by_user_state_reset"
    },
    "legacy_rag": {
      "requested": false,
      "scope": "not_touched",
      "result": null
    }
  }
}
```

语义：

- 默认只重置 `DataStore` 中 `users/{user_id}` 下的当前用户状态，包括 profile、chat history、risk assessments、care plan、planner jobs/actions、relay messages、action sessions、family policy/message 子目录、medication/timed event 等用户内文件。
- 重置前会取消该用户仍在运行的后台 planner 任务，避免旧任务在 reset 后重新写回状态。
- 不会删除其他用户目录，也不会删除全局 `communities/{community_id}` 数据。
- `include_legacy_rag=true` 时才会额外调用 `RAGHelper.reset_all_memory()`；注意该 legacy RAG reset 是**全局作用域**，不是 per-user。前端不要默认打开。

---

## 10. 当前未实现/未冻结项

1. `POST /api/family/messages` 的 `elder_note` 未实现；当前只支持 `quiet_message`。
2. 社区 crisis alert 还没有 `community_id` 隔离参数。
3. `/api/elder/pending_messages` 的 `risk_tier` 仍由前端传入，后续应由后端推导。
4. 故事 `story_payload` 尚未作为独立 SSE 事件落地；后台 planner 可创建 story action session，但前端消费协议还需下一阶段冻结。

已实现但建议前端按补充文档接入：

- 相册本地库：`POST /api/photo_library/sync`、`POST /api/photo_library/import`、`GET /api/photo_library/photos`、`POST /api/photo_library/caption_pending`。
- 音乐库：`POST /api/music/library`、`GET /api/music/library`、`GET /api/music/library/match`。
- 详细字段见 `docs/frontend_photo_music_profile_proactive_integration.md`。

---

## 11. 前端最小接入顺序

建议前端按以下顺序接：

1. `/api/chat` SSE：先支持 `token`、`risk_detail`、`done`。
2. `music_payload` + `/api/action_complete`：完成音乐动作闭环。
3. `/api/medication/plans` + `/api/timed_events/due` + `/api/timed_events/{id}/ack`。
4. quiet message：`/api/family/messages`、`/api/elder/pending_messages`、`/api/elder/messages/{id}/consent`。
5. family-side：`/api/family/elder_summary`、`/api/family/chat`。
6. community：announcement/activity/crisis alerts。

验证后端时使用：

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
```

---

## 12. 前端补充接入说明

以下内容已单独整理成详细文档：

```text
docs/frontend_photo_music_profile_proactive_integration.md
```

覆盖范围：

1. 相册如何通过前端同步 manifest / 数据库文件，在 Python 本地缓存，并用 Qwen3.5-Flash 做视觉描述识别。
2. 音乐库如何让前端只提供“歌名 + 歌曲描述”，由后台 Agent 判断什么时候播放。
3. `/api/profile` GET 和 `/api/proactive_check` 为什么不是统一 `status/data` 包装，以及前端 TypeScript 应如何特殊处理。
