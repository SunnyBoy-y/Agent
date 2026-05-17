# 前端接入补充说明：相册视觉缓存、音乐库、特殊响应接口

更新日期：2026-05-17  
适用阶段：前端可以开始 MVP 联调；本文记录当前已经落地的相册、音乐库、profile/proactive 特殊响应 contract。

---

## 0. 2026-05-17 当前实现状态

本轮已经落地后端接口和本地缓存服务：

- `POST /api/photo_library/sync`：同步前端相册 manifest。
- `POST /api/photo_library/import`：以前端上传的原始 JSON/SQLite/DB 文件 bytes 缓存在 `data/users/{elder_user_id}/photo_library/imports/`，随后导入索引。
- `GET /api/photo_library/photos`：按关键词查询 Python 本地相册索引。
- `POST /api/photo_library/caption_pending`：对允许视觉识别的照片调用 Qwen 兼容视觉模型生成描述缓存；测试使用 fake captioner，不依赖真实网络。
- `POST /api/music/library`：同步前端音乐库，前端只需提供歌名、描述、标签和可播放引用。
- `GET /api/music/library` / `GET /api/music/library/match`：查询音乐库和语义匹配歌曲。

Agent 侧也已接入：`search_family_photos` 会优先查本地相册库，再 fallback 到原文件服务；`play_music` 和 `/api/chat` 的 `music_payload` 会在有 `elder_user_id` 时匹配本地音乐库并带回 `music_id`、`playable_ref`、`music_description`。

---

## 1. 相册：Python 本地缓存 + Qwen 视觉理解

结论：**可以做，而且这是正确方向**。  
当前代码里的相册能力是：Agent 通过 `search_family_photos` 调外部文件服务：

```text
{FILE_SERVICE_BASE_URL}/api/file/search
{FILE_SERVICE_BASE_URL}/api/file/download/{uuid}
```

然后基于文件服务返回的已有元数据检索：

- `description`
- `caption`
- `tags`
- `people`
- `location`
- `time_text`
- `taken_at`
- `event`
- `album`
- `originalFileName`

当前后端**不会主动读取图片像素做视觉理解**。如果要让 Agent 更准确地知道“照片里是什么”，建议新增一个本地相册索引层。

### 1.1 推荐目标架构

```text
前端相册/文件服务
  -> 上传或同步相册索引 manifest / db 文件
  -> Python 后端缓存到 data/users/{elder_user_id}/photo_library/
  -> 后端按 photo_id + content_hash 判断是否需要重新识别
  -> Qwen3.5-Flash 视觉理解生成 caption / tags / people_hint / scene
  -> 写入本地 photo_index.jsonl 或 sqlite
  -> Agent 搜相册时优先查本地 photo_index
  -> 找不到再 fallback 到原文件服务 search
```

这样做的好处：

- 前端不用每次对话都传全量照片数据。
- Python 可以异步批量识别，避免聊天时阻塞。
- Qwen 视觉识别结果可缓存，避免重复消耗模型调用。
- Agent 检索时可以用“照片描述 + 人物关系 + 场景 + 时间 + 标签”综合判断。

### 1.2 前端同步照片的推荐方式

优先建议使用 JSON manifest，而不是直接让后端依赖前端数据库内部结构。

建议下一阶段新增：

```http
POST /api/photo_library/sync
Content-Type: application/json
```

