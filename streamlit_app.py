from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import streamlit as st  # noqa: E402

from smart_service_agent.demo_backend import STORIES, DemoBackend, panel_for_state  # noqa: E402

st.set_page_config(page_title="安克智能服务助手（比赛演示）", layout="wide")


def backend() -> DemoBackend:
    if "backend" not in st.session_state:
        st.session_state.backend = DemoBackend()
    return st.session_state.backend


def send(text: str) -> None:
    message = text.strip()
    if not message:
        st.warning("请先填写用户原话。")
        return
    try:
        backend().send_message(message)
    except ValueError as error:
        st.error(str(error))


demo = backend()
last = demo.last
state_name = last.state.value if last else "未开始"

st.warning("模拟工单 / 演示环境，尚未连接安克官方客服系统")
st.caption("Anker 737 · A1289 · Streamlit 公开 Demo")
st.title("安克智能服务助手（比赛演示）")
st.write(
    "只处理 A1289 给充电宝自身充电时充不进去。"
    "这是同一套 FastAPI 编排器的 Streamlit 外壳，供别人通过公网打开；"
    "路径仍由后端 A1289 规则决定。"
)

story_cols = st.columns(len(STORIES) + 1)
for column, (label, seed) in zip(story_cols[:-1], STORIES.items()):
    if column.button(label, use_container_width=True):
        st.session_state.start_message = seed
if story_cols[-1].button("新会话", use_container_width=True):
    demo.reset()
    st.session_state.start_message = ""
    st.rerun()

st.caption(
    f"状态：{state_name}"
    + (" · 风险锁定" if last and last.risk_lock else "")
    + f" · 建议停留 {panel_for_state(last.state.value if last else None)}"
)
if demo.conversation_id:
    st.caption(f"会话 `{demo.conversation_id}`")

tab_start, tab_ask, tab_guide, tab_risk, tab_agent = st.tabs(
    ["1 开始咨询", "2 追问确认", "3 当前一步", "4 风险转人工", "5 人工工作台"]
)

with tab_start:
    st.subheader("开始咨询")
    st.write("描述 A1289 自身充电问题。不要在这里办理退款或给手机充电。")
    start_message = st.text_area(
        "用户原话",
        key="start_message",
        height=140,
        placeholder="例如：A1289 接 C1 充不进去，屏幕 0W",
    )
    if st.button("开始排查", type="primary"):
        send(start_message)
        st.rerun()

with tab_ask:
    st.subheader("追问与待确认")
    st.write(
        f"处理进度：{state_name}" + (" · 需要确认" if last and last.confirmation_required else "")
    )
    if last and last.confirmation_required:
        st.info(last.message)
    else:
        st.write("暂无待确认项" if not (last and last.reasons) else "\n".join(last.reasons))
    facts = demo.current_facts()
    st.write("已确认事实：")
    st.write("\n".join(f"- {item}" for item in facts) if facts else "- 还没有已确认事实")
    ask_message = st.text_area(
        "补充或更正",
        key="ask_message",
        height=100,
        placeholder="一次只补充一项；不确定就写不确定",
    )
    ask_cols = st.columns(3)
    if ask_cols[0].button("发送", type="primary"):
        send(ask_message)
        st.rerun()
    if ask_cols[1].button("记为不确定"):
        send("不确定")
        st.rerun()
    if ask_cols[2].button("没有风险迹象"):
        send("没有")
        st.rerun()

