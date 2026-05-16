# 心理健康识别、分级 CBT 干预与后台计划闭环设计

更新时间：2026-05-15

## 1. 目标

本机制要解决当前项目的核心缺口：

- 心理健康识别不再散落在提示词、关键词和工具调用中。
- 每轮都产出结构化评估：证据、置信度、严重程度、风险等级、下一步目标。
- CBT 不再只是提示词，而是可追踪的阶段状态机。
- 路由不再只是规则命中，而是后台 Planner 对“下一个干预目标”的规划结果。
- “活着没意思”直接进入 `crisis`，并强制走危机稳定流程。

## 2. 总体链路

推荐把系统拆成两条链路：

```text
实时链路 FastResponder
用户输入
  -> 轻量评估 RiskAssessment
  -> 读取 CarePlan
  -> 决定本轮 target_agent + intervention_goal
  -> 流式回复 + 输出动作事件
  -> 保存对话、评估和干预记录

后台链路 BackgroundPlanner
本轮结束后
  -> 汇总本轮评估、历史趋势、用户画像、工具结果
  -> ReAct 思考下一轮干预目标
  -> 更新 CarePlan
  -> 必要时写入子女/社区消息队列
```

实时链路保证快，后台链路保证策略质量。

## 3. 核心数据结构

### 3.1 MentalRiskAssessment

建议新增 `src/schemas/mental_health.py`，用 Pydantic 定义：

```python
class DetectedState(BaseModel):
    state: str
    severity: int
    confidence: float
    evidence: list[str]
    source: str = "text"

class SafetyFlags(BaseModel):
    self_harm_ideation: bool = False
    explicit_death_wish: bool = False
    medical_emergency: bool = False
    fraud_risk: bool = False
    manic_activation: bool = False

class MentalRiskAssessment(BaseModel):
    turn_id: str
    user_id: str
    primary_state: str
    detected_states: list[DetectedState]
    risk_tier: str
    confidence: float
    evidence_summary: str
    safety_flags: SafetyFlags
    next_response_mode: str
```

`risk_tier` 固定为：

- `safe`
- `low`
- `medium`
- `high`
- `crisis`

### 3.2 CarePlan

```python
class CarePlan(BaseModel):
    user_id: str
    active_domain: str
    risk_tier: str
    current_stage: str
    stage_goal: str
    next_turn_goal: str
    target_agent: str
    allowed_interventions: list[str]
    blocked_interventions: list[str]
    abort_conditions: list[str]
    expires_after_turns: int = 2
```

建议保存到 `data/care_plan.json`。后续多用户时按 `user_id` 拆分。

### 3.3 InterventionLog

```python
class InterventionLog(BaseModel):
    turn_id: str
    user_id: str
    risk_tier: str
    intervention_type: str
    stage: str
    goal: str
    payload: dict
    result: str | None = None
```

建议保存到 `data/intervention_log.jsonl`。

## 4. 风险分级判定方式

建议不要只依赖一个公式，也不要完全交给 LLM。更稳妥的方式是三层混合：

```text
硬规则红线
  -> Python 加权评分
  -> LLM 结构化复核与证据摘要
```

原因：

- 硬规则负责兜住危机场景，例如“活着没意思”直接 `crisis`。
- Python 加权评分负责稳定、可解释、可测试。
- LLM 负责理解语境、补充证据摘要和下一步回应模式，但不能推翻硬规则。

### 4.1 硬规则

直接 `crisis`：

- “活着没意思”
- “不想活了”
- “死了算了”
- “我想去死”
- “我不想再撑了”
- 明确自伤、轻生、告别、安排后事等表达

直接 `high` 或 `crisis` 候选：

- “我是累赘”
- “没人需要我”
- “我撑不住了”
- 极端无助 + 近期持续失眠/不吃饭/不出门

躁期高风险候选：

- 连续不睡也不困。
- 话题跳跃、计划暴增。
- 冲动花钱或重大决定。
- 明显兴奋但不可控。

