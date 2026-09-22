# Anker Smart Service Agent

面向充电设备售后的 AI service intelligence backend，聚焦故障定位、单步排障引导、
风险识别和带上下文的人工升级。
当前实现采用 FastAPI、MongoDB、可替换的 `IntentProvider`/`KnowledgeProvider` 和确定性安全
fallback，可在没有外部 LLM 的情况下运行完整演示流程。

## 环境要求

- Python 3.9+

## 本地开发

```bash
scripts/bootstrap.sh
scripts/mongo-dev.sh  # 在独立 terminal 启动 MongoDB
scripts/dev.sh
```

服务启动后可访问：

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>

## 已实现能力

- 消费者多轮咨询及 `GUIDE`、`RESOLVE`、`ASK`、`HANDOFF`、`BLOCK` 状态编排；
  `GUIDE` 用于充电设备无法充电单条件排查，`RESOLVE` 用于有审核依据的使用与选型咨询。
- Case 事实版本、更正与未知项，以及建议执行、跳过、观察和结果相互独立的 Attempt 记录；
  A1289 主流程会根据用户反馈自动更新 Attempt，并避免重复建立相同步骤。
- 当前消息优先的风险识别，支持常见否定、假设、第三方主体和已恢复表达。
- 可注入的 LLM 意图识别边界，以及 timeout、非法输出和低置信度 fallback。
- 可注入的 RAG/知识库边界，以及知识可回答性过滤和可追踪 evidence。
- MongoDB 会话、审计、反馈、幂等人工事件和客服动作持久化。
- 人工客服视图、事件队列和基础服务洞察 API。
- Ticket 人工回复、动作完成、用户确认解决和重开事件，以及消费者/客服最小 web workspace。
- 可审计 Eval：数据指纹、`GOLD` / `CHALLENGE` / `HOLDOUT` 隔离、风险门禁和 `run_id` 报告。

当前附件只进行 metadata 校验；系统会明确告知无法读取内容并建议转人工。订单系统、真实文件
存储、登录鉴权和 webhook 等 external integration 尚未选型，不会在演示中伪造已接入状态。

完整 API contract 与演示边界见
[比赛 MVP API](docs/api.md)，实现结构见
[比赛 MVP Backend Architecture](docs/architecture.md)。

## 质量检查

```bash
scripts/check.sh
```

## 项目结构

```text
src/smart_service_agent/  # 应用代码
tests/                    # 自动化测试
docs/                     # 详细项目文档
config/                   # environment 配置
data/                     # 本地数据分层
sandbox/                  # 本地实验区
scripts/                  # 开发与验证脚本
```

## 开发规范

仓库级开发要求见 [AGENTS.md](AGENTS.md)，版本变更记录见
[CHANGELOG.md](CHANGELOG.md)，详细文档索引见 [docs/README.md](docs/README.md)。

config、data、sandbox 和 scripts 的完整说明见
[开发环境文档](docs/development.md)。

Eval 的数据隔离、发布门禁和运行方式见 [可审计服务 Eval](docs/evaluation.md)。

## 当前边界

内置规则与知识仅用于 deterministic demo，不代表 production 模型效果、真实商品知识或正式客服
SLA。上线前仍需接入真实 LLM/RAG、认证与 RBAC、文件处理、订单/售后系统、通知 webhook、分页、
事件认领和并发控制；具体风险和接入顺序见
[Backend Architecture](docs/architecture.md#production-接入路线)。

## 来源与边界

本仓库的通用服务架构最初由团队的
[`ttchu1221/Loreal-ai-service-intelligence`](https://github.com/ttchu1221/Loreal-ai-service-intelligence)
`feat/backend` 分支抽取后重新适配。当前运行时规则、知识、示例和测试均面向充电设备；
内置充电故障知识仅用于可重放 demo，不代表安克官方售后政策、产品数据或 SLA。

数据指纹、分层验证、风险门禁和可审计运行方法由团队的
[`stylewth/Meijian-Narrative-Intelligence`](https://github.com/stylewth/Meijian-Narrative-Intelligence)
方法适配；未复制梅见品牌语料、候选叙事、飞书表数据或历史运行产物。
