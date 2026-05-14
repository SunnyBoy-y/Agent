# 待确认 TODO 与讨论点

更新时间：2026-05-14

这个文件用于和你逐项核对。建议先确认“业务规则”，再动代码。

## A. 需要你确认的业务规则

### A1. 风险等级如何定义

建议初版：

- `safe`：普通陪伴。
- `low`：轻度孤独、想念亲人、短暂低落。
- `medium`：明显焦虑/抑郁倾向，影响睡眠、吃饭、活动。
- `high`：强烈无助、无价值感、冲动风险、躁期明显失控风险。
- `crisis`：明确轻生/自伤表达，或当下无法保证安全。

已确认：

- “活着没意思”直接算 `crisis`。

待确认：

- “不想活了”是否必须触发子女消息或 SOS？建议同样直接 `crisis`，至少入子女消息队列。
- `high` 和 `crisis` 是否都要通知社区管理员，还是只有 `crisis`？
- 风险公式是否采用“硬规则 + Python 加权评分 + LLM 结构化复核”的混合方案？
- 视觉/语音情绪是否参与评分，还是只作为证据展示？

### A2. 子女消息联动的产品形态

背景中提到前端有专门接口，前端请求后拿到消息并渲染为 SOS 或悄悄话。

已确认/新增设想：

- 当前前端没有子女相关接口规范，需要从后端先提出契约草案。
- 子女端有两类能力：设置 Agent 行为规范；发送给老人“悄悄话”。
- 老人端读取悄悄话前，必须先询问老人是否愿意听，得到明确答复后再读。
- 子女端与老人端消息方向需要字段区分。

建议：

- 不只用一个身份字段，建议使用 `actor_role` + `direction` 两个字段。
- 子女行为规范只能作为照护偏好，不能覆盖 `SafetyPolicy`、老人明确拒绝和危机流程。

待确认：

- 是否接受 `actor_role` + `direction` 两字段，而不是单字段？
- 悄悄话同意由前端按钮确认，还是完全靠自然语言识别？
- 子女设置的 `avoid_topics` 如果被老人主动提到，Agent 是否仍可回应？
- 是否允许子女设置长期行为目标，例如“多提醒散步/多聊孙女近况”？

建议 payload：

```json
{
  "id": "uuid",
  "target": "family",
  "display_type": "quiet_message",
  "risk_tier": "high",
  "title": "需要关注老人情绪",
  "content": "老人刚才表达了明显无助感，建议家人稍后主动联系。",
  "created_at": "2026-05-13 21:00:00",
  "status": "pending"
}
```

### A3. 社区管理员 SOS 规则

已确认/新增设想：

- 社区管理员有两个功能：设置公告；接收 `crisis` 危机警告。
- 社区公告在谈及社区、物业、小区、活动室等语境时可被 Agent 读取。
- 社区活动由前端调用后端接口写入，也可通过后端接口读取。
- SOS/危机需要专用接口收集和辨别危机具体信息，以便给出报警级别。

待确认：

- 社区管理员只接收 `crisis` 吗？
- 身体紧急风险和心理危机是否走同一个 SOS？
- 社区消息是否允许包含用户原话，还是只包含风险摘要？
- `crisis` 默认是否通知社区，还是根据 `alarm_level` 决定？
- 报警等级是否采用 `level_1/level_2/level_3`？

### A4. 医疗红线边界

背景要求“严禁医疗建议，也不能说带您去医院看看”。

当前代码里 `MedicalAgent` 会给健康建议，`emergency_contact` high 会模拟拨打 120。

新增约束：

- 死守禁止诊断命名、禁止医疗建议、禁止“去医院看看/带您去医院”等表达。
- 后台可以记录风险倾向，但前台不对老人说“您是抑郁症/双相障碍/焦虑症”。
- 用药相关只能基于既有画像/已确认医嘱做提醒，不增减药、不解释处方。
- 医疗相关最多做用药提醒，不提供任何医疗建议。

待确认：

- 对“胸口疼、喘不上气、摔倒起不来”这类身体紧急输入，系统允许说什么？
- 是否允许说“我帮您联系家人/社区管理员”？
- 是否完全禁止 “120”“急救”等词，还是只禁止主动医疗诊断和就医建议？
- 用药提醒是否只读用户画像中的医嘱，不解释药理、不增减药？

### A5. 心理健康表述边界

建议原则：

- 不说“您得了抑郁症/双相障碍”。
- 可以说“我听起来您这阵子像是很焦虑/很低落”。
- 后台结构可以记录 `depression_tendency`，但前台不直接诊断。

待确认：

- 前端/后台报告中可以显示“疑似抑郁倾向/疑似躁期倾向”这一类标签，已倾向确认。
- 是否需要给老人端、子女端、社区端分别使用不同措辞？
- 风险证据中是否允许保存和展示老人原话？如果允许，哪些角色能看？

已确认/新增设想：

- 所有推理证据需要落盘，前端可通过接口获取并展示。
- 证据建议保存到 `data/mental_assessments.jsonl`，通过 `GET /api/mental_assessments` 读取。

### A6. 社区活动队列

已确认/新增设想：

- 社区活动由前端调用后端接口写入。
- 活动可通过后端接口读取。
- Planner 在合适时机消费活动，而不是每次都主动推荐。

待确认：

- 社区活动是否需要过期时间 `valid_until`？
- 活动是否需要 `suitable_states`，例如适合孤独、轻度焦虑、低落等？
- 社区公告和社区活动是否在前端 UI 上分成两个入口？

建议字段：

