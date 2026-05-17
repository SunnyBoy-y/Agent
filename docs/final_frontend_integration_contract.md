# 前端最终对接文档（当前后端完整功能版）

更新日期：2026-05-17  
对应后端：`src/server.py` 当前 FastAPI 实现  
建议前端基准地址：`http://<backend-host>:8082`

本文是给前端直接接入用的最终 contract。以本文为准，不再需要从历史方案文档里拼接口。

---

## 0. 必读结论

1. 大部分接口返回统一包装：

```ts
type ApiOk<T> = { status: 'success'; data: T };
type ApiErr = { status: 'error'; code?: number; message?: string; detail?: unknown; request_id?: string };
```

2. 有两个历史接口不是 `status/data` 包装，必须特殊处理：

- `GET /api/profile`：直接返回 profile object。
- `GET /api/proactive_check`：直接返回 SSE 事件形态 object，例如 `{type:'none'}`、`{type:'timed_event', data:{...}}`。

3. `POST /api/chat` 是老人端主入口，返回 `text/event-stream`。前端必须按 SSE 解析 `data: <json>\n\n`。

4. `user_id` 是老人端硬边界：`POST /api/chat` 顶层 `user_id` 必须作为唯一可信用户 id；不要在 `context.user_id` 里传不同值，否则后端返回 400。

5. 相册和音乐库已经可以接前端：

- 相册：前端同步 manifest 或上传 JSON/SQLite/DB 原始文件，后端缓存到 Python 本地，并可用 Qwen 视觉模型生成照片描述。
- 音乐：前端只需要同步歌名、描述、标签和 `playable_ref`；后端 Agent 决定何时播放并在 `music_payload` 里返回命中歌曲。

---

## 1. 推荐接入顺序

按这个顺序接，最少返工：

```text
1. 健康检查 /health
2. 老人 profile GET/POST
3. 相册库 sync/import/photos/caption_pending
4. 音乐库 POST/GET/match
5. 老人端 /api/chat SSE
6. music_payload -> 前端播放器 -> /api/action_complete
7. 用药计划 + timed event due/ack
8. 子女端 family policy/topic/quiet message
9. 社区端 announcement/activity/crisis alerts
10. /api/proactive_check 轮询主动事件
```

---

## 2. 前端通用封装

### 2.1 普通 JSON 接口

```ts
async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw { status: res.status, body, requestId: res.headers.get('X-Request-ID') };
  if (body && body.status === 'success' && 'data' in body) return body.data as T;
  return body as T;
}
```

### 2.2 特殊接口：profile

```ts
type ElderProfile = {
  name?: string;
  health_condition?: string[];
  family_members?: string[];
  preferences?: string[];
  medications?: unknown[];
  dialect?: string;
  [key: string]: unknown;
};

async function getProfile(userId: string): Promise<ElderProfile> {
  const res = await fetch(`${BASE_URL}/api/profile?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) throw await res.json().catch(() => ({ status: res.status }));
  return res.json(); // 注意：没有 status/data 包装
}
```

### 2.3 特殊接口：proactive_check

```ts
type ProactiveEvent =
  | { type: 'none' }
  | { type: 'timed_event'; data: TimedEventDisplay }
  | { type: 'proactive_question'; data: unknown }
  | { type: string; data?: unknown };

async function getProactiveCheck(userId: string, now?: string): Promise<ProactiveEvent> {
  const qs = new URLSearchParams({ user_id: userId });
  if (now) qs.set('now', now);
  const res = await fetch(`${BASE_URL}/api/proactive_check?${qs}`);
  if (!res.ok) throw await res.json().catch(() => ({ status: res.status }));
  return res.json(); // 注意：没有 status/data 包装
}
```

### 2.4 SSE 解析

```ts
type ChatSseEvent = { type: string; data?: unknown; content?: unknown };

