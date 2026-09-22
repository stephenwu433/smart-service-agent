# 2 号样例对话

来源：2 号在开发中期发给 3 号的对话样例，用于明确 A1289 主路径预期行为。
也是 `docs/product/demo-http-run.md` 里真实运行的对照脚本。

## 预期对话流

| 轮 | 用户 | 系统 | 状态 | 内部字段 |
|---|---|---|---|---|
| 1 | A1289 接 C1 充不进去 | 继续检查前，请先确认设备是否有鼓包、异味、冒烟、进液或异常发热？ | ASK | `confirmation_type=safety_precheck` |
| 2 | 没有 | 接通电源后，屏幕是否有显示？输入功率是多少？ | ASK | stage -> R03 |
| 3 | 屏幕一直 0W | 请保持当前充电器和线材不变，更换一个确认有电的插座测试。 | GUIDE | stage=R04, 自动建 Attempt |
| 4a | 我之前说错了，我接的是 C2，不是 C1 | 你之前说接的是 C1，现在说是 C2。确认更正吗？回复「确认」即更正，回复「取消」保持原样。 | ASK | `confirmation_type=fact_correction`，`pending_confirmation.type=fact_correction_pending` |
| 4b | 确认 | 收到，已更正为 C2。刚才的插座测试先不用做，因为 A1289 自身充电需要使用 C1。请改接 C1，其他条件保持不变，再观察屏幕是否出现输入功率。 | GUIDE | 撤回依赖 C1 的 Attempt，新增依赖 C2 的 Attempt |
| 5 | 改接 C1 后有输入了 | 请继续观察一会儿，确认输入功率是否能够保持稳定，没有再次变成 0W 或中断。 | GUIDE | `confirmation_type=resolved_check` |
| 6 | 持续有输入，没有中断 | 已记录设备在改接 C1 后恢复稳定输入，本次排障结束。 | RESOLVE | `a1289_stage=__clear__` |

## 关键点

1. **事实更正二次确认**（按交接文档第 5 页）。用户明确说「我之前说错了，我接的是 C2，不是 C1」时，
   系统先返回 `state=ASK` 询问是否确认；用户回复肯定词后才更新 Case revision、撤回依赖旧值的
   Attempt，给出改接 C1 的指引。取消词则丢弃 pending，保留旧事实。
2. **`safety_precheck`、`resolved_check` 和 `fact_correction_pending` 都阻塞**，需要用户回复；
   已应用的 `fact_correction` 不阻塞。
3. **`resolved_check` 必须在用户明确说"稳定 / 持续"后才结案**，防止误报恢复。
4. **A1289 结案有专门文案**，不走通用 `RESOLVE` 分支的"根据已审核的使用指引："。

## 对照测试

- `test_a1289_first_turn_asks_safety_precheck`
- `test_a1289_safety_cleared_advances_to_r03`
- `test_a1289_r03_advances_to_r04`
- `test_a1289_fact_correction_revises_and_withdraws`
- `test_a1289_resolved_check_after_recovery`
- `test_a1289_resolved_check_concludes_case`
