# 项目背景差距分析与演进方案草案

更新时间：2026-05-14

## 1. 目标闭环

项目背景要求 Agent 后端形成“感知、认知、干预”的闭环：

- 感知：识别用户心理健康状态，包括焦虑、抑郁、双相情感障碍等，并识别风险等级。
- 认知：理解当前状态的表现、成因线索、趋势和下一步目标。
- 干预：基于 CBT 等方法做分级安抚、行为引导、音乐/故事/用药提醒/亲属或社区联动以及其他行为（需要持续补充标准 CBT 手段）。
- 闭环：每轮对话后，后台 Agent 更新下一步行为目标，下一轮快速 Agent 根据该目标实时回复。

当前项目已有基础 Agent、记忆、事件和工具，但缺少一个稳定的“心理评估 + 策略计划 + 干预执行 + 结果追踪”的机制层。

## 2. 主要差距

### 2.1 感知层差距

现有能力：

- 接收文本和语音转写文本。
- 可从外部接口尝试获取视觉情绪。
- 可接收 `voice_emotion`。
- 情绪日志记录 `expression` 和 `risk_level`。
- 情感 Agent 有关键词兜底识别焦虑、抑郁和双相高涨期信号。

核心差距：

- 没有统一的心理评估结构。
- 没有区分“诊断”和“风险倾向识别”，容易在提示词中混淆专业边界。
- 没有证据链，例如本轮命中的文本、历史趋势、视觉/语音信号、置信度。
- 没有焦虑、抑郁、双相躁期、孤独、悲伤、疑似自伤意念等多维标签。
- 没有纵向风险趋势建模，只用最近 5 条风险等级做粗略判断。

建议新增 `MentalRiskAssessment`：

```json
{
  "turn_id": "uuid",
  "user_id": "user_001",
  "primary_state": "anxiety",
  "detected_states": [
    {
      "state": "anxiety",
      "severity": 2,
      "confidence": 0.78,
      "evidence": ["心里发慌", "晚上睡不好"],
      "time_scope": "recent_days"
    }
  ],
  "risk_tier": "medium",
  "safety_flags": {
    "self_harm_ideation": false,
    "medical_emergency": false,
    "fraud_risk": false
  },
  "recommended_response_mode": "stabilize_first"
}
```

### 2.2 认知层差距

现有能力：

- `EmotionalConnectionAgent` 根据画像和情绪趋势注入 CBT 提示。
- `ProactiveAgent` 根据近期关键词选择主动问候模板。
- RAGHelper 可检索历史对话摘要、生活事件和知识库。

核心差距：

- 没有“当前心理状态为什么这样”的结构化认知结果。
- 没有“本轮目标”和“下轮目标”。
- 没有风险升级/降级后的策略切换。
- 没有计划状态，例如当前处于“情绪急救”“生理调节”“温和提问”“话题转移”哪一步。
- 没有计划放弃机制，例如用户情绪升级时中断原计划。

建议新增 `CarePlan`：

```json
{
  "user_id": "user_001",
  "active_domain": "anxiety",
  "risk_tier": "medium",
  "current_stage": "emotional_first_aid",
  "stage_goal": "让用户先回到安全感和被接纳感",
  "next_turn_goal": "引导一次很轻的呼吸或着陆练习",
  "allowed_interventions": ["acceptance", "normalization", "breathing", "music"],
  "blocked_interventions": ["long_reasoning", "medical_advice", "hospital_suggestion"],
  "abort_conditions": ["self_harm_signal", "medical_emergency", "fraud_high_risk"],
  "expires_after_turns": 2
}
```

### 2.3 干预层差距

现有能力：

- 音乐播放事件。
- 相册检索。
- SOS 事件。
- 健康主诉记录。
- 用药查询逻辑。
- 主动问候模板。

核心差距：

- CBT 没有变成“可执行的分级方案”。
- 音乐没有前置回复、播放 payload、结束语、话题引导的完整生命周期。
- 讲故事未实现。
- 用药提醒没有正式事件接口和调度机制。
- 子女消息和社区管理员消息目前只有 SOS flag 或模拟动作，没有正式消息队列。
- 社区活动没有消费队列和时机判断。

建议将干预定义成统一动作：

```json
{
  "type": "music",
  "intent": "emotion_regulation",
  "pre_reply": "好，我给您放一首舒缓点的老歌，咱先让心慢下来。",
  "payload": {
    "song_name": "月亮代表我的心",
    "singer": "邓丽君",
    "mood": "calm"
  },
  "post_reply": "刚才这首歌听完，心里有没有松一点？要不要跟我说说现在最挂心的是哪件事？",
  "risk_tier": "medium"
}
```

### 2.4 快速 Agent 与后台 Agent 差距