async function streamChat(payload: unknown, onEvent: (event: ChatSseEvent) => void) {
  const res = await fetch(`${BASE_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) throw await res.json().catch(() => ({ status: res.status }));

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';

    for (const chunk of chunks) {
      const line = chunk.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      onEvent(JSON.parse(raw));
    }
  }
}
```

---

## 3. 全量接口清单

| 模块 | 方法 | 路径 | 返回形态 |
| --- | --- | --- | --- |
| 健康 | GET | `/health` | 普通 object |
| 聊天 | POST | `/api/chat` | SSE |
| 画像 | POST | `/api/profile` | 普通 object，非 data 包 |
| 画像 | GET | `/api/profile` | profile object，特殊 |
| 系统 | GET | `/api/system_status` | `status/data` |
| Planner | GET | `/api/planner/status` | `status/data` |
| 用药 | GET | `/api/medication/plans` | `status/data` |
| 用药 | POST | `/api/medication/plans` | `status/data` |
| 用药 | PATCH | `/api/medication/plans/{medication_id}` | `status/data` |
| 定时事件 | GET | `/api/timed_events/due` | `status/data` |
| 定时事件 | POST | `/api/timed_events/{event_id}/ack` | `status/data` |
| 动作闭环 | POST | `/api/action_complete` | `status/data` |
| 相册 | POST | `/api/photo_library/sync` | `status/data` |
| 相册 | POST | `/api/photo_library/import` | `status/data` |
| 相册 | GET | `/api/photo_library/photos` | `status/data` |
| 相册 | POST | `/api/photo_library/caption_pending` | `status/data` |
| 音乐 | POST | `/api/music/library` | `status/data` |
| 音乐 | GET | `/api/music/library` | `status/data` |
| 音乐 | GET | `/api/music/library/match` | `status/data` |
| 家庭策略 | GET | `/api/family/agent_policy` | `status/data` |
| 家庭策略 | POST | `/api/family/agent_policy` | `status/data` |
| 家庭话题 | GET | `/api/family/topics/available` | `status/data` |
| 家庭话题 | POST | `/api/family/topics/{topic_id}/consume` | `status/data` |
| 悄悄话 | POST | `/api/family/messages` | `status/data`，隐藏 content |
| 家庭提醒 | GET | `/api/family/alerts` | `status/data` |
| 子女摘要 | GET | `/api/family/elder_summary` | `status/data` |
| 子女 Agent | POST | `/api/family/chat` | SSE |
| 老人待确认 | GET | `/api/elder/pending_messages` | `status/data` |
| 老人同意/拒绝 | POST | `/api/elder/messages/{message_id}/consent` | `status/data` |
| 社区公告 | POST | `/api/community/announcements` | `status/data` |
| 社区公告 | GET | `/api/community/announcements` | `status/data` |
| 社区活动 | POST | `/api/community/activities` | `status/data` |
| 社区活动 | GET | `/api/community/activities` | `status/data` |
| 社区危机 | GET | `/api/community/crisis_alerts` | `status/data` |
| 主动检查 | GET | `/api/proactive_check` | event object，特殊 |
| 重置画像 | POST | `/api/reset_profile` | 普通 object |
| 重置记忆 | POST | `/api/reset_memory` | 普通 object |

---

## 4. 老人端主聊天 `/api/chat`

### 4.1 请求

```http
POST /api/chat
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "message": "今天心里有点闷",
  "user_id": "elder_001",
  "context": {
    "turn_id": "turn_20260517_001",
    "visual_analysis": null,
    "audio_transcript": null
  }
}
```

规则：

- `message` 不能为空。
- `user_id` 放顶层。
- `context.user_id` 不要传；如果传了，必须和顶层一致。

### 4.2 SSE 事件

前端按 `type` 分发：

| type | 用途 | 前端动作 |
| --- | --- | --- |
| `log` | 后端过程日志 | 可忽略或开发态显示 |
| `risk_detail` | 本轮心理/安全风险结构化评估 | 更新风险 UI / 子女端摘要 |
| `risk` | legacy 风险事件 | 兼容处理 |
| `sos` | 紧急求助触发 | 拉起 SOS/家属/社区流程 |
| `step` | 路由/agent 状态 | 可选显示 |
| `token` | 回复文本分片 | 拼接到聊天气泡 |
| `action` | 数字人/业务动作 | 驱动 avatar 或前端动作 |
| `music_payload` | 音乐播放指令 | 播放音乐并回调 action_complete |
| `music` | legacy 音乐标记 | 兼容处理 |
| `photos` | 相册结果 | 展示照片卡片/轮播 |
| `photos_result` | 相册空结果/错误 | 展示无结果提示 |
| `expression` | 表情/情绪元数据 | 驱动 avatar 表情 |
| `error` | 流内错误 | toast + 结束本轮 |
| `done` | 本轮结束 | 停止 loading |

### 4.3 `risk_detail` 示例

```json
{
  "type": "risk_detail",
  "data": {
    "assessment_id": "assess_xxx",
    "tier": "safe",
    "risk_tier": "safe",
    "primary_state": "lonely",
    "confidence": 0.72,
    "score": 20,
    "evidence": [],
    "raw_quotes": [],
    "next_goal": "gently continue companionship"
  }
}
```

### 4.4 `music_payload` 示例

```json
{
  "type": "music_payload",
  "data": {
    "status": "success",
    "intent": "play_music",
    "trigger_music": true,
    "query": "安抚一点的老歌",
    "source": "music_library",
    "music_id": "song_001",
    "music_name": "月亮代表我的心",
    "playable_ref": "frontend://music/song_001",
    "music_description": "经典老歌，旋律柔和，适合安抚、怀旧、睡前或情绪低落时播放。",
    "action_id": "action_xxx",
    "action_type": "music",
    "post_reply": "这首歌先到这里。您现在心里有没有松一点？"
  }
}
```

前端处理：

1. `trigger_music=true` 时播放。
2. 优先用 `playable_ref` 找本地/前端歌曲资源。
3. 没有 `playable_ref` 时用 `music_name` 或 `query` 兜底搜索。
4. 播放结束、中断、失败都调用 `/api/action_complete`。

### 4.5 `photos` 示例

```json
{
  "type": "photos",
  "data": [
    {
      "url": "http://localhost:8080/api/file/download/file_uuid_001",
      "thumbnail_url": "http://localhost:8080/api/file/thumbnail/file_uuid_001",
      "desc": "孙女在公园野餐",
      "type": "image/jpeg",
      "tags": ["孙女", "公园", "野餐"],
      "description": "孙女在公园野餐",
      "people": ["孙女"],
      "location": "公园",
      "time_text": "2026-05-01T15:30:00+08:00",
      "caption_source": "qwen_vision",
      "original_file_name": "granddaughter_park.jpg",
      "metadata_available": true,
      "photo_id": "photo_001",
      "file_uuid": "file_uuid_001"
    }
  ]
}
```

---

## 5. Profile / System / Planner

### 5.1 更新画像

```http
POST /api/profile?user_id=elder_001
Content-Type: application/json
```

```json
{
  "name": "张阿姨",
  "preferences": ["老歌", "散步"],
  "family_members": ["女儿", "孙女"],
  "health_condition": ["腿疼"]
}
```

响应不是标准 `data` 包：

```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "user_id": "elder_001",
  "updated_keys": ["name", "preferences"],
  "profile": {}
}
```

### 5.2 读取画像（特殊）

```http
GET /api/profile?user_id=elder_001
```

直接返回 profile object：

```json
{
  "name": "张阿姨",
  "health_condition": ["腿疼"],
  "family_members": ["女儿", "孙女"],
  "preferences": ["老歌", "散步"],
  "medications": [],
  "dialect": "unknown"
}
```

### 5.3 系统状态

```http
GET /api/system_status?user_id=elder_001
```

返回：`data.routing_decision`、`data.tool_calls_analysis`、`data.user_profile`、`data.recent_chat_history`。

### 5.4 Planner 状态

```http
GET /api/planner/status?elder_user_id=elder_001
```

返回：

```json
{
  "status": "success",
  "data": {
    "planner": {
      "elder_user_id": "elder_001",
      "status": "idle",
      "latest_turn_id": null,
      "running_job_id": null,
      "last_completed_job_id": null,
      "last_error": null
    },
    "care_plan": {
      "elder_user_id": "elder_001",
      "risk_tier": "safe",
      "current_stage": "",
      "next_turn_goal": ""
    }
  }
}
```

---

## 6. 相册接入

目标：前端把相册信息同步给后端，后端本地缓存并生成可搜索的照片描述，Agent 在聊天里自然找照片。

### 6.1 同步 manifest

```http
POST /api/photo_library/sync
Content-Type: application/json
```

```json
{
  "elder_user_id": "elder_001",
  "source": "frontend_album",
  "sync_mode": "upsert",
  "photos": [
    {
      "photo_id": "photo_001",
      "file_uuid": "file_uuid_001",
      "url": "http://localhost:8080/api/file/download/file_uuid_001",
      "thumbnail_url": "http://localhost:8080/api/file/thumbnail/file_uuid_001",
      "original_file_name": "granddaughter_park.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 238812,
      "content_hash": "sha256:xxx",
      "taken_at": "2026-05-01T15:30:00+08:00",
      "album": "family",
      "frontend_caption": "孙女在公园野餐",
      "tags": ["孙女", "公园", "野餐"],
      "people": ["孙女"],
      "location": "公园",
      "permission": {
        "allow_backend_cache": true,
        "allow_visual_caption": true
      },
      "metadata": {}
    }
  ]
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "received": 1,
    "upserted": 1,
    "skipped_unchanged": 0,
    "skipped_permission": 0,
    "caption_jobs_created": 1,
    "total": 1
  }
}
```

权限说明：

- `allow_backend_cache=false`：后端不会缓存该照片。
- `allow_visual_caption=false`：后端可缓存 manifest，但不会调用视觉模型。

### 6.2 上传一份相册数据库/JSON 文件

```http
POST /api/photo_library/import?elder_user_id=elder_001&file_name=album.json&sync_mode=upsert&source=frontend_album
Content-Type: application/octet-stream
```

请求体：原始文件 bytes。支持：`.json`、`.sqlite`、`.db`。  
注意：不是 multipart，不需要 `python-multipart`。

JSON 文件格式可以是数组，也可以是 `{ "photos": [...] }`。字段兼容：

- `photo_id` / `id`
- `file_uuid` / `uuid`
- `url`
- `thumbnail_url`
- `original_file_name` / `originalFileName` / `file_name`
- `frontend_caption` / `caption` / `description`
- `tags` / `tags_json`
- `people` / `people_json`
- `location`

SQLite 需要有 `photos` 表。

### 6.3 查询本地相册

```http
GET /api/photo_library/photos?elder_user_id=elder_001&query=孙女公园&limit=20
```

返回 `PhotoLibraryRecord[]`，包含 `vision` 字段。前端调试页可直接展示；聊天 UI 通常消费 SSE `photos` 即可。

### 6.4 触发 Qwen 视觉描述缓存

```http
POST /api/photo_library/caption_pending?elder_user_id=elder_001&limit=5&force=false
Content-Type: application/json
```

```json
{
  "photo_ids": ["photo_001"]
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "requested": 1,
    "captioned": 1,
    "skipped": 0,
    "failed": 0,
    "results": [
      {
        "photo_id": "photo_001",
        "status": "captioned",
        "caption": {
          "description": "孙女在公园野餐",
          "family_labels": ["孙女"],
          "scene": "公园",
          "objects": ["野餐垫"],
          "activity": "野餐",
          "caption_source": "qwen_vision",
          "vision_model": "qwen3.5-flash"
        }
      }
    ]
  }
}
```

推荐前端策略：

- 用户授权后，先 `/sync` 或 `/import`。
- Wi-Fi/充电/空闲时批量调用 `/caption_pending?limit=5`。
- 不要每次聊天实时上传图片给模型；让后端用缓存服务搜索。

---

## 7. 音乐库接入

目标：前端维护歌曲资源，后端只需要知道“这首歌是什么、适合什么场景、怎么让前端播放”。

### 7.1 同步音乐库

```http
POST /api/music/library
Content-Type: application/json
```

```json
{
  "elder_user_id": "elder_001",
  "sync_mode": "replace",
  "songs": [
    {
      "music_id": "song_001",
      "name": "月亮代表我的心",
      "artist": "邓丽君",
      "description": "经典老歌，旋律柔和，适合安抚、怀旧、睡前或情绪低落时播放。",
      "aliases": ["月亮代表我心", "邓丽君月亮"],
      "mood_tags": ["怀旧", "安抚", "温柔", "低刺激"],
      "scene_tags": ["情绪安抚", "睡前", "陪伴", "回忆"],
      "duration_seconds": 210,
      "playable_ref": "frontend://music/song_001",
      "status": "active",
      "metadata": {}
    }
  ]
}
```

响应：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "received": 1,
    "upserted": 1,
    "skipped_unchanged": 0,
    "total": 1
  }
}
```

### 7.2 读取音乐库

```http
GET /api/music/library?elder_user_id=elder_001&include_inactive=false
```

### 7.3 测试匹配

```http
GET /api/music/library/match?elder_user_id=elder_001&query=有点闷想听安抚的歌&limit=1
```

返回：

```json
{
  "status": "success",
  "data": {
    "elder_user_id": "elder_001",
    "query": "有点闷想听安抚的歌",
    "matched": true,
    "song": {
      "music_id": "song_001",
      "name": "月亮代表我的心",
      "playable_ref": "frontend://music/song_001"
    },
    "score": 120.0,
    "reason": "description,mood_tags"
  }
}
```

### 7.4 聊天里自动播放

当 Agent 判断应该播放音乐时，`/api/chat` 会发 `music_payload`。前端不要主动让 LLM 选歌；只要维护音乐库并消费 `music_payload`。

播放完成后必须调用 `/api/action_complete`，否则后端无法闭环。

---

## 8. 动作闭环 `/api/action_complete`

用于音乐、故事、用药、社区活动等前端动作完成反馈。当前最重要是音乐。

```http
POST /api/action_complete
Content-Type: application/json
```

```json
{
  "action_id": "action_xxx",
  "elder_user_id": "elder_001",
  "action_type": "music",
  "status": "completed",
  "music_name": "月亮代表我的心",
  "played_seconds": 210,
  "total_seconds": 210,
  "finished_at": "2026-05-17T16:30:00+08:00",
  "payload": {
    "music_id": "song_001",
    "playable_ref": "frontend://music/song_001"
  }
}
```

`status` 可选：`completed`、`interrupted`、`cancelled`、`failed`。

---

## 9. 用药与定时事件

### 9.1 创建用药计划

```http
POST /api/medication/plans
Content-Type: application/json
```

```json
{
  "elder_user_id": "elder_001",
  "name": "降压药",
  "dosage_text": "1片",
  "instruction_text": "饭后服用",
  "schedule": [
    { "time": "08:00", "label": "早饭后" },
    { "time": "20:00", "label": "晚饭后" }
  ],
  "window_before_minutes": 0,
  "window_after_minutes": 30,
  "overdue_after_minutes": 30,
  "expire_after_minutes": 180,
  "status": "active"
}
```

后端会自动生成 `medication_id`，也可以前端传。

### 9.2 查询/修改用药计划

```http
GET /api/medication/plans?elder_user_id=elder_001&include_inactive=false
PATCH /api/medication/plans/{medication_id}?elder_user_id=elder_001
```

PATCH body 是局部字段，例如：

```json
{ "status": "paused" }
```

### 9.3 查询到期事件

```http
GET /api/timed_events/due?elder_user_id=elder_001&now=2026-05-17T08:05:00+08:00
```

返回 `TimedEvent[]`。每个 item 会有 `display_text`，前端可直接展示。

### 9.4 确认/稍后提醒/跳过

```http
POST /api/timed_events/{event_id}/ack
Content-Type: application/json
```

```json
{
  "elder_user_id": "elder_001",
  "ack": "taken",
  "snooze_minutes": 10,
  "text": "已经吃了"
}
```

`ack` 可选：`taken`、`snooze`、`skip`、`not_sure`、`missed`。

---

## 10. 子女端 / 家庭功能

### 10.1 家庭 Agent 策略

```http
POST /api/family/agent_policy
```

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "policy": {
    "preferred_tone": "温和、慢一点、不要催",
    "suggested_topics": [
      {
        "id": "topic_001",
        "title": "聊孙女近况",
        "content": "可以温和提起孙女最近学习顺利。",
        "max_consumptions": 3,
        "min_interval_hours": 24,
        "tags": ["family"]
      }
    ],
    "preferred_actions": ["适当播放老歌"],
    "long_term_goals": ["鼓励晚饭后散步"]
  }
}
```

