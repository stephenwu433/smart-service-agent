# Streamlit 公开 Demo

`127.0.0.1:8000/ui` 只绑定本机，别人的浏览器打不开。公开演示改用 Streamlit Community Cloud：
同一套 A1289 编排器，页面改成 Streamlit 五屏，数据用 `MemoryRepository`，不依赖本机 FastAPI 或 MongoDB。

## 别人怎么打开

仓库维护者在 [Streamlit Community Cloud](https://share.streamlit.io/) 用 GitHub 登录后：

1. New app
2. Repository：`stephenwu433/smart-service-agent`
3. Branch：要分享的分支（例如 `main` 或 `cursor/a1289-ui-five-panels-a6f9`）
4. Main file path：`streamlit_app.py`
5. Deploy

部署成功后地址形如 `https://<app-name>.streamlit.app`。把这个 URL 发给别人即可。

Streamlit Cloud 会安装仓库根目录的 [`requirements.txt`](../requirements.txt)。入口文件会把 `src/` 加入 `sys.path`，因此不需要先 `pip install` 本包。

## 本地预览

```bash
bash scripts/bootstrap.sh
bash scripts/streamlit-demo.sh
```

浏览器打开 <http://127.0.0.1:8501>。这仍是本机地址；要给别人用，必须走上面的 Community Cloud 部署。

## 和 FastAPI `/ui` 的关系

| | FastAPI `/ui` | Streamlit Demo |
|---|---|---|
| 入口 | `src/smart_service_agent/web/` | `streamlit_app.py` |
| 后端 | 同一套 `ConversationOrchestrator` | 同一套，经 `demo_backend.py` |
| 存储 | 本地 MongoDB（`config/development.env`） | 每个浏览器会话一份 MemoryRepository |
| 谁能打开 | 只有跑服务的那台机器 | Cloud 部署后公网可打开 |
| 鉴权 | 无 | 无；只适合报名验证 Demo |

不要把 Streamlit Demo 当成 production 客服系统，也不要把它解释为已连接安克官方工单。
