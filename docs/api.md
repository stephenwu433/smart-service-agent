# 比赛 MVP API

当前 backend 实现消费者咨询、人工接管和品牌洞察的同一条服务闭环。所有示例知识、事件处理人和
经营数据均属于 `demo`，不得解释为真实品牌业务数据或正式 SLA。

## 消费者端

### 发起和继续咨询

`POST /v1/conversations` 创建会话，`POST /v1/conversations/{conversation_id}/messages`
在同一会话补充信息。request 的主要字段如下：

```json
{
  "message": "第一次使用移动电源，应该怎么用？",
  "product": "演示移动电源",
  "order_reference": "DEMO-ORDER-001",
  "attachments": []
}
```

response 只包含消费者可见的自然语言、状态、依据与可执行动作，不暴露情绪标签、内部风险规则、
模型名称或推理过程。充电设备无法充电场景的 `GUIDE` 和兼容既有咨询的 `RESOLVE` 返回审核知识依据；
`ASK` 每轮只问一个关键问题；无审核依据时显式
进入 `HANDOFF`；命中设备安全风险时进入 `BLOCK`，停止排障并提示断电、停止使用和人工跟进。

每轮风险与意图判断以当前 `message` 为主，历史消息用于保存事实和补充必要的选购上下文，不会把
旧风险描述自动当作当前仍在发生。订单、退款、退换货等复合表达优先进入售后意图；现有 demo
知识不覆盖交易政策、拆机维修或电池更换，因此这些请求不会因为包含“使用”或“充电器”而错误进入
`RESOLVE`。

附件字段当前只校验 `product_image` 或 `order_screenshot` metadata，属于后续文件 provider 的预留入口。
收到附件时 API 会明确告知无法读取内容并请求转人工，不执行
真实图片识别，也不保存文件内容。

### Case 和 Attempt

- `GET /v1/conversations/{conversation_id}/case`：读取消费者原话、当前事实版本和全部修订历史。
- `POST /v1/conversations/{conversation_id}/case/revisions`：追加更正版本，不覆盖历史版本。
- `GET /v1/conversations/{conversation_id}/attempts`：读取建议及执行历史。
- `POST /v1/conversations/{conversation_id}/attempts`：记录一个包含目的、操作、观察点和退出条件的
  单条件建议；相同建议返回 `409`，防止重复尝试。
- `PATCH /v1/conversations/{conversation_id}/attempts/{attempt_id}`：分别记录执行或跳过、观察内容
  和结果；执行过的建议没有观察内容时返回 `422`。

### A1289 自充排查流程（P0）

A1289 是 P0 里唯一进入主排障流程的型号。触发条件：当前 `message` 或 Case 原话包含 `A1289` 或
`737`，且不含对外供电词（`接手机`、`给手机`、`给设备供电`、`对外供电`、`输出`）。不满足触发条件
的请求不进本流程，退到原有的 `RESOLVE` / `HANDOFF` / `BLOCK`。

流程用 `a1289_stage` 字段表达阶段（`R03`、`R04`、`X`、`R05`、`R06`、`R07`），不新增顶层
`ConversationState`。顶层状态仍为 `GUIDE` / `RESOLVE` / `ASK` / `HANDOFF` / `BLOCK`。

`EmpathyCard` 新增字段：

- `confirmation_type`：`safety_precheck` | `fact_correction` | `resolved_check`。
- `pending_confirmation`：含 `type`、`old`、`new`、`withdrawn_attempt_ids`、`new_revision`、`preserved_fact_ids`。
- `next_a1289_stage`：下一个 stage 标识；`__clear__` 表示清空。
- `next_attempt_id`：步骤闭环自动创建的 Attempt ID。

`ConsumerResponse` 新增字段：

- `next_attempt_id`、`confirmation_required`
- `old_fact`、`new_fact`、`affected_attempt_ids`
- `new_revision`、`withdrawn_attempt_ids`、`preserved_fact_ids`

`AttemptRecord` 新增字段：

- `depends_on`：`{field: {"value": v, "revision": r}}`，记录该 attempt 依赖的 Case 事实。
- `status`：`active` | `withdrawn`。
- `withdrawn_reason`：如 `fact_changed: charging_port C1 -> C2`。

`AttemptUpdateRequest` 新增：

- `execution_status` 接受 `skipped_unavailable`。
- `skip_reason`：用户无法执行该步骤时的原因。