读取：

```http
GET /api/family/agent_policy?elder_user_id=elder_001&child_user_id=child_001
```

### 10.2 可用话题与消费

```http
GET /api/family/topics/available?elder_user_id=elder_001&child_user_id=child_001
POST /api/family/topics/{topic_id}/consume?elder_user_id=elder_001&child_user_id=child_001
```

前端用法：子女端展示话题消耗状态；老人端通常不直接调用。

### 10.3 悄悄话

子女创建：

```http
POST /api/family/messages
```

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "message_type": "quiet_message",
  "title": "女儿留言",
  "content": "妈，今天降温了，记得加件衣服，我晚上给您打电话。",
  "priority": "normal"
}
```

响应会隐藏 `content`，避免老人未同意时前端误播。

老人端查询待确认：

```http
GET /api/elder/pending_messages?elder_user_id=elder_001&risk_tier=safe
```

老人同意/拒绝：

```http
POST /api/elder/messages/{message_id}/consent
```

```json
{
  "elder_user_id": "elder_001",
  "consent": "accepted",
  "source": "button",
  "raw_text": "可以，你读吧"
}
```

只有 `accepted` 时响应才会返回完整 content。

### 10.4 子女摘要、提醒、子女 Agent

```http
GET /api/family/elder_summary?elder_user_id=elder_001&child_user_id=child_001
GET /api/family/alerts?elder_user_id=elder_001&child_user_id=child_001&limit=20
POST /api/family/chat
```

`/api/family/chat` 是 SSE：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "message": "我妈最近状态怎么样？",
  "context": {}
}
```