请求：

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
      "content_hash": "sha256:...",
      "taken_at": "2026-05-01T15:30:00+08:00",
      "album": "family",
      "frontend_caption": "孙女在公园玩",
      "tags": ["孙女", "公园"],
      "people": ["孙女"],
      "location": "公园",
      "permission": {
        "allow_backend_cache": true,
        "allow_visual_caption": true
      }
    }
  ]
}
```

当前响应：

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

### 1.3 如果前端确实想上传“一份数据库文件”

已经实现，但需要约束格式：

```http
POST /api/photo_library/import?elder_user_id=elder_001&file_name=album.json&sync_mode=upsert&source=frontend_album
Content-Type: application/octet-stream
```

请求体是原始文件 bytes，不是 multipart。当前后端刻意不引入 `python-multipart` 依赖；前端直接把 `.json`、`.sqlite` 或 `.db` 文件内容作为 body 发送即可。

后端处理规则：

1. 数据库文件只作为输入快照缓存到：

   ```text
   data/users/{elder_user_id}/photo_library/imports/{import_id}/source.sqlite
   ```

2. 后端只读取白名单字段，抽取成统一索引，不把前端 DB 当业务库直接运行。
3. SQLite 只允许只读打开；不执行任何来自 DB 的 SQL 脚本。
4. 必须有稳定主键：`photo_id` 或 `file_uuid`。
5. 必须有图片可访问地址：`url`、`file_uuid` 或可下载路径。

最小表结构建议：

```sql
CREATE TABLE photos (
  photo_id TEXT PRIMARY KEY,
  file_uuid TEXT,
  url TEXT,
  thumbnail_url TEXT,
  original_file_name TEXT,
  mime_type TEXT,
  content_hash TEXT,
  taken_at TEXT,
  album TEXT,
  frontend_caption TEXT,
  tags_json TEXT,
  people_json TEXT,
  location TEXT,
  allow_backend_cache INTEGER,
  allow_visual_caption INTEGER,
  updated_at TEXT
);
```

### 1.4 Qwen3.5-Flash 视觉识别缓存结果

当前触发接口：

```http
POST /api/photo_library/caption_pending?elder_user_id=elder_001&limit=5&force=false
Content-Type: application/json
```

可选 body：

```json
{
  "photo_ids": ["photo_001", "photo_002"]
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
    "results": []
  }
}
```

按你当前 `.env` 的模型配置，后端可以使用 Qwen3.5-Flash 做视觉理解。缓存结果形态：

```json
{
  "photo_id": "photo_001",
  "elder_user_id": "elder_001",
  "content_hash": "sha256:...",
  "vision_model": "qwen3.5-flash",
  "caption_source": "qwen_vision",
  "description": "一名小女孩在公园草地附近玩耍，画面氛围轻松。",
  "people_hint": ["小女孩"],
  "family_labels": ["孙女"],
  "scene": "公园",
  "objects": ["草地", "树", "儿童"],
  "activity": "户外玩耍",
  "emotion_hint": "开心",
  "time_hint": "白天",
  "searchable_text": "孙女 小女孩 公园 草地 户外 开心 玩耍",
  "safety_flags": [],
  "captioned_at": "2026-05-17T10:00:00+08:00"
}
```

重要边界：

- 模型可以描述画面内容，但不要让它“凭脸认人”。  
- 如果前端或家属已经标注 `people=["孙女"]`，后端可以把该标签合并进检索字段。
- 如果没有家属标注，视觉模型最多输出“老人/小女孩/男性/女性”等非身份描述。
- 前端必须提供授权字段，例如 `allow_visual_caption=true`，避免隐私边界不清。

### 1.5 Agent 如何使用本地相册索引

下一阶段实现后，`search_family_photos` 应该改成：

```text
1. 先查 data/users/{elder_user_id}/photo_library/photo_index
2. 用 keyword 对 description/tags/people/location/time_text/searchable_text 打分
3. 返回最相关照片
4. 如果本地索引为空，再调 FILE_SERVICE_BASE_URL /api/file/search
```

返回给前端的 SSE 事件仍然沿用当前 `photos`：

```json
{
  "type": "photos",
  "data": [
    {
      "url": "http://localhost:8080/api/file/download/file_uuid_001",
      "desc": "孙女在公园草地附近玩耍，画面氛围轻松。",
      "type": "image/jpeg",
      "tags": ["孙女", "公园", "户外"],
      "description": "一名小女孩在公园草地附近玩耍，画面氛围轻松。",
      "people": ["孙女"],
      "location": "公园",
      "time_text": "2026-05-01 下午",
      "caption_source": "qwen_vision",
      "original_file_name": "granddaughter_park.jpg",
      "metadata_available": true
    }
  ]
}
```

---

## 2. 音乐：前端只给“歌名 + 歌曲描述”是可行的

结论：**这个设计成立**。  
前端不需要把完整音乐推荐逻辑交给后端，只需要把可播放音乐库同步给后端，让 Agent 知道：

- 有哪些歌；
- 每首歌适合什么场景；
- 前端播放时如何定位这首歌。

当前代码已有：

- Agent 判断是否需要放歌；
- SSE 发 `music_payload`；
- 前端播放结束后调用 `/api/action_complete`。

当前代码还没有：

- `POST /api/music/library`
- `GET /api/music/library`

因此 MVP 联调时可以先由前端本地维护音乐库，并根据 `music_payload.music_name` 或 `music_payload.query` 做模糊匹配。下一阶段建议补音乐库接口。

### 2.1 推荐音乐库同步接口

建议新增：

```http
POST /api/music/library
Content-Type: application/json
```

请求：

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
      "playable_ref": "frontend://music/song_001"
    }
  ]
}
```