身体紧急独立标记：

- 摔倒起不来、胸口疼、喘不上气、呼吸困难等标记 `medical_emergency=true`，但前台仍遵守医疗红线。

### 4.2 Python 评分草案

```python
def assess_risk(text: str, trend: dict, context: dict) -> dict:
    score = 0
    evidence = []

    crisis_phrases = ["活着没意思", "不想活了", "死了算了", "我想去死"]
    if any(p in text for p in crisis_phrases):
        return {
            "risk_tier": "crisis",
            "primary_state": "suicidal_ideation",
            "confidence": 0.95,
            "evidence": [p for p in crisis_phrases if p in text],
        }

    anxiety = ["心慌", "发慌", "坐立不安", "睡不着", "担心", "害怕"]
    depression = ["没力气", "不想动", "没意思", "空落落", "我是累赘"]
    mania = ["一夜没睡也不困", "停不下来", "好多计划", "花钱"]

    for p in anxiety:
        if p in text:
            score += 2
            evidence.append(p)
    for p in depression:
        if p in text:
            score += 3
            evidence.append(p)
    for p in mania:
        if p in text:
            score += 3
            evidence.append(p)

    if trend.get("medium_count", 0) >= 2:
        score += 2
    if trend.get("high_count", 0) >= 1:
        score += 3

    if score >= 8:
        tier = "high"
    elif score >= 5:
        tier = "medium"
    elif score >= 2:
        tier = "low"
    else:
        tier = "safe"

    return {
        "risk_tier": tier,
        "confidence": min(0.95, 0.45 + score * 0.06),
        "evidence": evidence,
    }
```

### 4.3 风险评分公式建议

建议第一版使用可调权重，不追求医学量表精度，目标是“业务可解释 + 能被测试 + 能持续校准”。

总分：

```text
risk_score =
  crisis_override
  + text_signal_score
  + trend_score
  + multimodal_score
  + context_score
  + protective_factor_adjustment
```

其中：

- `crisis_override`：命中危机硬规则时直接 `crisis`，不再看总分。
- `text_signal_score`：本轮文本证据。
- `trend_score`：最近多轮风险趋势。
- `multimodal_score`：语音/视觉情绪，只做加权辅助，不单独决定危机。
- `context_score`：近期重大事件、长期孤独、睡眠/食欲/活动变化等。
- `protective_factor_adjustment`：保护因素扣分，例如用户明确愿意继续聊、愿意联系家人、情绪已缓和。

初始权重建议：

| 信号 | 分值 |
| --- | --- |
| 明确轻生/活着没意思/不想活了 | 直接 `crisis` |
| 自责、累赘、无价值感 | +4 |
| 持续失眠、吃不下、不想动 | +3 |
| 明显焦虑：心慌、发慌、坐立不安、灾难化担心 | +2 |
| 躁期高风险：不睡也不困、冲动花钱、计划暴增 | +3 |
| 最近 5 轮已有 2 次 `medium` | +2 |
| 最近 5 轮已有 1 次 `high` | +3 |
| 视觉/语音强烈悲伤或激动且置信度高 | +1 到 +2 |
| 用户明确表示愿意继续聊、愿意听陪伴、愿意联系家人 | -1 到 -2 |

阈值建议：

| 总分 | 风险等级 |
| --- | --- |
| 0-1 | `safe` |
| 2-4 | `low` |
| 5-7 | `medium` |
| 8+ | `high` |
| 命中硬规则 | `crisis` |

待核对：

- “不想活了”是否和“活着没意思”一样直接 `crisis`？建议是。
- `high` 是否通知子女悄悄提醒，`crisis` 是否通知子女 + 社区？
- 视觉/语音情绪是否允许影响风险等级，还是只作为证据展示？
- 保护因素最多能降几分？建议不能把 `crisis` 降级。

### 4.4 LLM 复核范围、边界与时效

LLM 复核的定位不是重新判风险等级，而是做“语义补全 + 证据整理 + 下一步模式建议”。硬规则和 Python 评分先给出即时判定，LLM 复核只在允许范围内补充结构化信息。