---

## 11. 社区端功能

### 11.1 公告

```http
POST /api/community/announcements
```

```json
{
  "community_id": "community_001",
  "actor_role": "community_admin",
  "title": "本周体检通知",
  "content": "周三上午社区卫生站提供免费血压检测。",
  "tags": ["health"],
  "valid_from": "2026-05-17T08:00:00+08:00",
  "valid_until": "2026-05-24T08:00:00+08:00",
  "priority": 2
}
```

查询：

```http
GET /api/community/announcements?community_id=community_001&only_active=true&limit=20
```

### 11.2 活动

```http
POST /api/community/activities
```

```json
{
  "community_id": "community_001",
  "title": "下午音乐活动",
  "content": "社区活动室有怀旧音乐会。",
  "time_text": "今天下午三点",
  "location": "社区活动室",
  "tags": ["music", "social"],
  "valid_until": "2026-05-17T16:00:00+08:00",
  "priority": 3
}
```

查询：

```http
GET /api/community/activities?community_id=community_001&only_active=true&limit=20
```

### 11.3 危机提醒

```http
GET /api/community/crisis_alerts?elder_user_id=elder_001&limit=20
```

社区端默认看摘要，不应展示老人原话；家庭端可以看更细证据。

---

## 12. 主动检查与轮询

