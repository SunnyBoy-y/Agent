# 当前代码风险修复优先级与方案

更新时间：2026-05-16

## 1. 修复原则

本阶段目标不是一次性实现全部 family/community/music/planner 接口，而是先修复会影响后续扩展的架构风险：

- 先把轻量落盘能力从 `RAGHelper` 中剥离出来。
- 先建立 `SafetyPolicy`，再改医疗、心理、反诈等高风险输出。
- 先让 `user_id` 贯穿新增数据，不急着迁移旧全局文件。
- 先做风险评估和消息队列，再做子女端 Agent 和社区活动推荐。

## 2. 风险优先级总表

| 优先级 | 风险 | 影响 | 修复策略 |
| --- | --- | --- | --- |
| P0 | `MedicalAgent` 仍有“家庭医生助手/健康建议”提示词 | 违反医疗红线，风险最高 | 新增 `SafetyPolicy`，先做输出后处理，再改提示词 |
| P0 | 反诈、心理、医疗联动没有落盘队列 | crisis/子女/社区通知无法追踪 | 新增 `DataStore` + `relay_message_service` + alerts JSONL |
| P0 | `RAGHelper` 多处实例化且承担过多职责 | 新接口复用会引入慢启动和副作用 | 新增轻量 `DataStore`，新业务落盘不走 RAGHelper |
| P1 | `POST /api/profile` 没有 `user_id` | 多用户会写串画像 | 兼容旧接口，新增 query/body 中的 `user_id`，默认 `elder_001` |
| P1 | `/api/proactive_check` 没有 `user_id` | 多用户主动关怀会串状态 | 增加 `user_id` query 参数，默认 `elder_001` |
| P1 | `AntiFraudAgent.arun` 重复定义 | 维护风险和潜在覆盖问题 | 删除重复定义，补单测 |
| P2 | 全局文件未迁移到 `data/users/{elder}` | 多角色数据隔离不足 | 先兼容，后续迁移脚本 |
| P2 | Chroma 集合无 `elder_user_id` 过滤 | 多用户记忆检索串扰 | 写入 metadata，检索时加过滤 |

## 3. P0 修复方案

### P0.1 新增 SafetyPolicy，约束医疗与心理输出

问题：

- `MedicalAgent` 当前提示词仍是“家庭医生助手”，会生成健康建议。
- 项目规则要求严禁医疗建议、严禁“去医院看看”、严禁诊断命名。

方案：

- 新增 `src/policies/safety_policy.py`。
- 提供三类能力：
  - `sanitize_user_facing_text(text)`：过滤或替换禁用表达。
  - `validate_agent_result(result, context)`：对 Agent 返回结果做安全检查。
  - `build_safe_emergency_reply(kind)`：身体紧急/心理危机的固定安全话术。
- Orchestrator 在所有 Agent 返回前统一调用。
- MedicalAgent 提示词改为“健康记录与提醒助手”，不再生成健康建议。

验收：

- 输入“我胸口疼，要不要去医院”时，回复不能出现“去医院/看医生/医疗建议/诊断”等表达。
- 用药问题只能基于已记录用药提醒，不解释药理、不增减药。
- 心理危机不输出“抑郁症/双相障碍/焦虑症”等诊断命名。

### P0.2 新增 DataStore，避免把新业务塞进 RAGHelper

问题：

- `RAGHelper` 初始化 embedding 和 Chroma，不适合每个接口做轻量 JSON 读写。
- 当前它已负责画像、记忆、情绪、状态、向量库，职责过重。

方案：

- 新增 `src/services/data_store.py`。
- 支持：
  - `read_json(path, default)`
  - `write_json(path, data)`
  - `append_jsonl(path, item)`
  - `read_jsonl(path, limit=None, filters=None)`
  - 自动创建目录。
  - FileLock。
- 新增路径工具：
  - `elder_dir(elder_user_id)`
  - `family_dir(elder_user_id, child_user_id)`

验收：

- 不实例化 `RAGHelper` 也能完成 family/community/action/assessment 文件读写。
- 并发 append JSONL 不破坏文件。

