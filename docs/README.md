# 项目文档

详细项目文档统一放在此目录，并在新增文档时维护本索引。

## 当前文档

- [开发环境](development.md)：config、data、sandbox 和 scripts 的使用方式。
- [Configuration](configuration.md)：environment variable、加载顺序与安全边界。
- [比赛 MVP API](api.md)：消费者、人工客服与品牌洞察 endpoint、状态语义和 error behavior。
- [五屏原型前端](ui.md)：`GET /ui` 的五屏 Demo、调用的 API、error case 与 Mock 边界。
- [比赛 MVP Backend Architecture](architecture.md)：请求链路、状态机、持久化、provider 边界和
  production 接入路线。
- [MongoDB](mongodb.md)：本地启动、collection、index 与 production 安全要求。
- [部署与恢复](deployment.md)：启动检查、稳定版本、恢复和降级边界。

## 后续文档

在对应能力实际存在时，再建立 `deployment.md`、`integrations/` 或
`migrations/`，不要创建空 placeholder。