### 12.1 主动检查（特殊响应）

```http
GET /api/proactive_check?user_id=elder_001&now=2026-05-17T08:05:00+08:00
```

可能返回：

```json
{ "type": "none" }
```

或：

```json
{
  "type": "timed_event",
  "data": {
    "event_id": "event_xxx",
    "event_type": "medication_due",
    "display_text": "该吃降压药了",
    "payload": {}
  }
}
```

推荐前端策略：

- 老人端每 30-60 秒轮询一次，或 App 回到前台时调用。
- 收到 `timed_event` 后展示卡片/语音提醒。
- 用户确认后调用 `/api/timed_events/{event_id}/ack`。

### 12.2 重置接口

```http
POST /api/reset_profile?user_id=elder_001
POST /api/reset_memory?user_id=elder_001&include_legacy_rag=false
```

注意：`include_legacy_rag=true` 是全局 legacy RAG reset，不要在前端默认打开。

---

## 13. 前端页面/模块建议

### 老人端

必须接：

- `/api/profile` GET/POST
- `/api/chat` SSE
- `/api/proactive_check`
- `/api/action_complete`
- `/api/timed_events/due` + `/ack`
- `/api/elder/pending_messages` + `/consent`
- 相册和音乐库同步模块

### 子女端

必须接：