后端只需要存：

- `name`
- `description`
- `aliases`
- `mood_tags`
- `scene_tags`
- `playable_ref`

Agent 可以基于描述判断什么时候放。例如：

```text
用户说：“今天心里有点闷。”
Agent 查到某首歌描述包含“安抚、低刺激、情绪低落”
=> 返回 music_payload，建议播放这首歌
```

当前也提供显式匹配接口，便于前端或调试面板确认后台会选哪首：

```http
GET /api/music/library/match?elder_user_id=elder_001&query=心里有点闷想听安抚的歌&limit=1
```

响应包在统一 `status/data` 内，`data.song` 是最佳匹配歌曲；没有匹配时 `matched=false`。

### 2.2 前端播放逻辑

当前已实现的 `music_payload` 示例：

```json
{
  "type": "music_payload",
  "data": {
    "status": "success",
    "intent": "play_music",
    "trigger_music": true,
    "query": "月亮代表我的心",
    "music_name": "月亮代表我的心",
    "music_id": "song_001",
    "playable_ref": "frontend://music/song_001",
    "music_description": "经典老歌，旋律柔和，适合安抚、怀旧、睡前或情绪低落时播放。",
    "action_id": "action_xxx",
    "action_type": "music",
    "post_reply": "这首歌先到这里。您现在心里有没有松一点？"
  }
}
```

前端处理：

1. 收到 `trigger_music=true`；
2. 用 `music_name` 优先匹配本地音乐库；
3. 如果没有精确匹配，用 `query` / `aliases` 做模糊匹配；
4. 播放；
5. 播放完成、中断、失败，都调用：

```http
POST /api/action_complete
```

请求：

```json
{
  "action_id": "action_xxx",
  "elder_user_id": "elder_001",
  "action_type": "music",
  "status": "completed",
  "music_name": "月亮代表我的心",
  "played_seconds": 210,
  "total_seconds": 210,
  "finished_at": "2026-05-17T10:30:00+08:00",
  "payload": {
    "music_id": "song_001"
  }
}
```

---

## 3. 前端必须特殊处理的两个非统一包装接口

大部分新接口返回：

```json
{
  "status": "success",
  "data": {}
}
```

但以下两个接口不是这种形态：

- `GET /api/profile`
- `GET /api/proactive_check`

前端不能对它们直接使用通用 `res.data.data` 解析。

---

## 4. `GET /api/profile` 前端应用方式

### 4.1 当前真实响应

请求：

```http
GET /api/profile?user_id=elder_001
```

成功响应直接是 profile object：

```json
{
  "name": "张阿姨",
  "health_condition": [],
  "family_members": [],
  "preferences": [],
  "medications": [],
  "dialect": "unknown"
}
```

它不是：

```json
{
  "status": "success",
  "data": {
    "name": "张阿姨"
  }
}
```

### 4.2 前端 TypeScript 示例

```ts
type ElderProfile = {
  name: string;
  health_condition: unknown[];
  family_members: unknown[];
  preferences: unknown[];
  medications: unknown[];
  dialect: string;
  [key: string]: unknown;
};

async function getProfile(userId: string): Promise<ElderProfile> {
  const res = await fetch(`/api/profile?user_id=${encodeURIComponent(userId)}`);
  const body = await res.json();

  if (!res.ok) {
    throw new Error(body?.message || body?.detail || "获取用户画像失败");
  }

  // 注意：这里直接返回 body，不取 body.data。
  return body as ElderProfile;
}
```

### 4.3 和 `POST /api/profile` 的区别

`POST /api/profile` 是包装响应：

```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "user_id": "elder_001",
  "updated_keys": ["name"],
  "profile": {
    "name": "张阿姨"
  }
}
```