`HandoffPackage` 新增：`executed_attempts`、`skipped_attempts`、`withdrawn_attempts`、`observations`。

**人工解除风险锁**：

`POST /v1/agent/conversations/{conversation_id}/risk-lock/release`

    {"reason": "已完成风险处理", "operator": "agent-001"}

响应为更新后的 `StoredConversation`，`risk_lock=false`。D09：普通流程不因用户下一轮否认自动
恢复；只有人工明确解除后才恢复。

**事实更正**（按交接文档第 5 页，二次确认）：

- 跨轮明确更正：用户在同一轮内表达"说错 / 实际是 / 其实"等触发词，且出现新事实值 →
  系统先返回 `confirmation_type=fact_correction` 且
  `pending_confirmation.type=fact_correction_pending`，`state=ASK`，询问"确认更正吗？"。
  用户回复肯定词（`确认` / `是` / `对` / `没错` / `更正` / `好` / `是的` / `确定`）后才更新
  Case revision，撤回依赖旧值的 Attempt，写出 `case_revised` 审计，并给出下一步。
  用户回复取消词（`取消` / `不更正` / `算了` / `不用` / `不对` / `不改`）则丢弃 pending，
  保留旧事实。
- 同轮矛盾：同一消息内出现 ≥2 个端口 + 犹豫词（`不对`、`可能`、`也许`、`一会儿`、`又`、
  `不确定`）→ 返回 `confirmation_type=fact_correction` 且
  `pending_confirmation.type=contradiction`，进入追问 `ASK`，不直接更正。

**附件可选（D05）**：若 `message` 含充不进/无法充电/A1289/737 等关键词，即使带附件也走正常
流程；否则保持原有"无法读取附件 + 转人工"分支。

### 人工交接和反馈

- `POST /v1/conversations/{conversation_id}/handoff`：记录用户是否同意转人工；同意时使用
  `idempotency_key` 幂等创建服务事件。
- `GET /v1/events/{event_id}`：查看等待接管、处理中或已完成状态及演示预计响应时间。
- `POST /v1/conversations/{conversation_id}/feedback`：把是否解决和开放反馈绑定到最新
  `result_id`。反馈只进入待分析数据，不自动训练模型或修改风险规则。

## 人工客服工作台

- `GET /v1/agent/events`：按风险优先级和等待时间返回事件队列。
- `GET /v1/agent/conversations/{conversation_id}`：返回消费者原话、共情卡、事实与推断、缺失
  信息、风险、知识依据、建议下一步和审计轨迹。
- `POST /v1/agent/events/{event_id}/actions`：记录回复、索要材料、建立售后记录、升级专家或关闭
  事件。鉴权接入前审计主体明确记录为 `unauthenticated_agent_api`；生产环境必须用认证身份替换。
- `POST /v1/agent/conversations/{conversation_id}/ticket/results`：分别记录人工回复、动作完成、
  用户确认解决或重开；这些结果不会相互冒充。

`GET /workspace/consumer` 和 `GET /workspace/agent` 提供无额外 frontend dependency 的最小可运行
工作区，用于联调消费者输入和人工队列。它们不包含 production 登录能力。

## 品牌洞察

`GET /v1/insights/overview` 返回咨询量、反馈解决率、转人工率、重复提问率和高频未解决问题。
每项指标包含时间范围、样本数和 `demo`/`real` 属性；默认且当前实际支持的属性为 `demo`。

## 状态和版本

全局状态为 `GUIDE`、`RESOLVE`、`ASK`、`HANDOFF` 和 `BLOCK`。MongoDB 持久化保存状态切换原因、规则版本、
知识版本、结果编号、人工动作和反馈。当前版本由 `SCHEMA_VERSION`、`RULE_VERSION` 和
`KNOWLEDGE_VERSION` 配置。

## 错误行为

- 空白或超过限制的输入返回 `422`。
- 不存在的会话或事件返回 `404`。
- feedback 的 `result_id` 不是该会话最新结果时返回 `409`。
- MongoDB 暂时不可用时返回不包含连接信息的 `503`。
- 无知识命中不会生成产品事实，而是显式请求转人工。

## 尚未实现

- 附件二进制上传、图片识别、语音转文字和真实文件存储。
- 订单/售后系统、企业登录或 SSO、消息通知和客服 webhook。
- 客服队列分页、事件认领、RBAC、optimistic locking 和多人并发冲突处理。

这些能力尚未选定 vendor。当前 API 只保留业务边界，不宣称 external service 已接入。
