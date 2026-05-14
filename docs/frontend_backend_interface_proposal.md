# 前后端接口与角色交互方案草案

更新时间：2026-05-14

## 1. 设计原则

本文件整理当前确认的产品设想，并给出接口方案。重点是先把边界定清楚，后续实现时再拆模块。

核心原则：

- 所有危机、安全、医疗红线由后端统一判定，不能完全交给前端或子女配置。
- 子女可以设置 Agent 行为偏好，但不能覆盖安全策略、老人意愿和系统红线。
- “悄悄话”不是直接播报，老人端必须先询问，得到明确同意后再读。
- “疑似抑郁倾向”等评估可以给前端展示，但必须以“风险倾向/观察证据”表达，不做诊断命名。
- 风险证据必须落盘，便于前端展示和后续复核。
- 社区公告/活动由前端写入后端，Agent 在合适时机读取和消费。
- 音乐库由前端维护，后端只选择并返回音乐名字。
- 讲故事由 LLM 生成，不需要前端故事库。
- 当前阶段不考虑复杂并发，多用户可先保留 `user_id` 字段以便未来扩展。

## 2. 身份与方向字段

你提出“身份识别以一个字段辨别是子女对老人还是老人对子女”。这个方向是合理的，但建议不要只用一个含混字段，推荐拆成两个字段：

- `actor_role`：谁在发起动作。
- `direction`：消息关系方向。

推荐枚举：

```json
{
  "actor_role": "elder | child | community_admin | system",
  "direction": "child_to_elder | elder_to_child | community_to_elder | system_to_family | system_to_community"
}
```

这样做的好处：

- `actor_role=child` 且 `direction=child_to_elder`：子女给老人悄悄话。
- `actor_role=elder` 且 `direction=elder_to_child`：老人给子女留言。
- `actor_role=system` 且 `direction=system_to_family`：后端根据风险自动生成的子女提醒。
- `actor_role=community_admin` 且 `direction=community_to_elder`：社区公告。

待确认：

- 前端是否愿意采用两个字段？如果必须一个字段，可以用 `message_flow`，但后续扩展会差一点。

## 3. 子女侧行为规范配置

你的想法：子女在前端设置希望 Agent 遵守的行为规范、希望 Agent 做的事情。

合理性判断：合理，但必须加优先级。子女配置属于“照护偏好”，不能覆盖老人明确拒绝、安全红线、危机流程。

建议接口：

### POST /api/family/agent_policy

子女端写入 Agent 行为偏好。

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "policy": {
    "preferred_tone": "温和、慢一点、不要催",
    "preferred_actions": ["提醒喝水", "多聊孙女近况", "适当播放老歌"],
    "avoid_topics": ["不要提过世亲人细节"],
    "routine_goals": ["晚饭后提醒散步", "睡前提醒听舒缓音乐"],
    "crisis_contact_preference": "先通知子女，再通知社区"
  }
}
```

### GET /api/family/agent_policy

后端和前端读取当前策略。

建议落盘：

- `data/family_agent_policy.json`

后端使用方式：

- `ContextGuard` 读取策略。
- `BackgroundPlanner` 在非危机场景参考策略。
- `SafetyPolicy` 优先级高于子女策略。

## 4. 子女对老人：悄悄话

你的定义：悄悄话就是子女想给父母说的话，老人端先询问要不要听，得到明确答复再读。

合理性判断：正确。建议不要在 `/api/chat` 里直接塞完整悄悄话，避免老人未同意就被前端读出。

推荐流程：

```text
子女端提交悄悄话
  -> 后端保存 pending
  -> 老人端对话或轮询获取 only metadata
  -> Agent 询问“孩子有句话想跟您说，要不要听？”
  -> 老人明确同意
  -> 后端返回完整 content
  -> 前端播报/数字人朗读
```

### POST /api/family/messages

子女创建悄悄话。

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "child",
  "direction": "child_to_elder",
  "message_type": "quiet_message",
  "content": "妈，今天降温了，记得加件衣服，我晚上给您打电话。",
  "priority": "normal"
}
```

### GET /api/elder/pending_messages

老人端获取待处理消息元数据，不含完整内容或可配置为只含摘要。

```json
{
  "messages": [
    {
      "id": "msg_001",
      "from_display": "女儿",
      "message_type": "quiet_message",
      "prompt_text": "女儿有句话想跟您说，您要不要听？",
      "status": "pending"
    }
  ]
}
```

### POST /api/elder/messages/{message_id}/consent

老人明确同意或拒绝。

