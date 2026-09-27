from pathlib import Path

from smart_service_agent.demo_backend import STORIES, DemoBackend, panel_for_state


def test_demo_backend_story1_enters_ask() -> None:
    demo = DemoBackend()
    body = demo.send_message(STORIES["故事1 接口更正"])
    assert body.state.value == "ASK"
    assert body.confirmation_required is True
    assert demo.conversation_id
    assert panel_for_state(body.state.value) == "ask"


def test_demo_backend_story3_blocks_then_mock_handoff() -> None:
    demo = DemoBackend()
    blocked = demo.send_message(STORIES["故事3 途中风险"])
    assert blocked.state.value == "BLOCK"
    assert panel_for_state(blocked.state.value) == "risk"
    event = demo.decide_handoff(True)
    assert event.event_id.startswith("evt_")
    assert demo.list_events()
    view = demo.agent_view(demo.conversation_id or "")
    assert view is not None
    assert "鼓包" in " ".join(view.handoff_package.original_messages)


def test_demo_backend_reset_clears_session() -> None:
    demo = DemoBackend()
    demo.send_message(STORIES["故事1 接口更正"])
    demo.reset()
    assert demo.conversation_id is None
    assert demo.transcript == []


def test_streamlit_entrypoint_exists() -> None:
    text = Path("streamlit_app.py").read_text(encoding="utf-8")
    assert "DemoBackend" in text
    assert "模拟工单 / 演示环境，尚未连接安克官方客服系统" in text
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "streamlit>=" in requirements