### P0.3 落地风险评估与联动队列

问题：

- 反诈、心理、医疗消息联动目前只返回事件，没有持久化队列。
- crisis 默认通知社区管理员，但当前无 `community_alerts` 落盘。

方案：

- 新增：
  - `src/schemas/mental_health.py`
  - `src/schemas/relay.py`
  - `src/services/assessment_service.py`
  - `src/services/relay_message_service.py`
- 新增落盘：
  - `data/users/{elder}/mental_assessments.jsonl`
  - `data/family_alerts.jsonl`
  - `data/community_alerts.jsonl`
  - `data/relay_messages.jsonl`
- crisis 评估后立即写：
  - family alert：含老人原话、摘要、建议。
  - community alert：只含原因摘要、解决建议。

验收：

- “活着没意思”直接生成 `risk_tier=crisis`。
- 子女 alert 中有 `raw_quotes`。
- 社区 alert 中没有 `raw_quotes`。
- Orchestrator 输出兼容旧 `risk`，并新增 `risk_detail`。

## 4. P1 修复方案

### P1.1 给画像接口增加 user_id

问题：

- `POST /api/profile` 和 `GET /api/profile` 当前读写全局 `data/user_profile.json`。

方案：

- 保持旧行为：未传 `user_id` 时默认 `elder_001` 或当前全局画像。
- 新增 query/body 支持：
  - `GET /api/profile?user_id=elder_001`
  - `POST /api/profile` body 中允许 `user_id`。
- 第一版可以仍写旧文件，但接口签名先对齐。
- 第二版迁移到 `data/users/{elder}/profile.json`。

验收：

- 旧前端不传 `user_id` 不崩。
- 新前端传 `user_id` 后响应包含该 `user_id`。

### P1.2 给 proactive_check 增加 user_id

问题：

- `/api/proactive_check` 当前没有 `user_id`，多用户会共享 `agent_status.json`。

方案：

- 改为：

```text
GET /api/proactive_check?user_id=elder_001
```

- 第一版默认仍使用旧全局状态。
- 后续 `ProactiveAgent` 接收 `elder_user_id`，读取用户命名空间状态。

验收：

- 旧调用不传 `user_id` 仍返回。
- 新调用能在日志和返回中体现 `user_id`。

### P1.3 清理 AntiFraudAgent.arun 重复定义

问题：

- `src/agents/antifraud_agent.py` 中 `arun` 重复定义，后者覆盖前者。

方案：

- 删除重复定义，只保留一个。
- 补测试：构造 fake workflow，确认 `arun` 返回 workflow 结果。

验收：

- `python -m py_compile src/agents/antifraud_agent.py` 通过。
- 单测通过。

## 5. P2 修复方案

### P2.1 全局数据迁移

方案：

- 保留旧文件作为 `elder_001` 初始数据。
- 新增迁移脚本：
  - `scripts/migrate_global_data_to_users.py`
- 迁移：
  - `data/user_profile.json` -> `data/users/elder_001/profile.json`
  - `data/chat_history.json` -> `data/users/elder_001/chat_history.json`
  - `data/emotion_log.json` -> `data/users/elder_001/emotion_log.json`
  - `data/agent_status.json` -> `data/users/elder_001/agent_status.json`

### P2.2 Chroma metadata 隔离

方案：

- 新写入的 memory/event documents 增加 `elder_user_id` metadata。
- 检索时使用 Chroma filter。
- 旧数据没有 `elder_user_id` 时只给默认用户 `elder_001` 使用。

## 6. 推荐实施顺序

1. `DataStore` + schemas。
2. `SafetyPolicy`。
3. `AssessmentService` + crisis family/community alerts 落盘。
4. Orchestrator 输出 `risk_detail` 并接 SafetyPolicy。
5. `profile/proactive_check` 增加 `user_id`。
6. 清理 `AntiFraudAgent.arun`。
7. family/community/action_complete 接口。
8. 子女端 SSE Agent。
9. 数据迁移和 Chroma 隔离。

每个目标点的检查命令、预期结果和失败信号见：

- `docs/incremental_update_plan.md`