with tab_guide:
    st.subheader("当前一步")
    attempt = demo.current_attempt()
    if attempt and last and last.state.value == "GUIDE":
        st.success(attempt.instructions or attempt.recommendation)
        st.write(f"观察：{attempt.observation_target}")
        st.write(f"停止条件：{attempt.exit_condition}")
    elif last:
        st.write(last.message)
    else:
        st.write("进入 GUIDE 后才会出现有依据的单步建议。")
    evidence = last.evidence if last else []
    st.write("知识依据：")
    if evidence:
        for item in evidence:
            st.write(f"- {item.knowledge_id}：{item.excerpt}")
    else:
        st.write("- 本轮没有可展示的知识依据")
    observe = st.text_area(
        "执行反馈 / 观察结果",
        key="observe_message",
        height=100,
        placeholder="例如：改接 C1 后有输入了 / 换插座后仍是 0W",
    )
    guide_cols = st.columns(4)
    if guide_cols[0].button("已执行并观察", type="primary"):
        demo.report_attempt("executed", observe)
        st.rerun()
    if guide_cols[1].button("已恢复"):
        demo.report_attempt("improved", observe)
        st.rerun()
    if guide_cols[2].button("跳过此步"):
        demo.report_attempt("skipped", observe)
        st.rerun()
    if guide_cols[3].button("没有配件"):
        demo.report_attempt("skipped_unavailable", observe)
        st.rerun()
    if last and last.confirmation_required and last.state.value == "GUIDE":
        confirm_cols = st.columns(2)
        if confirm_cols[0].button("持续有输入，没有中断"):
            send("持续有输入，没有中断")
            st.rerun()
        if confirm_cols[1].button("又变成 0W 或中断了"):
            send("又变成 0W 了")
            st.rerun()
    st.write("历史尝试：")
    attempts = demo.attempts()
    if not attempts:
        st.write("- 还没有历史尝试")
    for item in attempts:
        mark = "已撤回" if item.status == "withdrawn" else item.execution_status
        extra = f" · {item.observation}" if item.observation else ""
        st.write(f"- {mark} · {item.recommendation}{extra}")

with tab_risk:
    st.subheader("风险停止与模拟转人工")
    st.error("鼓包、冒烟、异味、进液、异常发热时停止普通排障，不能因用户想继续而恢复测试。")
    if last:
        st.write(last.message)
    else:
        st.write("出现鼓包、冒烟、异味、进液或异常发热后，这里会停止排障。")
    risk_cols = st.columns(2)
    if risk_cols[0].button("同意模拟转人工", type="primary"):
        try:
            demo.decide_handoff(True)
        except ValueError as error:
            st.error(str(error))
        st.rerun()
    if risk_cols[1].button("暂不转人工"):
        try:
            demo.decide_handoff(False)
        except ValueError as error:
            st.error(str(error))
        st.rerun()
    st.caption("转人工后生成模拟事件，不会创建真实安克工单。")

with tab_agent:
    st.subheader("人工客服工作台")
    events = demo.list_events()
    if st.button("刷新队列"):
        st.rerun()
    if not events:
        st.write("队列为空")
    else:
        labels = [f"{item.status} · {item.reason} · {item.event_id}" for item in events]
        chosen = st.selectbox("模拟事件", labels)
        selected = events[labels.index(chosen)]
        view = demo.agent_view(selected.conversation_id)
        pack = view.handoff_package if view else None
        if pack:
            st.write("**原话**")
            st.write(" / ".join(pack.original_messages) or "无")
            st.write("**事实与更正**")
            st.write(pack.confirmed_facts or ["无"])
            st.write("**尝试及结果**")
            recs = []
            for item in pack.attempts or pack.executed_attempts:
                if isinstance(item, dict):
                    recs.append(str(item.get("recommendation", item)))
                else:
                    recs.append(str(item))
            st.write(recs or ["无"])
            st.write("**升级原因 / 待处理问题**")
            st.write(pack.suggested_next_step or pack.summary)
            unresolved = pack.unresolved_items or pack.untested_items
            st.write("未排除：" + ("，".join(unresolved) if unresolved else "无"))
        action = st.selectbox(
            "处理动作",
            ["reply", "request_material", "create_after_sales", "escalate_expert", "close"],
        )
        note = st.text_area("处理说明", key="agent_note", height=80)
        if st.button("记录动作", type="primary"):
            demo.execute_action(selected.event_id, action, note)
            st.rerun()

st.divider()
st.subheader("对话与进度")
if not demo.transcript:
    st.write("尚未开始")
else:
    for item in demo.transcript:
        st.markdown(f"**{item['role']}** {item['text']}")