- `/api/family/elder_summary`
- `/api/family/agent_policy`
- `/api/family/topics/available`
- `/api/family/messages`
- `/api/family/alerts`
- `/api/family/chat` SSE

### 社区端

必须接：

- `/api/community/announcements`
- `/api/community/activities`
- `/api/community/crisis_alerts`

### 调试/管理页

建议接：

- `/health`
- `/api/system_status`
- `/api/planner/status`
- `/api/photo_library/photos`
- `/api/music/library/match`

---

## 14. 接入验收清单

前端完成以下项，基本就能用上当前所有后端能力：

- [ ] 普通接口支持 `status/data`。
- [ ] `GET /api/profile` 特殊解析。
- [ ] `GET /api/proactive_check` 特殊解析。
- [ ] `/api/chat` SSE 能处理 `token`、`risk_detail`、`photos`、`music_payload`、`sos`、`done`。
- [ ] 播放音乐后调用 `/api/action_complete`。
- [ ] 相册 manifest 能同步，且可触发 `/caption_pending`。
- [ ] 音乐库能同步，`playable_ref` 能映射到前端播放器资源。
- [ ] 用药事件能展示并 ack。
- [ ] 悄悄话必须先征得老人同意再展示/朗读 content。
- [ ] 社区公告/活动能写入并展示。
- [ ] 危机提醒社区端只展示脱敏摘要。

---

## 15. 当前已知边界

- `story_payload` 还没有作为独立 SSE 事件冻结；后台 planner 可以创建 story action session，但前端故事播放协议仍属于下一阶段。
- `/api/elder/pending_messages` 的 `risk_tier` 当前由前端传入；后续可改成后端推导。
- `/api/proactive_check` 与 `/api/profile` GET 是历史特殊形态，不要强行套 `status/data`。
- 相册视觉理解是缓存型能力，不建议在每轮聊天实时跑图片识别。