```json
{
  "elder_user_id": "elder_001",
  "consent": "accepted",
  "source": "voice_confirmed"
}
```

返回：

```json
{
  "id": "msg_001",
  "status": "accepted",
  "content": "妈，今天降温了，记得加件衣服，我晚上给您打电话。"
}
```

待确认：

- 老人同意是否只能通过聊天语义识别，还是前端会提供按钮确认？
- 是否允许“以后都听”这种长期授权？当前建议不要做，先逐条确认。

## 5. 老人对子女：留言与联动

老人对子女有两类：

- 主动留言：老人想给孩子说一句话。
- 系统联动：系统因风险向子女发送提醒。

建议不要混在一个状态里，但可以共用消息表。

### POST /api/family/messages

老人主动留言也用同一个接口，区别是：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "actor_role": "elder",
  "direction": "elder_to_child",
  "message_type": "elder_note",
  "content": "闺女，晚上有空给我回个电话。",
  "delivery_mode": "pending_child_pull"
}
```

建议对老人端做二次确认：

```text
老人：你跟我女儿说我想她。
Agent：好，我帮您整理成“妈想你了，有空回个电话”。要发给女儿吗？
老人：发。
后端入队。
```

## 6. 系统到子女：风险提醒

风险提醒不是“悄悄话”，是系统根据评估生成给子女看的提醒。

### GET /api/family/alerts

子女端读取系统提醒。

```json
{
  "alerts": [
    {
      "id": "alert_001",
      "elder_user_id": "elder_001",
      "risk_tier": "crisis",
      "display_type": "sos",
      "title": "老人出现危机表达",
      "summary": "老人表达了“活着没意思”，系统已进入危机稳定流程。",
      "evidence_ids": ["assess_001"],
      "created_at": "2026-05-14 10:20:00",
      "status": "pending"
    }
  ]
}
```

注意：

- 是否展示老人原话需要你确认。默认建议展示摘要 + 可选查看原始证据，避免隐私过度暴露。

## 7. 社区管理员功能

你定义社区管理员主要两个功能：

- 设置公告，在谈及社区时可由 Agent 读到。
- 接收 `crisis` 警告危机状态。

### 7.1 社区公告

合理性判断：公告和活动建议分开。公告是“需要被读到的信息”，活动是“可被 Planner 推荐的干预资源”。

### POST /api/community/announcements

```json
{
  "community_id": "community_001",
  "actor_role": "community_admin",
  "title": "社区停水通知",
  "content": "明天上午九点到十一点小区检修管道，请提前接水。",
  "tags": ["notice", "water"],
  "valid_from": "2026-05-14 00:00:00",
  "valid_until": "2026-05-15 12:00:00",
  "priority": 5
}
```

### GET /api/community/announcements

前端或 Agent 读取有效公告。

Agent 使用规则：

- 老人主动谈及社区、物业、小区、活动室时，可以读公告。
- 高风险心理干预初期不主动插公告，避免打断安全稳定流程。

### 7.2 社区活动

你确认：社区活动由前端调用后端接口写入，并可通过后端接口读取。

### POST /api/community/activities

```json
{
  "community_id": "community_001",
  "title": "社区合唱活动",
  "time_text": "今天下午三点",
  "location": "社区活动室",
  "tags": ["music", "social", "low_intensity"],
  "suitable_states": ["lonely", "anxiety_low", "depression_low"],
  "valid_until": "2026-05-14 16:00:00",
  "priority": 3
}
```

### GET /api/community/activities

读取活动列表。

Planner 使用规则：

- `safe/low/medium` 可以推荐。
- `crisis` 初期不推荐活动，先安全稳定。
- 老人明确拒绝外出时，不重复推。

### 7.3 社区危机警告

### GET /api/community/crisis_alerts

社区端读取危机警告。

```json
{
  "alerts": [
    {
      "id": "crisis_001",
      "elder_user_id": "elder_001",
      "risk_tier": "crisis",
      "alarm_level": "level_2",
      "summary": "老人出现明确危机表达，系统已通知子女并进入安全陪伴流程。",
      "evidence_ids": ["assess_001"],
      "created_at": "2026-05-14 10:20:00",
      "status": "pending"
    }
  ]
}
```

## 8. SOS 与危机信息接口

你提出：后端需要专用接口收集和辨别危机具体信息，以便提供合适报警级别。

合理性判断：非常必要。建议把它设计成“危机事件服务”，聊天管线内部调用，前端也可以在传感器/按钮触发时调用。

### POST /api/crisis/events

```json
{
  "elder_user_id": "elder_001",
  "source": "chat | frontend_sos_button | sensor | caregiver",
  "raw_text": "活着没意思",
  "context": {
    "last_user_message_id": "turn_001",
    "location": "home",
    "visual_emotion": {"emotion": "sad", "confidence": 0.81}
  }
}
```

返回：

```json
{
  "crisis_event_id": "crisis_001",
  "risk_tier": "crisis",
  "alarm_level": "level_2",
  "recommended_channels": ["family_alert", "community_alert"],
  "evidence_ids": ["assess_001"],
  "status": "created"
}
```

报警级别建议先用 3 级：

- `level_1`：高关注，给子女提醒，不通知社区。
- `level_2`：危机，通知子女 + 社区管理员。
- `level_3`：多信号严重危机，前端显式 SOS + 子女 + 社区。

待确认：

- `crisis` 是否默认 `level_2`？
- 什么条件升级到 `level_3`？例如重复危机表达、前端 SOS 按钮、视觉/语音强烈异常。

## 9. 风险评估证据接口

你确认：疑似抑郁倾向等可通过接口获取数据给前端显示；推理证据需要落盘。

合理性判断：需要。建议证据和用户端回复分离，避免把内部推理过程直接说给老人。

### GET /api/mental_assessments

```json
{
  "elder_user_id": "elder_001",
  "limit": 20
}
```

返回：

```json
{
  "items": [
    {
      "id": "assess_001",
      "created_at": "2026-05-14 10:20:00",
      "risk_tier": "crisis",
      "primary_state": "suicidal_ideation",
      "display_label": "危机风险",
      "confidence": 0.95,
      "evidence": [
        {
          "type": "text_quote",
          "content": "活着没意思",
          "weight": 100
        }
      ],
      "next_goal": "安全稳定"
    }
  ]
}
```

展示建议：

- 子女/管理员端可显示 `display_label`、摘要、证据。
- 老人端不显示“疑似抑郁/危机”等标签，只用于 Agent 行为。

## 10. 音乐库接口

你确认：音乐库由前端设置，后端只传输名字。

合理性判断：可行。建议后端仍存标签，便于 Planner 选择合适歌曲。

### POST /api/music/library

```json
{
  "elder_user_id": "elder_001",
  "items": [
    {
      "music_name": "月亮代表我的心",
      "singer": "邓丽君",
      "tags": ["calm", "classic", "comfort"],
      "suitable_states": ["anxiety", "lonely", "depression_low"]
    }
  ]
}
```

### GET /api/music/library

读取音乐库。

### /api/chat 中的 music_payload

建议简化为：

```json
{
  "intent": "play_music",
  "trigger_music": true,
  "music_name": "月亮代表我的心"
}
```

如前端只接受名字，可以兼容保留 `query` 字段：

```json
{
  "query": "月亮代表我的心",
  "music_name": "月亮代表我的心"
}
```

## 11. 讲故事

你确认：故事由 LLM 生成。

建议：

- 不需要单独故事库接口。
- 需要故事动作事件，便于前端区分普通聊天和“讲故事模式”。

建议 `story_payload`：

```json
{
  "intent": "tell_story",
  "title": "一碗热汤的故事",
  "content": "从前有位老人...",
  "post_reply": "这个故事听完，您想不想也跟我说说以前家里热闹的时候？"
}
```

## 12. Planner 状态接口

你确认：后台 Planner 状态通过专用接口让前端获取。

### GET /api/planner/status

```json
{
  "elder_user_id": "elder_001",
  "care_plan": {
    "risk_tier": "medium",
    "active_domain": "anxiety",
    "current_stage": "anxiety.body_regulation",
    "next_turn_goal": "引导轻量呼吸或着陆练习",
    "target_agent": "mental_health_agent"
  },
  "last_assessment_id": "assess_001",
  "last_updated_at": "2026-05-14 10:22:00",
  "planner_status": "idle"
}
```

## 13. 当前最需要你确认的问题

1. 风险评分的权重是否接受“硬规则 + 加权评分 + LLM 复核”的混合方案？
2. `crisis` 是否默认通知社区管理员，还是先只通知子女，达到 `alarm_level=level_2/3` 才通知社区？
3. 子女行为规范是否允许设置“不要提某些话题”？如果老人主动提到这些话题，Agent 是否仍可回应？
4. 悄悄话读取同意，前端会提供按钮，还是完全靠语义识别？
5. 风险证据里是否允许保存老人原话？如果允许，哪些角色能看到原话？