目标背景明确要求：

- 用户发来消息时，需要一个快速 Agent 实时回复。
- 在下一次用户回复之前，后台 Agent 分析下一步行为目标。

现有系统：

- 有快速规则路由。
- 有主动关怀轮询。
- 没有“对话完成后自动启动后台规划”的异步任务。

建议拆成两条链路：

- FastResponder：处理 `/api/chat`，基于最新 `CarePlan` 和本轮评估即时回复。
- BackgroundPlanner：在本轮结束后异步运行，更新 `CarePlan`、社区活动候选、下轮目标、联动消息。

推荐流程：

```text
用户输入
  -> 快速评估 MentalRiskAssessment
  -> 读取 CarePlan
  -> FastResponder 流式回复 + 返回必要动作
  -> 保存本轮对话和评估
  -> BackgroundPlanner 异步更新下轮目标
  -> 前端下一轮请求时带上最新计划
```

### 2.5 安全红线差距

项目背景规则：

- 严禁医疗建议。
- 不能说“带您去医院看看”类似表达。
- 严重到“活着没意思”等情况时，要设计回归安全感的逻辑。

当前冲突点：

- `MedicalAgent` 当前是“家庭医生助手”定位，会生成健康建议。
- `emergency_contact` high 级别会模拟拨打 120。
- 高风险心理状态当前只是 risk=high，没有专门的安全稳定流程。

建议建立 `SafetyPolicy`：

- 输出不做诊断，不说“你是抑郁症/双相障碍”。
- 输出不提供医疗处置建议。
- 不说“去医院/带您去医院看看”。
- 对自伤或“活着没意思”：
  - 先确认陪伴和当下安全感。
  - 引导感官着陆，例如看见什么、摸到什么、坐稳。
  - 不追问刺激性细节。
  - 触发合适等级的子女悄悄话或 SOS，由业务规则决定是否前端显式展示。
  - 后台把 `CarePlan` 置为 crisis stabilization。

## 3. 建议演进架构

### 3.1 模块新增

建议新增或改造以下模块：

- `src/schemas/mental_health.py`：心理评估、风险、干预动作、CarePlan 的 Pydantic schema。
- `src/policies/safety_policy.py`：安全红线和风险分级策略。
- `src/agents/assessment_agent.py`：统一心理风险评估，可先用规则 + LLM JSON 输出。
- `src/agents/planning_agent.py`：后台计划 Agent，负责下轮目标、风险升降级和动作选择。
- `src/services/intervention_service.py`：把音乐、故事、用药提醒、子女消息、社区消息转成统一 payload。
- `src/services/community_event_queue.py`：社区活动队列读取、过滤和消费。
- `src/services/message_relay.py`：子女/社区消息队列与前端拉取接口。

### 3.2 数据新增

建议新增数据文件或表：

- `data/mental_assessments.jsonl`：每轮结构化评估日志。
- `data/care_plan.json`：当前用户活跃计划。
- `data/intervention_log.jsonl`：干预动作、触发原因和结果。
- `data/community_events.json`：社区活动队列。
- `data/relay_messages.jsonl`：给子女/社区管理员的待处理消息。

### 3.3 风险分级建议

先用业务上可解释的 5 级：

- `safe`：普通陪伴，无明显心理风险。
- `low`：轻度孤独、思念、短暂低落。
- `medium`：明显焦虑/抑郁倾向，影响睡眠、食欲、活动意愿。
- `high`：强烈无助、无价值感、持续痛苦、冲动行为或躁期高风险迹象。
- `crisis`：明确自伤/轻生表达、当下无法保证安全、严重失控。

高风险不一定等于 SOS，是否通知子女/社区要由业务授权和规则决定。

### 3.4 CBT 干预机制建议

焦虑路径：

```text
情绪急救：接纳 + 正常化
  -> 生理稳定：呼吸/着陆/慢下来
  -> 认知重构：区分担心和事实
  -> 微行动：一个小而安全的行动
  -> 话题转移或社区活动
```

抑郁路径：

```text
低电量陪伴：承认没力气，不催促
  -> 微笑行动启动：极小行为激活
  -> 价值重塑：回顾高光时刻和重要关系
  -> 规律支持：温和提醒吃饭、晒太阳、作息
  -> 家庭/社区轻联动
```

双相躁期倾向路径：

```text
接纳托底：不否定热情
  -> 降低刺激：短句、低兴奋、少夸大
  -> 放慢决策：把计划记下来，不马上行动
  -> 作息锚定：温和回到睡眠、吃饭、固定节奏
  -> 亲属悄悄话或后台关注
```

危机安全路径：

