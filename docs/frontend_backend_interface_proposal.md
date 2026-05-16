# 前后端接口与角色交互方案草案

更新时间：2026-05-15

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
- 悄悄话同意同时支持前端按钮确认和自然语言语义识别。
- 低风险状态下，如果有待播报悄悄话，Agent 可以在回复末尾自然嵌入提醒。
- `crisis` 默认通知社区管理员，通知内容包含原因摘要和解决建议。
- 风险证据允许保存老人原话；老人原话仅子女端可见，社区端默认只看摘要。
- 已确认采用 `actor_role` + `direction` 两字段。
- 子女端 Agent 第一版使用 SSE。
- 老人端、子女端、社区端需要使用不同心理健康措辞。
- 社区公告和社区活动需要前端 UI 分成两个入口。
- 社区活动需要 `valid_until`，不需要 `suitable_states`。

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

已确认：

- 采用 `actor_role` + `direction` 两字段。

## 3. 子女侧行为规范配置

你的想法：子女在前端设置希望 Agent 遵守的行为规范、希望 Agent 做的事情。

合理性判断：合理，但必须加优先级。子女配置属于“照护偏好”，不能覆盖老人明确拒绝、安全红线、危机流程。

已确认：

- 子女行为规范不允许设置“不要提某些话题”。
- 子女可以设置建议提起的话题。
- 每个建议话题需要能看到是否被消费。
- 每个建议话题允许设置总消费次数和频率间隔。
- 允许设置长期目标。

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
    "suggested_topics": [
      {
        "id": "topic_001",
        "title": "聊孙女近况",
        "prompt_hint": "可以温和提起孙女最近学习顺利。",
        "max_consumptions": 3,
        "consumed_count": 0,
        "min_interval_hours": 24,
        "last_consumed_at": null,
        "status": "active"
      }
    ],
    "preferred_actions": ["提醒喝水", "适当播放老歌"],
    "routine_goals": [
      {
        "id": "goal_001",
        "title": "晚饭后提醒散步",
        "type": "long_term",
        "frequency": "daily",
        "status": "active"
      }
    ],
    "crisis_contact_preference": "先通知子女，再通知社区"
  }
}
```

### GET /api/family/agent_policy

后端和前端读取当前策略。

查询参数：

```text
elder_user_id=elder_001&child_user_id=child_001
```

建议落盘：

- `data/family_agent_policy.json`

后端使用方式：

- `ContextGuard` 读取策略。
- `BackgroundPlanner` 在非危机场景参考策略。
- `SafetyPolicy` 优先级高于子女策略。
- `TopicConsumptionService` 记录建议话题是否被消费、消费次数和下次可提及时间。

## 4. 子女对老人：悄悄话

你的定义：悄悄话就是子女想给父母说的话，老人端先询问要不要听，得到明确答复再读。

合理性判断：正确。建议不要在 `/api/chat` 里直接塞完整悄悄话，避免老人未同意就被前端读出。

推荐流程：

```text
子女端提交悄悄话
  -> 后端保存 pending
  -> 老人端对话或轮询获取 only metadata
  -> 低风险且时机合适时，Agent 在对话末尾自然询问
  -> 老人通过按钮或自然语言明确同意
  -> 后端返回完整 content
  -> 前端播报/数字人朗读