LLM 只能补充：

- 情绪主类：焦虑、低落、孤独、躁期高涨、危机表达、普通陪伴等。
- 语义归因：例如“头疼”更像焦虑伴随表达，还是身体紧急硬信号。
- 证据摘要：把老人原话处理成摘要，不输出长推理过程。
- 保护因素：老人是否愿意继续聊、是否愿意听留言、是否愿意联系家人。
- 下一步回应模式：安全稳定、接纳安抚、呼吸/着陆、行为激活、话题转移、子女/社区联动。
- 建议给子女端看的摘要和建议：仅子女端可见。

LLM 不能做：

- 把 `crisis` 降级。
- 输出诊断名称。
- 输出医疗建议。
- 输出“去医院看看”等表述。
- 覆盖 `SafetyPolicy`。
- 把内部思考过程暴露给老人端、子女端或社区端。

时效性策略：

```text
同步硬规则/Python 评分，目标 < 50ms
  -> 如果命中 crisis/high，立即进入安全回复和联动队列
  -> 并发启动 LLM 复核，目标 300-800ms 内返回
  -> 超过 timeout 仍未返回，前台不等待，后台落盘为 review_pending
  -> LLM 迟到结果只更新 mental_assessments、care_plan、family/community 摘要
```

并发建议：

- 与 RAG 预加载、视觉接口、CarePlan 读取并发。
- `crisis` 硬规则不等待 LLM 复核，避免延迟安全响应。
- `safe/low` 可等待短超时 LLM 复核，用于更自然地嵌入悄悄话或社区活动。
- LLM 复核失败时，保留 Python 评估结果，并写入 `review_status="failed"`。
- 复核结果带 `reviewed_at`、`source_turn_id`、`expires_at`，避免旧复核污染新一轮。

时效字段建议：

```json
{
  "review_status": "pending | completed | timeout | failed",
  "review_started_at": "2026-05-15 10:20:00",
  "reviewed_at": "2026-05-15 10:20:01",
  "expires_at": "2026-05-15 10:25:01",
  "latency_ms": 620
}
```

### 4.5 风险证据落盘

每次评估都要保存到 `data/mental_assessments.jsonl`，供前端接口读取。

建议字段：

```json
{
  "id": "assess_001",
  "turn_id": "turn_001",
  "elder_user_id": "elder_001",
  "created_at": "2026-05-14 10:20:00",
  "risk_tier": "crisis",
  "primary_state": "suicidal_ideation",
  "display_label": "危机风险",
  "score": 100,
  "confidence": 0.95,
  "evidence": [
    {
      "type": "text_quote",
      "content": "活着没意思",
      "weight": 100,
      "source": "current_turn"
    }
  ],
  "next_goal": "安全稳定",
  "llm_review": {
    "state_summary": "用户表达强烈无意义感，需要危机稳定流程。",
    "allowed_frontend_label": "危机风险"
  }
}
```

注意：

- 老人端不展示这些标签。
- 风险证据允许保存老人原话，但按角色做可见性控制。
- 子女端可见老人原话、风险摘要、后台 Agent 处理后的建议。
- 社区端默认只看摘要、原因和解决建议，不展示老人原话，除非后续明确授权。
- `crisis` 默认通知社区管理员，但社区通知内容使用摘要化表达。

## 5. 分级 CBT 状态机

### 5.1 焦虑路径

```text
anxiety.emotional_first_aid
  -> anxiety.body_regulation
  -> anxiety.cognitive_reframe
  -> anxiety.micro_action
  -> anxiety.topic_shift_or_community
```

阶段规则：

- `emotional_first_aid`：接纳 + 正常化，不讲道理。
- `body_regulation`：呼吸、着陆、慢下来。
- `cognitive_reframe`：区分担心和事实。
- `micro_action`：一个极小行动。
- `topic_shift_or_community`：音乐、照片、社区活动。