所以前端不能把 GET 和 POST 混成一个解析器。

---

## 5. `GET /api/proactive_check` 前端应用方式

### 5.1 当前真实响应

请求：

```http
GET /api/proactive_check?user_id=elder_001
```

无主动事件：

```json
{
  "type": "none"
}
```

有用药/定时事件：

```json
{
  "type": "timed_event",
  "data": {
    "event_id": "event_xxx",
    "elder_user_id": "elder_001",
    "event_type": "medication_due",
    "status": "pending",
    "display_text": "该提醒服药了",
    "payload": {
      "medication_id": "med_xxx",
      "dose_event_id": "dose_xxx",
      "name": "药名"
    }
  }
}
```

有普通主动问候：

```json
{
  "type": "proactive_question",
  "data": {
    "content": "现在要不要起来活动一下？"
  }
}
```

服务未初始化时可能是：

```json
{
  "error": "System not ready"
}
```

### 5.2 前端 TypeScript 示例

```ts
type ProactiveNone = { type: "none" };
type ProactiveTimedEvent = { type: "timed_event"; data: any };
type ProactiveQuestion = { type: "proactive_question"; data: any };
type ProactiveError = { error: string };

type ProactiveResponse =
  | ProactiveNone
  | ProactiveTimedEvent
  | ProactiveQuestion
  | ProactiveError;

async function checkProactive(userId: string): Promise<ProactiveResponse> {
  const res = await fetch(`/api/proactive_check?user_id=${encodeURIComponent(userId)}`);
  const body = await res.json();

  if (!res.ok) {
    throw new Error(body?.error || body?.message || body?.detail || "主动检查失败");
  }

  // 注意：这里 body 本身就是事件，不是 { status, data }。
  return body as ProactiveResponse;
}
```

### 5.3 前端处理建议

```ts
async function pollProactive(userId: string) {
  const event = await checkProactive(userId);

  if ("error" in event) {
    // 后端未初始化或临时不可用：前端静默重试即可。
    return;
  }

  switch (event.type) {
    case "none":
      return;

    case "timed_event":
      // 展示用药/定时提醒卡片。
      // 用户点击“已服用/稍后/跳过”后调用 /api/timed_events/{event_id}/ack。
      showTimedEventCard(event.data);
      return;

    case "proactive_question":
      // 插入一条 Agent 主动问候消息，或驱动数字人开口。
      showAssistantProactiveMessage(event.data);
      return;
  }
}
```

轮询策略：

- 建议 30-60 秒一次。
- 当前正在 `/api/chat` SSE 流式回复时可以暂停轮询，避免两个 Agent 输出抢 UI。
- 前端应按 `event_id` 去重，避免同一个 timed event 多次弹窗。
- 页面隐藏或 App 后台时降低频率。

---

## 6. 通用 fetch 包装建议

可以保留一个通用解析器，但要允许特殊接口绕过。

```ts
async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(body?.message || body?.detail || body?.error || `HTTP ${res.status}`);
  }

  return body as T;
}

async function fetchWrappedData<T>(url: string, init?: RequestInit): Promise<T> {
  const body = await fetchJson<any>(url, init);

  if (body?.status === "success" && "data" in body) {
    return body.data as T;
  }

  throw new Error("接口响应不是标准 status/data 包装，请检查 endpoint contract");
}
```

使用规则：

```ts
// 标准包装接口
const plans = await fetchWrappedData<MedicationPlan[]>("/api/medication/plans?elder_user_id=elder_001");

// 特殊接口：不要用 fetchWrappedData
const profile = await getProfile("elder_001");
const proactiveEvent = await checkProactive("elder_001");
```

---

## 7. 前端接入优先级建议

第一批可以立即接：

1. `/api/chat` SSE；
2. `music_payload` 本地音乐匹配 + `/api/action_complete`；
3. `/api/profile` GET 特殊解析；
4. `/api/proactive_check` 轮询特殊解析；
5. `/api/medication/plans` + `/api/timed_events/due` + ack。

第二批建议后端补完接口后再接：

1. `/api/photo_library/sync` 或 `/api/photo_library/import`；
2. Qwen 视觉 caption 后台任务；
3. 本地 photo index 优先检索；
4. `/api/music/library`。
