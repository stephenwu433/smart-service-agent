# 五屏原型前端

`GET /ui` 提供 A1289 报名验证版的可点击前端，按原型五屏组织，并直接调用现有 FastAPI：

1. 开始咨询
2. 追问与待确认
3. 当前一步 / 执行反馈
4. 风险停止与模拟转人工
5. 人工客服工作台

静态文件位于 `src/smart_service_agent/web/`，由 `ui_assets.py` 按白名单提供 `index.html`、
`styles.css` 和 `app.js`。不引入 React、npm 或其他 frontend framework。

## 访问

服务启动后打开：

- 五屏前端：<http://127.0.0.1:8000/ui>
- 兼容入口：<http://127.0.0.1:8000/workspace/consumer>
  与 <http://127.0.0.1:8000/workspace/agent>

页面顶部固定显示「模拟工单 / 演示环境，尚未连接安克官方客服系统」。

宽屏下五屏同时可见；窄屏一次只显示一屏，可用顶部导航切换。`/workspace/agent` 会直接打开人工工作台。

## Input / Output

消费者操作只提交：

- `POST /v1/conversations` 与 `POST /v1/conversations/{id}/messages`：用户原话、补充、观察与确认。
  请求固定带 `product: "Anker 737 Power Bank A1289"`。
- `PATCH /v1/conversations/{id}/attempts/{attempt_id}`：记录执行、跳过或配件不可用；随后仍发消息让
  编排器推进。
- `POST /v1/conversations/{id}/handoff`：同意或暂不模拟转人工。

人工工作台读取：

- `GET /v1/agent/events`
- `GET /v1/agent/conversations/{id}`
- `POST /v1/agent/events/{event_id}/actions`

页面同时读取 `GET /v1/conversations/{id}/case` 和 `.../attempts`，展示已确认事实与历史尝试。

## Error case

- 空白消息不会提交。
- API `4xx/5xx` 会显示在页面错误条，不展示 stack trace 或 secret。
- 未知静态文件和路径穿越返回 `404`。

## 行为边界

- 前端只提交用户原话、观察和客服动作，不在浏览器里拼 `ASK` / `GUIDE` / `BLOCK` / `HANDOFF` 状态机。
- 三条演示故事按钮只预填用户原话，路径仍由后端 A1289 规则决定。
- 未接入登录鉴权；该页面只用于内部联调和报名验证 Demo。
- 转人工只创建模拟事件，不得显示为真实安克工单。

原型图 ZIP 未进入本仓库；页面结构按冻结表五屏与消费者/人工端统一用语实现，而不是像素级还原。