### 5.2 抑郁路径

```text
depression.low_energy_companion
  -> depression.micro_activation
  -> depression.value_recall
  -> depression.routine_support
  -> depression.family_or_community_soft_link
```

阶段规则：

- `low_energy_companion`：承认没力气，不催。
- `micro_activation`：极小行为激活，例如拉开窗帘、喝口水、坐到阳台。
- `value_recall`：回顾高光时刻，但不强行正能量。
- `routine_support`：温和支持吃饭、睡眠、晒太阳，不做医疗建议。
- `family_or_community_soft_link`：子女悄悄话或社区轻活动。

### 5.3 双相躁期倾向路径

```text
bipolar_mania.accept_and_slow
  -> bipolar_mania.reduce_stimulation
  -> bipolar_mania.delay_decision
  -> bipolar_mania.routine_anchor
  -> bipolar_mania.family_quiet_message
```

阶段规则：

- 不否定热情。
- 降低刺激，不兴奋跟随。
- 鼓励“先记下来，等会儿再看”，避免马上行动。
- 维护规律节奏。
- 必要时生成子女悄悄话。

### 5.4 危机路径

```text
crisis.safety_grounding
  -> crisis.short_companion
  -> crisis.family_or_sos_relay
  -> crisis.monitor_next_turn
```

“活着没意思”直接进入此路径。

`crisis` 默认动作：

- 通知子女端，包含老人原话、摘要、风险等级、建议。
- 通知社区管理员，包含原因摘要和解决建议，不默认展示老人原话。
- CarePlan 进入 `crisis.safety_grounding`。
- 前台老人端只做短句稳定与安全感回归。

前台回复要求：

- 短句。
- 稳定当下。
- 不追问刺激性细节。
- 不诊断。
- 不说医院。
- 不做医疗建议。
- 允许说“我在这儿陪着您”“咱先坐稳、慢慢呼一口气”“我会帮您把这份担心告诉家里人/守护的人”。

## 6. 后台 ReAct Planner 设计

路由选择应当是“下一步干预目标”的结果，而不是单纯关键词分派。

### 6.1 Planner 输入

- 本轮 `MentalRiskAssessment`
- 当前 `CarePlan`
- 用户画像
- 最近对话
- 情绪趋势
- 工具调用结果
- 社区活动候选
- 前端动作完成回调

### 6.2 Planner 可用动作

- `set_target_agent`
- `set_cbt_stage`
- `schedule_music`
- `schedule_story`
- `enqueue_family_message`
- `enqueue_community_sos`
- `consume_community_activity`
- `clear_plan`

### 6.3 ReAct 思考过程

```text
Thought: 本轮用户表达“活着没意思”，硬规则为 crisis，不能继续普通闲聊。
Action: set_cbt_stage(crisis.safety_grounding)
Observation: CarePlan 已更新。
Thought: 需要让下一轮目标保持安全感，并通知子女和社区管理员。
Action: enqueue_family_message(display_type="sos" or "quiet_message")
Observation: 消息已入队。
Action: enqueue_community_sos(reason_summary="老人表达强烈无意义感", suggested_actions=["请社区管理员关注老人当前状态", "优先联系子女确认照护安排"])
Observation: 社区通知已入队。
Final: 下轮 target_agent=mental_health_agent，next_turn_goal=继续稳定当下安全感。
```

实际实现时不需要把 Thought 暴露给前端，只保存结构化结果。

### 6.4 Planner 输出

```json
{
  "target_agent": "mental_health_agent",
  "intervention_goal": "crisis_safety_grounding",
  "care_plan_patch": {
    "risk_tier": "crisis",
    "current_stage": "crisis.safety_grounding",
    "next_turn_goal": "维持安全感，避免刺激追问"
  },
  "queued_actions": [
    {
      "type": "family_message",
      "display_type": "sos",
      "reason": "用户表达活着没意思"
    },
    {
      "type": "community_alert",
      "display_type": "sos",
      "reason_summary": "老人表达强烈无意义感",
      "suggested_actions": ["请社区管理员关注当前状态", "协同子女侧确认照护安排"]
    }
  ]
}
```

