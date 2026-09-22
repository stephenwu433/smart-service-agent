# Changelog

## [Unreleased] — A1289 P0 workflow

### Added

- Cross-turn risk lock on `StoredConversation` (`risk_lock`,
  `risk_lock_reason`, `risk_lock_source`). Once a safety risk is detected,
  subsequent turns stay in `BLOCK` until an unrelated after-sales topic
  is raised. Manual release by an agent is not yet implemented.
- Liquid-ingress and plastic-burn risk language variants (`进液`,
  `洒到`, `烫得`, `烤焦`, etc.).
- A1289 knowledge entries (`KB-A1289-CHARGING-001`,
  `KB-A1289-PORT-001`). Demo source, not official policy.
- `AttemptRecord.depends_on` records the Case fact fields and revisions
  each attempt depends on, e.g.
  `{"charging_port": {"value": "C1", "revision": 1}}`.
- `AttemptRecord.status` / `withdrawn_reason` and partial rollback in
  `revise_case` / `_apply_correction`: only attempts bound to the changed
  fact are withdrawn; unrelated facts and history are preserved.
- A1289 conversational flow with `a1289_stage` stages R03–R07 plus an X
  accessory question, matching 规则表 Sheet2 (both / charger only /
  cable only / none).
- `EmpathyCard.confirmation_type` (`safety_precheck`,
  `fact_correction`, `resolved_check`) and `pending_confirmation`.
- Fact correction is accepted directly (per 2号 sample); the system
  rewrites the case revision, withdraws affected attempts, writes an
  audit entry, and continues from R04.
- MA01 detection: contradictory port / display claims in one message
  trigger a clarification ASK instead of a silent correction.
- NB01: output-direction complaints (`接手机`, `给手机`, …) exit the
  A1289 self-charge flow. NB02: non-A1289 models never enter it.
- `HandoffPackage` now carries `executed_attempts`, `skipped_attempts`,
  `withdrawn_attempts`, `observations`.
- `outcome="resolved"` accepted on attempt update.

### Tests

- 30 new P0 tests in `tests/test_a1289_workflow.py`; total 64 passing.
- All original 34 tests preserved unchanged.

### Design decisions

- Risk lock follows D09 strictly: a locked conversation stays in `BLOCK`
  even if the user switches to an unrelated topic. It is released only by
  `POST /v1/agent/conversations/{cid}/risk-lock/release`. The original
  `test_old_risk_message_does_not_block_a_new_unrelated_turn` was replaced
  by `test_risk_lock_survives_unrelated_turn_until_manual_release` to match
  D09.
- No real LLM / RAG, file processing, order system, auth or RBAC
  integration in this iteration.


All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 从团队既有服务架构抽取全新的充电设备售后 demo。
- 新增无法充电的关键追问、单步排障、异常发热/冒烟/异味/鼓包风险退出与人工升级。
- 适配梅见项目的数据指纹、Gold/Challenge/Holdout 隔离、run_id 与发布门禁方法。
- Added repository-wide development instructions and documentation conventions.
- Added the initial FastAPI service skeleton and health endpoint.
- 新增 development、test 和 production 的 config 模板与类型化 runtime settings。
- 新增本地 sandbox、data 分层和可重复执行的开发 scripts。
- 新增 config 与本地开发流程文档。
- 新增比赛 MVP 的消费者咨询、四状态编排、风险守卫、知识依据和反馈 API。
- 新增 MongoDB 会话、审计、幂等人工事件、客服动作和品牌洞察持久化能力。
- 新增人工客服队列与完整接管包 API，并保留消费者端内部标签隔离。
- 新增比赛 MVP API、architecture、演示边界和 error behavior 文档。
- 新增 MongoDB 本地启动脚本、collection/index 文档和 database unavailable 安全降级。
- 新增可注入的 `IntentProvider` 和规则 fallback；外部模型异常、非法输出或低置信度时自动降级，高风险安全规则始终优先执行。
- 新增可注入的 `KnowledgeProvider` interface，为后续真实 RAG provider 保留稳定边界。
- 新增充电设备无法充电 `GUIDE` 短链路、可更正 Case、独立 Attempt 执行记录和带版本 Ticket 结果事件。
- 新增消费者与人工客服最小 web workspace，以及部署恢复说明。
- 新增非 root container deployment package、health check 和 secret/data 排除规则。

### Changed

- Defined Chinese as the default language for non-technical communication while keeping
  established technical terms in English.
- 调整对话理解为当前消息优先，避免历史风险词污染后续轮次，并修正交易与使用等重叠意图优先级。
- 回复会按意图选择文案；未接入文件处理时明确告知附件不可读取，设备风险回复统一为断电、停止使用、禁止拆机与人工跟进。
- 更新 README、API 和 architecture 文档，补充当前能力、请求链路、external integration 边界及
  production 分阶段接入路线，并修正过时的客服审计主体描述。
- 人工回复、动作完成、用户确认解决和重开改为独立 Ticket 事件，避免虚假完成。

### Fixed

- 修复否定、假设、第三方主体和已恢复风险描述被简单关键词误判为高风险的问题。
- 修复通用知识因单个关键词命中而错误回答拆机维修、电池更换或订单问题的问题。
- 修复固定场景、固定推断和伪造 `demo_agent` 审计主体造成的误导。

## [0.1.0] - 2026-09-09

### Added

- Initialized the project repository.