```text
稳定当下安全感
  -> 简短陪伴
  -> 感官着陆
  -> 不刺激追问
  -> 触发业务授权的联动
  -> 后台计划进入 crisis stabilization
```

## 4. 建议实施阶段

### Phase 0：确认业务边界

- 确认风险等级和联动规则。
- 确认医疗红线的具体措辞。
- 确认子女消息、社区消息、SOS 的前端接口形态。
- 确认社区活动数据来源。

### Phase 1：修复当前显性问题

- 修复 `orchestrator.py` 中 `antifraud_agent` 分支缺少 return。
- 修复流式括号过滤中 `?` 误用问题。
- 让心理健康路由至少能命中 `mental_health_agent` 或新增统一评估层。
- 补齐测试依赖或提供 mock 测试环境。

### Phase 2：引入结构化评估与安全策略

- 增加 `MentalRiskAssessment` schema。
- 增加 `SafetyPolicy`。
- 把风险评估结果写入日志。
- 将 `risk` 事件从简单字符串升级为兼容结构化 payload。

### Phase 3：引入 CarePlan 和后台 Planner

- 每轮结束后启动后台计划。
- 存储下轮目标和当前阶段。
- FastResponder 读取计划并决定本轮话术。
- 支持风险升级/降级后中止或切换计划。

### Phase 4：完善干预工具闭环

- 标准化音乐、故事、用药提醒、子女消息、社区消息 payload。
- 增加动作完成回调，例如 `/api/action_complete`。
- 支持音乐/故事结束后的话题引导。
- 增加社区活动队列和时机判断。

### Phase 5：评测与回归

- 建立心理风险样例集。
- 建立红线用语检查。
- 建立前端事件契约测试。
- 建立多轮干预流程测试。

## 5. 近期最小可行修改建议

第一轮不要直接大重构，建议先做四件事：

1. 修复当前 `antifraud_agent` 路由运行错误。
2. 新增结构化文档和 schema，不立即替换所有 Agent。
3. 在 Orchestrator 开头增加轻量 `assessment` 步骤，先用规则实现，再接 LLM。
4. 新增 `care_plan.json`，让后台 Planner 可以先以同步/伪异步方式写入下一步目标。

这样可以保留现有可用能力，同时逐步把心理健康闭环从提示词升级成机制。

## 6. 用户澄清后的缺口到方案总表

| 缺口 | 解决方案 | 首批落地点 |
| --- | --- | --- |
| 心理识别散落在提示词、关键词、工具调用中 | 新增 `MentalRiskAssessment`，每轮统一评估，输出证据、置信度、严重程度、风险等级 | `src/agents/assessment_agent.py`、`data/mental_assessments.jsonl` |
| 缺少稳定风险分级 | 使用 Python 硬规则优先，LLM 只做结构化复核；“活着没意思”直接 `crisis` | `src/policies/safety_policy.py`、`src/schemas/mental_health.py` |
| CBT 只是提示词 | 建立 CBT 阶段状态机：焦虑、抑郁、双相躁期、危机四条路径 | `data/care_plan.json`、`src/services/care_plan_service.py` |
| 路由是规则命中，不是干预目标 | 后台 ReAct Planner 输出下一轮 `target_agent` 和 `intervention_goal`，实时链路读取计划 | `src/agents/planning_agent.py` |
| 上下文可能跑偏 | 新增 `ContextGuard`，区分老人事实、AI 生成内容、系统事件、低可信历史 | `src/services/context_guard.py` |
| 音乐没有完整生命周期 | 音乐动作扩展 `pre_reply`、`music_payload`、`post_reply`、`action_id`，前端完成后回调 | `POST /api/action_complete` |
| 讲故事未实现 | 新增故事干预动作，可按用户偏好和风险阶段选择短故事 | `src/services/intervention_service.py` |
| 用药提醒边界不清 | 只基于既有画像/已确认医嘱提醒，不解释药理、不增减药 | `SafetyPolicy` + `MedicalAgent` 改造 |
| 子女/社区联动不完整 | 新增 `relay_messages.jsonl`，前端拉取后渲染 SOS 或悄悄话 | `GET /api/relay_messages` |
| 社区活动没有队列 | 新增 `community_events.json` 和消费策略，Planner 判断时机 | `src/services/community_event_queue.py` |
| 前端事件混用 | 将 `action` 拆成 `avatar_action` 和 `business_action`，新增 `risk_detail` | `src/orchestrator.py` |
| 医疗/心理红线不统一 | 所有 Agent 统一走 `SafetyPolicy` 后处理，禁止诊断命名和医疗建议 | `src/policies/safety_policy.py` |

详细机制见：

- `docs/mental_health_cbt_closure_design.md`
- `docs/event_contract_and_routing_notes.md`
- `docs/frontend_backend_interface_proposal.md`