### 6.5 Planner 并发与短间隔输入冲突

当前代码还没有实现独立后台 ReAct Planner。落地时必须先处理并发冲突，否则会出现“上一轮后台计划晚返回，覆盖下一轮用户最新状态”的风险。

核心规则：

- `/api/chat` 实时回复不等待后台 Planner。
- 同一老人同一时间最多一个有效 Planner。
- 新 turn 到达时，旧 Planner 标记为 `cancel_requested` 或 `stale_discarded`。
- `crisis/high` 新输入可以抢占旧的低优先级 Planner。
- `safe/low` 新输入可以 debounce 300-800ms 后合并规划。
- Planner 输出提交前必须检查 `base_turn_id` 和 `care_plan_version`。
- 旧 Planner 即使完成，只要版本不一致就丢弃，不能覆盖新 CarePlan。

详细任务模型、伪代码和测试预测见 `background_planner_concurrency_design.md`。

## 7. 安全红线

必须集中到 `SafetyPolicy`，所有 Agent 共用。

严禁：

- 给用户下医学或精神疾病诊断名称。
- 说“您就是抑郁症/双相障碍/焦虑症”。
- 给医疗处置建议、药物建议、增减药建议。
- 说“带您去医院看看”“您去医院吧”等就医建议。
- 用刺激性追问扩大危机。
- 在危机场景长篇说理。

允许：

- 描述观察：“我听出来您这会儿很难受。”
- 稳定当下：“咱先坐稳，我陪您慢慢缓一口气。”
- 做业务联动：“我会把这个情况悄悄告诉家里守护您的人。”
- 播放舒缓音乐、讲短故事、照片回忆，但危机初期不强行转移。

## 7.1 分角色措辞策略

心理健康状态需要按不同端使用不同措辞。

老人端：

- 不展示“焦虑倾向/抑郁倾向/躁期倾向/危机风险”等标签。
- 可以自然表达观察，例如“我听出来您这阵子有很多焦虑”“这会儿心里压得挺重”。
- 不做诊断命名，不说“您有抑郁/双相/焦虑症”。

子女端：

- 可以展示“焦虑倾向”“抑郁倾向”“躁期高涨倾向”“危机风险”等后台评估标签。
- 可以展示老人原话、风险摘要、证据权重、建议沟通方式。
- 仍不输出医疗建议和诊断结论，只说“倾向/风险/观察”。

社区端：

- 只接收 `crisis` 级危机通知。
- 只展示原因摘要和解决建议。
- 不展示普通焦虑、抑郁、躁期倾向。
- 默认不展示老人原话。

## 8. 与现有代码的落地点

第一步最小落地：

- 新增 `assessment_agent.py`，先用 Python 硬规则 + LLM 复核。
- 新增 `care_plan_service.py`，读写 `data/care_plan.json`。
- 在 `SystemOrchestrator.process_input_stream` 开头插入评估。
- 路由改为：`assessment + care_plan + router rules` 共同决定。
- 每轮结束后启动 `BackgroundPlanner` 更新计划。

第二步：

- `risk` 事件升级为兼容结构：

```json
{
  "tier": "crisis",
  "primary_state": "suicidal_ideation",
  "confidence": 0.95,
  "evidence_summary": "用户表达活着没意思",
  "next_goal": "safety_grounding"
}
```

为了兼容前端，也可以同时保留旧字符串 `risk="crisis"`，新增 `risk_detail` 事件。

## 9. 与接口设计的关系

与前端/后端接口契约相关的内容见：

- `docs/frontend_backend_interface_proposal.md`

其中定义了：

- 子女 Agent 行为规范配置。
- 子女到老人悄悄话。
- 老人到子女留言。
- 社区公告/活动。
- 危机事件和报警级别。
- 风险评估证据查询。
- 音乐库。
- Planner 状态查询。
