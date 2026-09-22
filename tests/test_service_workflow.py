from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure

from smart_service_agent.main import create_app
from smart_service_agent.repository import MemoryRepository


def make_client(tmp_path) -> TestClient:
    del tmp_path
    return TestClient(create_app(MemoryRepository()))


def test_low_risk_usage_question_resolves_with_traceable_evidence(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/v1/conversations", json={"message": "第一次使用移动电源，应该怎么用？"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "RESOLVE"
    assert body["evidence"][0]["knowledge_id"] == "KB-USAGE-001"
    assert "intent" not in body
    assert "risk_level" not in body


def test_model_question_asks_once_then_resolves_without_repeating_fact(tmp_path) -> None:
    client = make_client(tmp_path)
    first = client.post("/v1/conversations", json={"message": "我想选充电器型号"}).json()

    assert first["state"] == "ASK"
    assert first["message"].count("？") <= 1

    second = client.post(
        f"/v1/conversations/{first['conversation_id']}/messages",
        json={"message": "我是 iPhone 15，需要 USB-C 快充"},
    )
    assert second.status_code == 200
    assert second.json()["state"] == "RESOLVE"

    agent_view = client.get(f"/v1/agent/conversations/{first['conversation_id']}").json()
    facts = agent_view["empathy_card"]["confirmed_facts"]
    assert facts == ["我想选充电器型号", "我是 iPhone 15，需要 USB-C 快充"]


def test_high_risk_flow_blocks_recommendation_and_creates_idempotent_handoff(tmp_path) -> None:
    client = make_client(tmp_path)
    blocked = client.post(
        "/v1/conversations", json={"message": "充电器持续异常发热并有异味"}
    ).json()

    assert blocked["state"] == "BLOCK"
    assert blocked["evidence"] == []
    assert "停止使用并断开电源" in blocked["message"]
    assert "confirm_handoff" in blocked["available_actions"]

    path = f"/v1/conversations/{blocked['conversation_id']}/handoff"
    payload = {"accepted": True, "idempotency_key": "risk-case-001"}
    first_event = client.post(path, json=payload)
    repeated_event = client.post(path, json=payload)

    assert first_event.status_code == 200
    assert first_event.json()["event_id"] == repeated_event.json()["event_id"]
    assert first_event.json()["priority"] == 100

    agent_view = client.get(f"/v1/agent/conversations/{blocked['conversation_id']}").json()
    handoff = agent_view["handoff_package"]
    assert handoff["original_messages"] == ["充电器持续异常发热并有异味"]
    assert handoff["confirmed_facts"]
    assert handoff["risk_level"] == "high"
    assert handoff["rule_version"] == "risk-rules-v1"


def test_unknown_knowledge_fails_explicitly_to_handoff(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post("/v1/conversations", json={"message": "查询一个知识库没有的设备故障"})

    assert response.status_code == 201
    assert response.json()["state"] == "HANDOFF"
    assert "没有足够的已审核依据" in response.json()["message"]


def test_feedback_service_action_and_insights_are_auditable(tmp_path) -> None:
    client = make_client(tmp_path)
    resolved = client.post("/v1/conversations", json={"message": "第一次使用产品怎么用？"}).json()
    feedback_response = client.post(
        f"/v1/conversations/{resolved['conversation_id']}/feedback",
        json={"result_id": resolved["result_id"], "resolved": True, "comment": "已解决"},
    )
    assert feedback_response.status_code == 204

    blocked = client.post("/v1/conversations", json={"message": "充电时出现异味"}).json()
    event = client.post(
        f"/v1/conversations/{blocked['conversation_id']}/handoff",
        json={"accepted": True, "idempotency_key": "service-action-001"},
    ).json()
    action_response = client.post(
        f"/v1/agent/events/{event['event_id']}/actions",
        json={"action": "close", "parameters": {"note": "人工已完成跟进"}},
    )
    assert action_response.json()["status"] == "completed"

    insights = client.get("/v1/insights/overview").json()
    assert insights["consultation_count"]["value"] == 2
    assert insights["consultation_count"]["sample_size"] == 2
    assert insights["consultation_count"]["data_classification"] == "demo"
    assert insights["resolution_rate"]["value"] == 1.0


def test_rejects_blank_input_and_unknown_resources(tmp_path) -> None:
    client = make_client(tmp_path)

    assert client.post("/v1/conversations", json={"message": "   "}).status_code == 422
    assert client.get("/v1/events/missing").status_code == 404
    assert client.get("/v1/insights/overview?data_classification=real").status_code == 422
    assert (
        client.post("/v1/conversations/missing/messages", json={"message": "补充信息"}).status_code
        == 404
    )


def test_handoff_idempotency_key_is_scoped_to_conversation(tmp_path) -> None:
    client = make_client(tmp_path)
    conversations = [
        client.post("/v1/conversations", json={"message": message}).json()
        for message in ("充电器冒烟", "充电器鼓包")
    ]

    events = [
        client.post(
            f"/v1/conversations/{conversation['conversation_id']}/handoff",
            json={"accepted": True, "idempotency_key": "same-client-key"},
        ).json()
        for conversation in conversations
    ]

    assert events[0]["event_id"] != events[1]["event_id"]
    assert events[0]["conversation_id"] != events[1]["conversation_id"]


def test_database_failure_returns_safe_actionable_error(tmp_path) -> None:
    del tmp_path

    class FailingRepository(MemoryRepository):
        def save_conversation(self, conversation) -> None:
            del conversation
            raise ConnectionFailure("secret-host.example:27017 unavailable")

    client = TestClient(create_app(FailingRepository()))
    response = client.post("/v1/conversations", json={"message": "第一次使用怎么用？"})

    assert response.status_code == 503
    assert response.json() == {"detail": "database temporarily unavailable"}
    assert "secret-host" not in response.text


def test_old_risk_message_does_not_block_a_new_unrelated_turn(tmp_path) -> None:
    client = make_client(tmp_path)
    first = client.post("/v1/conversations", json={"message": "充电器冒烟"}).json()

    second = client.post(
        f"/v1/conversations/{first['conversation_id']}/messages",
        json={"message": "我现在想问订单退款"},
    ).json()

    assert second["state"] == "HANDOFF"
    card = client.get(f"/v1/agent/conversations/{first['conversation_id']}").json()["empathy_card"]
    assert card["intent"] == "after_sales"
    assert card["risk_level"] == "low"


def test_negated_hypothetical_and_third_party_risks_do_not_false_block(tmp_path) -> None:
    client = make_client(tmp_path)

    for message in ("我没有异味，只想问怎么用", "这个会不会异常发热？", "我朋友的充电器鼓包"):
        body = client.post("/v1/conversations", json={"message": message}).json()
        assert body["state"] != "BLOCK", message


def test_safety_qualifiers_do_not_hide_a_new_active_risk(tmp_path) -> None:
    client = make_client(tmp_path)

    for message in ("其他设备的充电器冒烟", "之前异常发热已经好了，但现在又有异味"):
        body = client.post("/v1/conversations", json={"message": message}).json()
        assert body["state"] == "BLOCK", message


def test_unsupported_repair_is_not_answered_by_generic_usage_article(tmp_path) -> None:
    client = make_client(tmp_path)

    body = client.post("/v1/conversations", json={"message": "充电器拆机维修怎么做"}).json()

    assert body["state"] == "HANDOFF"
    assert body["evidence"] == []


def test_overlapping_after_sales_intent_has_priority(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "充电器买错了怎么退货"}).json()
    card = client.get(f"/v1/agent/conversations/{body['conversation_id']}").json()["empathy_card"]

    assert card["intent"] == "after_sales"
    assert card["scenario"] == "after_sales_service"
    assert body["state"] == "HANDOFF"


def test_attachment_is_explicitly_not_treated_as_processed(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post(
        "/v1/conversations",
        json={
            "message": "帮我看看这张图",
            "attachments": [{"kind": "product_image", "filename": "photo.jpg"}],
        },
    ).json()

    assert body["state"] == "HANDOFF"
    assert "无法读取" in body["message"]


def test_declining_non_risk_handoff_does_not_show_device_safety_warning(tmp_path) -> None:
    client = make_client(tmp_path)
    conversation = client.post("/v1/conversations", json={"message": "查询一个未知产品"}).json()
    response = client.post(
        f"/v1/conversations/{conversation['conversation_id']}/handoff",
        json={"accepted": False, "idempotency_key": "decline-unknown"},
    ).json()

    assert "断电" not in response["message"]
    assert "缺少可靠依据" in response["message"]
