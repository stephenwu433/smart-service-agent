# Real HTTP + MongoDB Demo

以下内容来自一次真实 uvicorn + 真实 MongoDB 的 6 轮会话，对应 2 号样例。
不是 TestClient，也不是 MemoryRepository。

## 会话

conversation_id = conv_048b129e96ae425eb8dd97135bd1382a

| 轮 | 用户 | 系统 | 状态 | 关键字段 |
|---|---|---|---|---|
| 1 | A1289 接 C1 充不进去 | 继续检查前，请先确认设备是否有鼓包、异味、冒烟、进液或异常发热？ | ASK | `confirmation_required=True` |
| 2 | 没有 | 接通电源后，屏幕是否有显示？输入功率是多少？ | ASK | |
| 3 | 屏幕 0W | 请保持当前充电器和线材不变，更换一个确认有电的插座测试。 | GUIDE | `next_attempt_id=attempt_0f75...` |
| 4 | 我之前说错了，我接的是 C2，不是 C1 | 收到，已更正为 C2。刚才的步骤先不用做。A1289 自身充电需要使用 C1，请改接 C1... | GUIDE | `old_fact=charging_port:C1` `new_fact=charging_port:C2` `next_attempt_id=attempt_2bf5...` |
| 5 | 改接 C1 后有输入了 | 请继续观察一会儿，确认输入功率是否能够保持稳定，没有再次变成 0W 或中断。 | GUIDE | `next_attempt_id=attempt_4446...` |
| 6 | 持续有输入，没有中断 | 已记录本次 A1289 自充排障结果。如果之后再次出现输入中断，可以继续联系我们。 | RESOLVE | |

## MongoDB 持久化

- conversations: 2
- audit_events: 14
- 会话 conv_048b... 的 messages:
  ["A1289 接 C1 充不进去","没有","屏幕 0W","我之前说错了，我接的是 C2，不是 C1","改接 C1 后有输入了","持续有输入，没有中断"]

## 复现步骤

1. 启动 MongoDB：setsid mongod --dbpath /var/lib/mongo --logpath /var/log/mongodb/mongod.log --fork --bind_ip 127.0.0.1
2. 启动 API：APP_RELOAD=false setsid .venv/bin/python -m smart_service_agent.cli > /tmp/api.log 2>&1 < /dev/null &
3. 跑 Demo：.venv/bin/python /tmp/demo.py