```json
{
  "id": "activity_001",
  "title": "社区合唱活动",
  "time_text": "今天下午三点",
  "location": "社区活动室",
  "tags": ["music", "social", "low_intensity"],
  "suitable_states": ["lonely", "anxiety_low", "depression_low"],
  "priority": 3,
  "valid_until": "2026-05-13 16:00:00"
}
```

### A7. 音乐与唱歌动作

已确认/新增设想：

- 前端会提供接口设置音乐库。
- 后端播放时只需要传输音乐名字。

待确认：

- 音乐库是否需要标签字段，便于 Planner 选择，例如 `calm/classic/comfort`？
- 唱歌前回复是否必须先作为普通 `token` 输出？
- 歌曲结束后前端会不会回调后端？如果不会，后端无法可靠输出结束语。

建议新增回调：

- `POST /api/action_complete`
- body 包含 `action_type=music`、`action_id`、`status=completed`
- 后端返回 `post_reply` 和下一步话题引导。

### A8. 讲故事工具

已确认：

- 讲故事由 LLM 生成。
- 暂不需要考虑并发。

待确认：

- 故事长度一般多长？
- 是否要按用户画像偏好选择故事类型，例如革命故事、家乡故事、童年回忆、民间故事？
- 是否需要专门 `story_payload` 事件，还是作为普通 `token` 输出即可？

### A9. 后台 Planner 的运行方式

已确认/新增设想：

- 后台 Planner 需要专用接口，前端可以获取 Planner 状态。
- 当前不考虑复杂并发问题。

待确认：

- `/api/chat` 返回后是否允许后台 Planner 异步继续运行？
- Planner 状态接口是否只读即可，例如 `GET /api/planner/status`？
- 期望首 token 延迟目标是多少？

## A10. 新增接口草案索引

详细接口方案见：

- `docs/frontend_backend_interface_proposal.md`

包含：

- `POST/GET /api/family/agent_policy`
- `POST /api/family/messages`
- `GET /api/elder/pending_messages`
- `POST /api/elder/messages/{message_id}/consent`
- `GET /api/family/alerts`
- `POST/GET /api/community/announcements`
- `POST/GET /api/community/activities`
- `GET /api/community/crisis_alerts`
- `POST /api/crisis/events`
- `GET /api/mental_assessments`
- `POST/GET /api/music/library`
- `GET /api/planner/status`

## B. 当前代码中的优先修复 TODO

### B1. 反诈路由当前会运行失败

位置：`src/orchestrator.py`

问题：

- `_run_specific_agent` 的 `antifraud_agent` 分支计算了 `content` 和 `risk` 后没有 return。
- 命中反诈路由时，上层会拿到 `None`，随后 `result.get(...)` 会报错。

状态：已修复。当前会返回 `content`、`action`、`risk_level`，并保留 `family_message`、`community_message` 字段供后续联动队列使用。

建议优先级：P0，已处理。

### B2. 流式括号过滤有疑似误改

位置：`src/orchestrator.py`

问题：

- 当前过滤逻辑里把 `?` 当成括号起止符。
- 应该确认是否误把中文括号 `（`、`）` 变成了 `?`。

状态：已修复为英文括号 `()` 和中文括号 `（）`。

建议优先级：P1，已处理。

### B3. 心理健康 Agent 很少被自然路由命中

位置：`src/agents/router_agent.py`

问题：

- 焦虑、抑郁、孤独等多数输入默认进入 `emotional_agent`。
- `mental_health_agent` 有心理逻辑，但大部分情况下不会被路由使用。

建议：

- 要么引入统一 AssessmentLayer。
- 要么先补心理关键词路由到 `mental_health_agent`。

建议优先级：P1。

### B6. 其他 Agent 流式返回

位置：`src/orchestrator.py`

状态：已做最小修复。

- `medical_agent`、`daily_life_agent`、`interest_agent`、`mental_health_agent`、`antifraud_agent` 仍使用现有 `arun` 生成完整结果。
- Orchestrator 将完整文本切成小段，通过连续 `token` 事件返回，前端可统一按流式消费。

后续真流式 TODO：

- 将这些 Agent 改为各自支持 `astream_run`。
- 让 LLM 边生成边输出，降低首 token 延迟。

### B4. 安全红线未统一

位置：

- `src/agents/emotional_agent.py`
- `src/agents/mental_health_agent.py`
- `src/agents/medical_agent.py`
- `src/tools/professional_skills.py`

问题：

- 不同 Agent 对医疗、心理危机、SOS 的表达规则不一致。
- 当前没有集中式 `SafetyPolicy`。

建议优先级：P1。

### B5. 测试环境缺依赖

本次执行：

```powershell
python -m pytest tests\test_agent_resilience_unittest.py tests\test_music_intent.py tests\test_photo_keyword_normalization.py -q
```

结果：

- 测试未运行。
- 原因：`ModuleNotFoundError: No module named 'langchain_openai'`。

建议：

- 确认依赖安装环境。
- 或给核心规则测试做更彻底的 mock，避免导入 LangChain 依赖。

建议优先级：P2。

## C. 下一轮建议确认顺序

建议你先回复这 6 个问题：

1. 风险评分是否采用“硬规则 + Python 加权评分 + LLM 复核”的混合方案？
2. `crisis` 是否默认通知社区管理员，还是按 `alarm_level` 决定？
3. 是否接受 `actor_role` + `direction` 两字段设计？
4. 悄悄话同意由前端按钮确认，还是完全靠语义识别？
5. 风险证据中是否允许保存和展示老人原话？哪些角色可见？
6. 音乐播放完成后，前端能否回调后端？

确认后，下一步可以先改 P0/P1，再落地 `MentalRiskAssessment` 和 `CarePlan`。
