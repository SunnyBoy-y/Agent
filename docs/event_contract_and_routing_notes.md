# 事件字段、共享上下文与路由设计说明

更新时间：2026-05-14

## 1. 这些字段在哪里生成、哪个接口使用

所有这些字段最终都通过 `POST /api/chat` 的 SSE 返回给前端。接口位置是 `src/server.py` 的 `chat_endpoint`，它调用 `SystemOrchestrator.process_input_stream`，然后把每个事件包装成：

```text
data: {"type": "...", "data": ...}
```

另外，`proactive_question` 由 `GET /api/proactive_check` 返回，不走 `/api/chat`。

### expression

含义：数字人表情。

生成位置：

- `src/agents/emotional_agent.py` 中 `EmotionalStateUpdate.expression`。
- `src/orchestrator.py` 的 `_run_emotional_agent` 从工具调用参数中提取后 `yield create_event("expression", value)`。

使用接口：

- `POST /api/chat`

当前只有 `emotional_agent` 明确生成该事件。

### action

含义：数字人动作或业务动作。

生成位置：

- `EmotionalStateUpdate.action`，由情感 Agent 输出。
- 其他 Agent 的返回 dict 中也可能有 `action`，例如 `play_music`、`alert_family`、`comfort`、`recommend_content`。
- `src/orchestrator.py` 统一 `yield create_event("action", result["action"])`。

使用接口：

- `POST /api/chat`

注意：当前 `action` 同时承载“数字人动作”和“业务动作”，建议后续拆成 `avatar_action` 和 `business_action`。

### risk

含义：风险等级。

生成位置：

- 情感 Agent：`EmotionalStateUpdate.risk_level`。
- 医疗 Agent：返回 `risk_level`。
- 心理 Agent：返回 `risk_level`。
- 反诈 Agent：分析结果经 Orchestrator 归一化为 `risk_level`。
- `src/orchestrator.py` 统一输出 `risk`。

使用接口：

- `POST /api/chat`

建议后续新增 `risk_detail`，包含证据、置信度、状态和下一步目标。

### photos / photos_result

含义：相册检索结果。

生成位置：

- `src/tools/professional_skills.py` 的 `search_family_photos` 调用文件服务。
- `src/orchestrator.py` 在情感 Agent 工具结束事件中解析工具输出。
- 有照片时输出 `photos`，无结果或错误时输出 `photos_result`。

使用接口：

- `POST /api/chat`

### music_payload / music

含义：音乐播放触发。

生成位置：

- `src/tools/professional_skills.py` 的 `play_music`。
- `src/agents/interest_agent.py` 识别音乐请求后返回 `music_result`。
- `src/orchestrator.py` 用 `_normalize_music_payload` 归一化后输出 `music_payload` 和 `music`。
- 情感 Agent 如果调用 `play_music` 工具，也会在 `_run_emotional_agent` 输出。

使用接口：

- `POST /api/chat`

建议后续将 `music_payload` 扩展为 `song_name`、`singer`、`pre_reply`、`post_reply`、`action_id`。

### sos

含义：触发前端 SOS 或高优先级联动。

生成位置：

- `src/tools/professional_skills.py` 的 `emergency_contact` 返回 `trigger_sos=true`。
- `src/agents/medical_agent.py` 的紧急响应返回 `sos=true`。
- `src/orchestrator.py` 统一输出 `sos`。

使用接口：

- `POST /api/chat`

建议后续分成：

- `sos=true`：前端显式 SOS。
- `relay_message`：子女悄悄话或社区消息。

### proactive_question

含义：主动关怀。

生成位置：

- `src/agents/proactive_agent.py` 的 `check_and_generate`。
- `src/orchestrator.py` 的 `check_and_generate_proactive_event` 包装为 `proactive_question`。

使用接口：

- `GET /api/proactive_check`

## 2. 当前是否有共享上下文

有。

位置：`src/orchestrator.py` 的 `_build_shared_context`。

当前共享上下文字段：

- `user_profile`
- `recent_history`
- `recent_history_text`
- `memory_context`
- `emotion_trend`
- `agent_status`
- 原始前端 context 中的 `user_id`、`audio_transcript`、`voice_emotion`、`visual_analysis` 等

这些字段会传给各个 Agent。

## 3. 当前是否处理上下文脏数据

有一点，但不够。

已有处理：

- `_sanitize_recent_history` 会过滤系统主动关怀产生的内部记录，避免下轮把系统沉默判断误当成真实用户意图。
- `RAGHelper` 会修复非标准 JSON 历史和情绪日志结构。
- `recent_history_text` 只取最近 6 条。
- `memory_context_preview` 只截断用于状态展示。

不足：

