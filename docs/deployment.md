# 部署与恢复

当前交付是可部署的单体 FastAPI backend。首次部署与最终冷启动应使用相同的版本、配置模板和启动
命令，不把开发机已有状态当作部署成功。

## 冷启动

1. 运行 `scripts/bootstrap.sh` 创建 `.venv` 并安装锁定范围内的 dependency。
2. 在 `.env` 或 runtime environment 注入 `MONGODB_URI`；不得把 credential 写入 repository。
3. 运行 `scripts/check.sh`，确认 lint、format 和全部 test 通过。
4. 启动 MongoDB，然后运行 `scripts/dev.sh config/production.env`。
5. 检查 `/health`、`/ui`，再走通创建会话、Case、Attempt、转人工和 Ticket 结果链路。

也可以构建不包含 `.env` 和本地数据的 image：

```bash
docker build -t smart-service-agent:local .
docker run --rm -p 8000:8000 \
  -e MONGODB_URI=mongodb://host.docker.internal:27017 \
  smart-service-agent:local
```

production 中应通过 secret manager 注入 `MONGODB_URI`，并将 image 固定到经过验证的 digest，作为
明确 rollback point。

`/health` 不依赖 MongoDB，因此只能证明 process 可响应。数据库可用性必须通过一次实际业务写入验证。

## 恢复和回滚

- application code 回退到最近一次通过 `scripts/check.sh` 的版本，保留 MongoDB 数据，不执行破坏性清理。
- Case revision、Attempt 和 Ticket 字段都有安全默认值，旧会话可以继续读取。
- MongoDB 不可用时 API 返回不包含连接详情的 `503`；不得在 UI 中显示成功。
- 真实 LLM、RAG、订单、图片或认证尚未接入时，继续使用 deterministic fallback 或显式
  `HANDOFF`，不得伪造 online result。

## 发布阻断项

鉴权、RBAC、TLS、正式客服身份、备份、并发认领和 external integration 在 production 开放前仍是
阻断项。五屏前端和兼容 workspace 只用于内部联调和 Demo，不能直接暴露到公网。