```

低风险嵌入话术示例：

```text
叮咚，您的女儿在今天上午十点给您留了句话，要不要我为您读出来，还是稍后再看？
```

嵌入条件：

- 当前心理风险为 `safe` 或 `low`。
- 当前回复已经完成主要安抚目标。
- 悄悄话 `priority` 为 `normal` 或 `low`。
- 没有处于 `crisis`、身体紧急、反诈高风险等高优先级流程。
- 老人最近没有拒绝读取该消息。

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

查询参数：

```text
elder_user_id=elder_001
```

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
  "source": "button | semantic",
  "raw_text": "可以，你读吧"
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

已确认：

- 老人同意既支持前端按钮确认，也支持语义识别。

语义识别建议：

- 同意表达：“读吧”“念给我听”“可以”“听听看”“你说吧”。
- 拒绝表达：“先不听”“等会儿”“稍后再看”“不想听”。
- 不明确表达不自动读取，继续保持 `pending`。

不建议第一版支持“以后都听”这种长期授权，先逐条确认。

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

查询参数：

```text
elder_user_id=elder_001&child_user_id=child_001&limit=20
```

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

- 子女端允许查看老人原话、风险摘要和后台 Agent 建议。
- 后台 Agent 需要把老人原话处理为摘要和建议，一并发送给子女端口。
- 老人端不展示风险标签和内部证据。
- 社区端默认不展示老人原话，只展示原因摘要和解决建议。

建议扩展 payload：

```json
{
  "id": "alert_001",
  "elder_user_id": "elder_001",
  "risk_tier": "crisis",
  "display_type": "sos",
  "title": "老人出现危机表达",
  "raw_quotes": ["活着没意思"],
  "summary": "老人表达了强烈无意义感，系统已进入危机稳定流程。",
  "agent_suggestion": "建议子女尽快以平静语气联系老人，先表达陪伴，不追问刺激性细节。",
  "evidence_ids": ["assess_001"],
  "visibility": {
    "family_can_view_raw_quotes": true,
    "community_can_view_raw_quotes": false
  },
  "created_at": "2026-05-15 10:20:00",
  "status": "pending"
}
```

## 7. 社区管理员功能

你定义社区管理员主要两个功能：

- 设置公告，在谈及社区时可由 Agent 读到。
- 接收 `crisis` 警告危机状态。

### 7.1 社区公告

合理性判断：公告和活动建议分开。公告是“需要被读到的信息”，活动是“可被 Planner 推荐的干预资源”。

已确认：

- 社区公告和社区活动在前端 UI 上分成两个入口。

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

查询参数：

```text
community_id=community_001&only_active=true
```

Agent 使用规则：

- 老人主动谈及社区、物业、小区、活动室时，可以读公告。
- 高风险心理干预初期不主动插公告，避免打断安全稳定流程。

### 7.2 社区活动

你确认：社区活动由前端调用后端接口写入，并可通过后端接口读取。

已确认：

- 社区活动需要过期时间 `valid_until`。
- 社区活动不需要 `suitable_states`，Planner 只根据活动标题、内容、时间、地点、标签和当前上下文判断是否推荐。

### POST /api/community/activities

```json
{
  "community_id": "community_001",
  "title": "社区合唱活动",
  "time_text": "今天下午三点",
  "location": "社区活动室",
  "tags": ["music", "social", "low_intensity"],
  "valid_until": "2026-05-14 16:00:00",
  "priority": 3
}
```

### GET /api/community/activities

读取活动列表。

查询参数：

```text
community_id=community_001&only_active=true
```

Planner 使用规则：

- `safe/low/medium` 可以推荐。
- `crisis` 初期不推荐活动，先安全稳定。
- 老人明确拒绝外出时，不重复推。
- 活动过期后不再推荐。

### 7.3 社区危机警告

### GET /api/community/crisis_alerts

社区端读取危机警告。`crisis` 默认写入社区管理员通知队列。

查询参数：

```text
community_id=community_001&status=pending&limit=20
```

```json
{
  "alerts": [
    {
      "id": "crisis_001",
      "elder_user_id": "elder_001",
      "risk_tier": "crisis",
      "alarm_level": "level_2",
      "reason_summary": "老人出现明确危机表达，系统已通知子女并进入安全陪伴流程。",
      "suggested_actions": [
        "请社区管理员关注老人当前状态",
        "优先协同子女确认照护安排",
        "沟通时使用平静短句，不追问刺激性细节"
      ],
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

已确认：

- `crisis` 默认通知社区管理员。

建议：

- `crisis` 默认 `level_2`。
- 重复危机表达、前端 SOS 按钮、视觉/语音强烈异常、多信号叠加时升级到 `level_3`。

## 9. 风险评估证据接口

你确认：疑似抑郁倾向等可通过接口获取数据给前端显示；推理证据需要落盘。

合理性判断：需要。建议证据和用户端回复分离，避免把内部推理过程直接说给老人。

### GET /api/mental_assessments

查询参数：

```text
elder_user_id=elder_001&limit=20
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

- 老人端：不显示“疑似抑郁/危机”等标签，只用于 Agent 行为；可用自然话术，例如“我听出来您这阵子有很多焦虑，咱先慢慢缓一缓。”
- 子女端：可显示“焦虑倾向”“抑郁倾向”“躁期高涨倾向”等风险倾向、摘要、证据和建议。
- 社区端：只能看到 `crisis` 级危机通知、原因摘要和解决建议，不显示普通焦虑/抑郁倾向，不显示老人原话。

## 10. 音乐库接口

你确认：音乐库由前端设置，后端只传输名字。

合理性判断：可行。后端需要保存标签和歌曲摘要，便于 Agent 选择合适歌曲。

### POST /api/music/library

```json
{
  "elder_user_id": "elder_001",
  "items": [
    {
      "music_name": "月亮代表我的心",
      "singer": "邓丽君",
      "summary": "温柔、怀旧、适合安抚和陪伴的经典老歌。",
      "tags": ["舒缓", "怀旧", "陪伴", "邓丽君"]
    }
  ]
}
```

### GET /api/music/library

读取音乐库。

查询参数：

```text
elder_user_id=elder_001
```

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

### POST /api/action_complete

音乐播放完成后，前端回调后端。当前前端还没有支持，需要新增该接口。

建议：

- 前端自然播放完成：`status=completed`。
- 用户手动打断、切歌、关闭播放器：`status=interrupted`。
- 前端主动取消且没有开始播放：`status=cancelled`。
- 播放失败：`status=failed`。

`interrupted` 也算 action session 结束，但不等于干预完整完成。后端应该记录结束并让 Planner 判断是否需要温和追问、换歌、转入其他干预，而不是默认进入“音乐有效完成”后的话题引导。

请求：

```json
{
  "elder_user_id": "elder_001",
  "action_id": "act_music_001",
  "action_type": "music",
  "status": "completed | interrupted | cancelled | failed",
  "music_name": "月亮代表我的心",
  "played_seconds": 42,
  "total_seconds": 180,
  "interrupt_reason": "user_skip | user_close | system_interrupt | unknown",
  "finished_at": "2026-05-15 10:30:00"
}
```

返回：

```json
{
  "status": "success",
  "post_reply": "这首歌先到这儿。您现在心里是松一点了，还是想换个方式让我陪您缓一缓？",
  "next_turn_goal": "温和确认情绪变化",
  "care_plan_patch": {
    "current_stage": "anxiety.topic_shift_or_community"
  }
}
```

落盘建议：

- `data/action_sessions.jsonl`
- `data/intervention_log.jsonl`

唱歌前回复建议：

- 唱歌前回复应先作为普通 `token` 输出，让老人知道系统即将播放音乐。
- 随后再输出 `music_payload`，包含 `action_id`、`music_name`、`post_reply`。
- 前端收到 `music_payload` 后播放歌曲。
- 播放结束或打断后调用 `POST /api/action_complete`。

如果前端短期无法回调：

- 后端只能返回 `post_reply` 给前端缓存，由前端在音乐结束后自行播报。
- 但后台 Planner 无法可靠知道音乐是否播放完成。

## 11. 定时事件与用药提醒接口

用药提醒属于定时事件，不建议继续放在 `MedicalAgent.check_medication_reminder()` 里临时判断。

设计原则：

- 只读取已记录医嘱或照护者录入信息。
- 可以提醒药名、剂量文本、饭前/饭后等记录。
- 不能生成新剂量，不能建议补服、加量、减量、停药或换药。
- 支持到点提醒、过时提醒、确认、稍后提醒和过期停止。

### POST /api/medication/plans

```json
{
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
  "window_after_minutes": 30,
  "expire_after_minutes": 180,
  "status": "active"
}
```

### GET /api/medication/plans

```text
elder_user_id=elder_001
```

### GET /api/timed_events/due

```text
elder_user_id=elder_001
```

返回：

```json
{
  "events": [
    {
      "event_id": "dose_20260516_0800_med_001",
      "event_type": "medication_due",
      "priority": "high",
      "display_text": "叮咚，到您按记录吃药的时间了：药名，一次1片，早餐后服用。您吃过后跟我说一声，我帮您记一下。",
      "payload": {
        "medication_id": "med_001",
        "name": "药名",
        "dosage_text": "一次1片",
        "instruction_text": "早餐后服用",
        "scheduled_at": "2026-05-16T08:00:00+08:00",
        "status": "due"
      }
    }
  ]
}
```

过时提醒文案：

```text
刚才那次吃药提醒时间已经过了一会儿，我担心您忙忘了。您要不要看一下药盒，确认有没有按记录吃过？
```

### POST /api/timed_events/{event_id}/ack

```json
{
  "elder_user_id": "elder_001",
  "ack": "taken | snooze | skip | not_sure",
  "snooze_minutes": 10,
  "text": "我吃过了"
}
```

说明：

- `taken`：老人确认已吃，不再提醒该次。
- `snooze`：稍后提醒。
- `skip`：本次不再提醒，但不表达医疗判断。
- `not_sure`：记录为未确认，可给子女端生成 quiet message。

详细机制见 `timed_event_and_medication_reminder_design.md`。

## 12. 讲故事

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

## 13. Planner 状态接口

你确认：后台 Planner 状态通过专用接口让前端获取。

### GET /api/planner/status

查询参数：

```text
elder_user_id=elder_001
```

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

## 14. 子女端 Agent 聊天框

你确认：子女端也有文字回复版本的聊天框，子女可以和 Agent 聊父母情况。该能力需要和老人端记忆隔离。

### 13.1 设计边界

- 子女端 Agent 可以读取老人画像、风险评估摘要、CarePlan、干预记录、子女可见风险证据。
- 子女端 Agent 不能把子女聊天内容写入老人聊天历史。
- 子女端 Agent 的记忆与老人端隔离，避免老人下一轮对话被子女问题污染。
- 子女端可见老人原话，但仅限风险证据与授权范围内内容。
- 子女端 Agent 不输出医疗建议，不诊断命名，只给照护沟通建议。

### POST /api/family/chat

请求：

```json
{
  "elder_user_id": "elder_001",
  "child_user_id": "child_001",
  "message": "我妈最近情绪怎么样？我该怎么跟她说话？",
  "context": {}
}
```

已确认：子女端 Agent 第一版使用 SSE，便于复用前端聊天组件。

```text
data: {"type":"token","data":"最近几次记录里，老人有明显低落和孤独表达。"}
data: {"type":"token","data":"建议您先用短句表达陪伴，不要急着追问原因。"}
data: {"type":"family_context","data":{"risk_tier":"medium","last_assessment_id":"assess_001"}}
data: {"type":"done","data":"stop"}
```

### GET /api/family/elder_summary

子女端查看父母概览。

查询参数：

```text
elder_user_id=elder_001&child_user_id=child_001
```

```json
{
  "elder_user_id": "elder_001",
  "summary": {
    "risk_tier": "medium",
    "primary_state": "low_mood",
    "recent_trend": "近几轮有低落和孤独表达",
    "care_plan_stage": "depression.low_energy_companion",
    "suggested_family_action": "用平静短句主动问候，先表达陪伴，不追问细节"
  },
  "visible_evidence": [
    {
      "id": "assess_001",
      "raw_quote": "活着没意思",
      "summary": "老人表达强烈无意义感",
      "created_at": "2026-05-15 10:20:00"
    }
  ]
}
```

### 13.2 子女端记忆落盘

建议单独保存：

- `data/users/{elder_user_id}/family/{child_user_id}/family_chat_history.json`
- `data/users/{elder_user_id}/family/{child_user_id}/family_chat_memory.jsonl`

不要写入：

- `data/chat_history.json`
- 老人端向量记忆集合
- 老人端用户画像，除非子女明确提交画像更新并经后端规则校验

### 13.3 子女端 Agent 可用上下文

允许读取：

- 老人画像。
- 最近风险评估摘要。
- 子女可见老人原话证据。
- CarePlan 当前阶段。
- 干预日志。
- 子女自己提交的偏好配置。

禁止读取或输出：

- 老人端完整长历史的无关隐私。
- 内部 ReAct 思考过程。
- 未授权社区信息。

## 15. 当前最需要你确认的问题

1. 社区消息是否永远不展示老人原话，还是允许特殊授权？
2. 音乐被打断后，前端是否能提供 `played_seconds` 和 `interrupt_reason`？
3. 子女建议话题的消费记录是否需要在子女端 UI 可编辑重置？