- 没有把“用户真实事实”和“Agent 生成内容”严格隔离。
- 没有对历史记忆做来源标记和可信度。
- 没有心理评估结果的证据窗口，容易被过远历史带偏。
- 没有按主题选择上下文，例如当前是焦虑就优先取情绪相关历史。

建议新增 ContextGuard：

```python
class ContextGuard:
    def build_turn_context(user_input, raw_context, rag):
        return {
            "profile": clean_profile(...),
            "recent_dialogue": last_real_user_turns(...),
            "memory": topic_filtered_memory(...),
            "emotion_trend": bounded_emotion_trend(...),
            "care_plan": current_care_plan(...),
            "excluded": ["system_proactive_records", "assistant_fiction"]
        }
```

核心原则：

- 只把老人说过的事实写入画像。
- Agent 编造的故事、比喻、安慰语不能进入长期事实。
- 当前评估只看本轮 + 最近有限窗口 + 明确相关记忆。
- 每条上下文标注 `source` 和 `confidence`。

## 4. 快速路由是什么意思

当前快速路由是 `RouterAgent.route_sync`，本质是 Python 规则匹配，不调用 LLM。

优点：

- 延迟低。
- 稳定、可解释。
- 对“摔倒、胸闷、放歌、诈骗关键词”等强信号很可靠。

缺点：

- 不能真正理解复杂语义。
- 容易把“焦虑导致头疼”误路由到医疗。
- 不会结合后台计划判断“下一步干预目标”。

结论：

当前快速路由不是语义路由，只是强规则路由。后续应改成：

```text
硬安全规则
  -> MentalRiskAssessment
  -> CarePlan.next_turn_goal
  -> ReAct Planner 推荐 target_agent
  -> RouterAgent 只做兜底和强信号拦截
```

## 5. 规则路由触发例子

### 5.1 必须医疗/身体紧急优先

触发例子：

- “救命，我摔倒了，起不来。”
- “我胸口疼得厉害，喘不上气。”
- “我呼吸困难，快不行了。”

路由：

- `medical_agent`

约束：

- 不给医疗建议。
- 不说去医院。
- 只做安全陪伴和业务联动。

### 5.2 心理危机优先

触发例子：

- “活着没意思。”
- “我不想活了。”
- “死了算了。”

路由：

- `mental_health_agent`
- `risk_tier=crisis`
- 后台 Planner 写入危机 CarePlan 和子女/社区联动队列。

### 5.3 焦虑导致身体不适，不应直接用药

触发例子：

- “我一紧张就头疼，越想越睡不着。”
- “心里发慌，头也胀，老担心出事。”
- “一想到孩子不回家，我胸口就堵得慌。”

路由：

- `mental_health_agent`

判断原因：

- 身体词和焦虑词共同出现。
- 没有摔倒、胸痛剧烈、呼吸困难等紧急身体硬信号。
- 下一步目标是情绪稳定，而不是用药提醒。

### 5.4 普通用药提醒

触发例子：

- “我晚上那颗药吃了吗？”
- “提醒我八点吃药。”
- “我今天的降压药记录一下。”

路由：

- `medical_agent`

约束：

- 只按用户画像/已有医嘱提醒。
- 不解释药理。
- 不建议增减药。

### 5.5 音乐请求

触发例子：

- “给我放一首邓丽君。”
- “来点舒缓音乐。”
- “想听歌。”

路由：

- `interest_agent`

如果当前 CarePlan 是危机初期：

- 先用 `mental_health_agent` 做安全稳定。
- Planner 可把音乐作为下一阶段动作，不马上强行转移。

### 5.6 诈骗风险

触发例子：

- “有人说我中奖了，让我转手续费。”
- “公安局让我把钱转到安全账户。”
- “他说要验证码。”

路由：

- `antifraud_agent`

如果同时出现心理危机：

- `crisis` 优先，前台先稳定情绪，后台同时生成反诈/家属联动。

## 6. 非情感 Agent 流式返回修复

已做最小修复：

- 其他 Agent 仍先运行 `arun` 得到完整结果。
- Orchestrator 将最终 `content` 切成小段连续输出 `token`。
- 前端现在可以统一按 `token` 流消费。

注意：

- 这不是模型级真流式，首 token 仍需等该 Agent 完整生成。
- 真流式需要后续把 `medical_agent`、`daily_life_agent`、`interest_agent`、`mental_health_agent`、`antifraud_agent` 都改成 `astream_run`。

## 7. 新增接口契约索引

子女端、老人端、社区端、危机事件、风险证据、音乐库和 Planner 状态的接口草案，统一放在：

- `docs/frontend_backend_interface_proposal.md`

接口匹配度、数据落盘和代码实施顺序复核见：

- `docs/interface_data_code_review.md`
