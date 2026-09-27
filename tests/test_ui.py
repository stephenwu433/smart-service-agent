import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from smart_service_agent.main import create_app
from smart_service_agent.repository import MemoryRepository
from smart_service_agent.ui_assets import ui_file


def make_client() -> TestClient:
    return TestClient(create_app(MemoryRepository()))


def test_ui_five_panels_are_served() -> None:
    client = make_client()
    page = client.get("/ui")
    assert page.status_code == 200
    assert "安克智能服务助手（比赛演示）" in page.text
    assert "模拟工单 / 演示环境，尚未连接安克官方客服系统" in page.text
    assert "Panel 1" in page.text
    assert "Panel 5" in page.text
    assert "开始咨询" in page.text
    assert "追问与待确认" in page.text
    assert "当前一步" in page.text
    assert "风险停止与模拟转人工" in page.text
    assert "人工客服工作台" in page.text
    assert "故事1 接口更正" in page.text
    assert "故事2 配件交叉" in page.text
    assert "故事3 途中风险" in page.text
    assert client.get("/ui/styles.css").status_code == 200
    assert client.get("/ui/app.js").status_code == 200


def test_ui_javascript_calls_existing_api() -> None:
    client = make_client()
    script = client.get("/ui/app.js").text
    assert "fetch(path" in script
    assert "/v1/conversations" in script
    assert "/v1/conversations/${state.conversationId}/messages" in script
    assert "/v1/conversations/${state.conversationId}/attempts/${attempt.attempt_id}" in script
    assert "/v1/conversations/${state.conversationId}/handoff" in script
    assert "/v1/agent/events" in script
    assert "Anker 737 Power Bank A1289" in script


def test_legacy_workspaces_serve_the_same_prototype() -> None:
    client = make_client()
    consumer = client.get("/workspace/consumer")
    agent = client.get("/workspace/agent")
    assert consumer.status_code == 200
    assert agent.status_code == 200
    assert "开始咨询" in consumer.text
    assert "人工客服工作台" in agent.text


def test_ui_rejects_unknown_and_escaped_assets() -> None:
    client = make_client()
    assert client.get("/ui/unknown.txt").status_code == 404
    response = client.get("/ui/../config.py")
    assert response.status_code in {404, 422}


def test_ui_file_helper_rejects_disallowed_names() -> None:
    with pytest.raises(HTTPException) as escaped:
        ui_file("../config.py")
    assert escaped.value.status_code == 404
    with pytest.raises(HTTPException) as unknown:
        ui_file("secret.json")
    assert unknown.value.status_code == 404
    allowed = ui_file("index.html")
    assert str(allowed.path).endswith("index.html")


def test_ui_start_payload_enters_a1289_ask() -> None:
    client = make_client()
    body = client.post(
        "/v1/conversations",
        json={
            "message": "A1289 接 C1 充不进去，屏幕一直 0W。",
            "product": "Anker 737 Power Bank A1289",
        },
    ).json()
    assert body["state"] == "ASK"
    assert body["confirmation_required"] is True
    assert body["conversation_id"]
