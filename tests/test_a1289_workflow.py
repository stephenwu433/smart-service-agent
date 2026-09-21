from fastapi.testclient import TestClient

from smart_service_agent.main import create_app
from smart_service_agent.repository import MemoryRepository


def make_client(tmp_path) -> TestClient:
    del tmp_path
    return TestClient(create_app(MemoryRepository()))


def test_liquid_risk_triggers_block(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "充电宝好像进液了"}).json()
    assert body["state"] == "BLOCK"


def test_plastic_burn_smell_triggers_block(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "一插电就有塑料烤焦味"}).json()
    assert body["state"] == "BLOCK"


def test_risk_lock_persists_across_turns(tmp_path) -> None:
    client = make_client(tmp_path)
    first = client.post("/v1/conversations", json={"message": "充电器有异味"}).json()
    assert first["state"] == "BLOCK"

    second = client.post(
        f"/v1/conversations/{first['conversation_id']}/messages",
        json={"message": "那我现在换个充电器试试"},
    ).json()
    assert second["state"] == "BLOCK"


def test_risk_lock_not_released_by_next_turn_denial(tmp_path) -> None:
    client = make_client(tmp_path)
    first = client.post("/v1/conversations", json={"message": "充电器冒烟"}).json()
    assert first["state"] == "BLOCK"

    second = client.post(
        f"/v1/conversations/{first['conversation_id']}/messages",
        json={"message": "现在没有风险了，可以继续吗"},
    ).json()
    assert second["state"] == "BLOCK"


def test_negated_liquid_risk_does_not_block(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "没有进液，只想问怎么用"}).json()
    assert body["state"] != "BLOCK"


def test_attempt_records_field_dependency(tmp_path) -> None:
    client = make_client(tmp_path)
    conv = client.post("/v1/conversations", json={"message": "A1289 无法充电"}).json()
    cid = conv["conversation_id"]
    # 先做一次 case revision，让 case 有 charging_port 事实
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={"facts": {"charging_port": "C1"}, "reason": "用户确认使用 C1"},
    )
    attempt = client.post(
        f"/v1/conversations/{cid}/attempts",
        json={
            "recommendation": "改接 C1 后重试",
            "purpose": "确认 C1 自充",
            "instructions": "使用 C1 和随附 USB-C to USB-C 线重试",
            "observation_target": "是否开始稳定充电",
            "exit_condition": "观察到稳定输入",
        },
    ).json()
    assert attempt["status"] == "active"
    dep = attempt["depends_on"]
    assert "charging_port" in dep
    assert dep["charging_port"]["value"] == "C1"
    assert dep["charging_port"]["revision"] == 2


def test_fact_correction_withdraws_only_affected_attempts(tmp_path) -> None:
    client = make_client(tmp_path)
    conv = client.post("/v1/conversations", json={"message": "A1289 无法充电"}).json()
    cid = conv["conversation_id"]

    # case revision 2: charging_port=C1, cable_model=原装
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={
            "facts": {"charging_port": "C1", "cable_model": "原装"},
            "reason": "初始事实",
        },
    )

    # attempt A 依赖 charging_port（C1）
    a = client.post(
        f"/v1/conversations/{cid}/attempts",
        json={
            "recommendation": "使用 C1 重试",
            "purpose": "确认 C1 自充",
            "instructions": "使用 C1 和随附 USB-C to USB-C 线重试",
            "observation_target": "是否充电",
            "exit_condition": "观察到充电",
        },
    ).json()
    # attempt B 只依赖 cable_model
    b = client.post(
        f"/v1/conversations/{cid}/attempts",
        json={
            "recommendation": "使用另一根线材测试",
            "purpose": "线材交叉测试",
            "instructions": "保持充电器不变，只更换线材",
            "observation_target": "是否充电",
            "exit_condition": "观察到充电",
        },
    ).json()
    assert a["status"] == "active"
    assert b["status"] == "active"
    assert "charging_port" in a["depends_on"]
    assert "charging_port" not in b["depends_on"]
    assert "cable_model" in b["depends_on"]

    # case revision 3: 用户更正 charging_port=C2
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={"facts": {"charging_port": "C2"}, "reason": "用户更正为 C2"},
    )

    attempts = {x["attempt_id"]: x for x in client.get(f"/v1/conversations/{cid}/attempts").json()}
    assert attempts[a["attempt_id"]]["status"] == "withdrawn"
    assert "charging_port" in attempts[a["attempt_id"]]["withdrawn_reason"]
    assert attempts[b["attempt_id"]]["status"] == "active"


def test_a1289_first_turn_asks_safety_precheck(tmp_path) -> None:
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()
    assert body["state"] == "ASK"
    assert "鼓包" in body["message"]
    assert "进液" in body["message"]


def test_a1289_safety_cleared_advances_to_r03(tmp_path) -> None:
    client = make_client(tmp_path)
    first = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()
    second = client.post(
        f"/v1/conversations/{first['conversation_id']}/messages",
        json={"message": "没有"},
    ).json()
    assert second["state"] == "ASK"
    assert "屏幕" in second["message"]


def test_a1289_fact_correction_revises_and_withdraws(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]

    client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "没有"},
    )

    # 手工建一条依赖 charging_port=C1 的 attempt
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={"facts": {"charging_port": "C1"}, "reason": "确认 C1"},
    )
    attempt = client.post(
        f"/v1/conversations/{cid}/attempts",
        json={
            "recommendation": "使用 C1 重试",
            "purpose": "确认 C1 自充",
            "instructions": "使用 C1 和随附线材重试",
            "observation_target": "是否充电",
            "exit_condition": "观察到充电",
        },
    ).json()
    assert attempt["status"] == "active"
    assert "charging_port" in attempt["depends_on"]

    # 用户更正 C1 → C2
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    assert corrected["state"] == "GUIDE"
    assert "已更正为 C2" in corrected["message"]
    assert "改接 C1" in corrected["message"]

    attempts = client.get(f"/v1/conversations/{cid}/attempts").json()
    a = next(x for x in attempts if x["attempt_id"] == attempt["attempt_id"])
    assert a["status"] == "withdrawn"
    assert "charging_port" in a["withdrawn_reason"]


def test_a1289_fact_correction_writes_audit(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={"facts": {"charging_port": "C1"}, "reason": "确认 C1"},
    )
    client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    )

    view = client.get(f"/v1/agent/conversations/{cid}").json()
    audit = view["audit_trail"]
    revision_events = [e for e in audit if e.get("event_type") == "case_revised"]
    assert revision_events
    last = revision_events[-1]
    payload = last
    assert payload.get("old_facts", {}).get("charging_port") == "C1"
    assert payload.get("new_facts", {}).get("charging_port") == "C2"


def test_a1289_r03_advances_to_r04(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕一直 0W"}).json()
    assert body["state"] == "GUIDE"
    assert "插座" in body["message"]


def test_a1289_resolved_check_after_recovery(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "换插座后好了"}).json()
    assert body["state"] == "GUIDE"
    assert "观察" in body["message"]


def test_a1289_resolved_check_concludes_case(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换插座后好了"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "稳定，持续有输入"}
    ).json()
    assert body["state"] == "RESOLVE"


def test_a1289_r04_no_recovery_asks_accessories(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了插座还是不行"}
    ).json()
    assert body["state"] == "ASK"
    assert "充电器" in body["message"]
    assert "线" in body["message"]


def _to_x(client, cid):
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了插座还是不行"})


def test_a1289_accessories_both_goes_r05(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"}).json()
    assert body["state"] == "GUIDE"
    assert "充电器" in body["message"]


def test_a1289_accessories_cable_only_goes_r06(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "只有线"}).json()
    assert body["state"] == "GUIDE"
    assert "线" in body["message"]


def test_a1289_accessories_none_goes_handoff(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "都没有"}).json()
    assert body["state"] == "HANDOFF"


def test_a1289_r05_charger_recovered_triggers_resolved_check(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了充电器后好了"}
    ).json()
    assert body["state"] == "GUIDE"
    assert "观察" in body["message"]


def test_a1289_r05_failed_then_r06(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "还是不行"}).json()
    assert body["state"] == "GUIDE"
    assert "线" in body["message"]


def test_a1289_r06_cable_recovered_triggers_resolved_check(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "还是不行"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了线后好了"}).json()
    assert body["state"] == "GUIDE"
    assert "观察" in body["message"]


def test_a1289_r06_failed_goes_handoff(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "还是不行"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了线还是不行"}
    ).json()
    assert body["state"] == "HANDOFF"


def test_nb01_output_direction_does_not_enter_a1289_self_charge(tmp_path) -> None:
    """NB01: 充电宝无法给手机供电，不属于自充问题，不应进入 A1289 自充流程。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "A1289 接手机后手机不充电"}).json()
    # 不应触发 A1289 自充的 safety precheck（鼓包/进液/冒烟等）
    assert "鼓包" not in body["message"]
    assert "进液" not in body["message"]
    assert "冒烟" not in body["message"]


def test_nb02_non_a1289_does_not_apply_a1289_rules(tmp_path) -> None:
    """NB02: A1259 不套用 A1289 的 C1 输入规则。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "A1259 充不进去"}).json()
    # 不应走 A1289 的 safety precheck（那个只对 A1289 触发）
    assert "A1289" not in body["message"]
    # 不应进入 A1289 的 R03 屏幕功率追问
    assert "接通电源后" not in body["message"]


def test_ma01_internal_contradiction_asks_clarification(tmp_path) -> None:
    """MA01: 同一消息内接口和屏幕描述矛盾，系统应追问而非直接接受。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})

    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={
            "message": "我确定接的是 C1，不对，可能是 C2；屏幕一会儿有显示，一会儿又说一直是 0W。"
        },
    ).json()
    assert body["state"] == "ASK"
    assert "确认" in body["message"]
    # 不应该直接接受更正（不应出现"已更正为"）
    assert "已更正为" not in body["message"]


def test_ma01_cross_turn_correction_still_accepted(tmp_path) -> None:
    """跨轮明确更正仍走直接接受，不误判为 MA01 矛盾。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    assert body["state"] == "GUIDE"
    assert "已更正为 C2" in body["message"]


def test_rv02_liquid_plus_heat_blocks_both_terms(tmp_path) -> None:
    """RV02: 进液 + 异常发热同时出现，必须同时记录两项风险并 BLOCK。"""
    client = make_client(tmp_path)
    body = client.post(
        "/v1/conversations",
        json={"message": "昨天饮料好像洒到接口里了，今天一接电就烫得拿不住"},
    ).json()
    assert body["state"] == "BLOCK"
    view = client.get(f"/v1/agent/conversations/{body['conversation_id']}").json()
    reasons = " ".join(view["empathy_card"]["risk_reasons"])
    # 系统记录命中词本身（洒到、烫得），两者必须都出现
    assert "洒到" in reasons
    assert "烫得" in reasons


def test_ms01_c2_to_c1_recovery_full_flow(tmp_path) -> None:
    """MS01: 用户接 C2，系统指导改接 C1，用户确认恢复。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C2 充不进去"}).json()[
        "conversation_id"
    ]
    # 用户先否认风险
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})

    # 用户更正接口，触发 fact_correction
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    # 更正后系统给出改接 C1 指引
    assert corrected["state"] == "GUIDE"
    assert "C1" in corrected["message"]

    # 用户说恢复
    recovered = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "改接 C1 后有输入了"},
    ).json()
    assert recovered["state"] == "GUIDE"
    assert "观察" in recovered["message"]

    # 用户确认稳定
    done = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "稳定，持续有输入，没有中断"},
    ).json()
    assert done["state"] == "RESOLVE"


def test_ms02_charger_fails_cable_recovers(tmp_path) -> None:
    """MS02: 充电器测试未恢复，线材测试恢复，流程结束。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了插座还是不行"})
    # 选择都有
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    # R05 充电器测试未恢复
    r05_fail = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换充电器还是 0W"}
    ).json()
    assert r05_fail["state"] == "GUIDE"
    assert "线" in r05_fail["message"]
    # R06 线材测试恢复
    r06_ok = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了线后有输入了"}
    ).json()
    assert r06_ok["state"] == "GUIDE"
    assert "观察" in r06_ok["message"]
    # 确认稳定
    done = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "持续有输入，稳定"}
    ).json()
    assert done["state"] == "RESOLVE"


def test_ms03_both_tests_fail_to_handoff(tmp_path) -> None:
    """MS03: 充电器和线材都测了仍失败，转人工。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了插座还是不行"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换充电器还是不行"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了线还是不行"}
    ).json()
    assert body["state"] == "HANDOFF"
    # 交接包应包含已尝试的记录
    view = client.get(f"/v1/agent/conversations/{cid}").json()
    pkg = view["handoff_package"]
    assert "executed_attempts" in pkg or "missing_information" in pkg


def test_story2_cable_to_charger_correction(tmp_path) -> None:
    """故事 2：用户先说换过线，后更正为换过充电头，系统修正事实。"""
    client = make_client(tmp_path)
    cid = client.post(
        "/v1/conversations", json={"message": "A1289 接 C1 充不进去"}
    ).json()["conversation_id"]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})

    # 建一条依赖 cable_model 的 attempt
    client.post(
        f"/v1/conversations/{cid}/case/revisions",
        json={"facts": {"cable_model": "换过"}, "reason": "用户说换过线"},
    )
    attempt = client.post(
        f"/v1/conversations/{cid}/attempts",
        json={
            "recommendation": "使用新线材测试",
            "purpose": "线材交叉测试",
            "instructions": "更换线材测试是否充电",
            "observation_target": "是否充电",
            "exit_condition": "观察到充电",
        },
    ).json()
    assert attempt["status"] == "active"
    assert "cable_model" in attempt["depends_on"]

    # 用户更正：之前说换过线，其实是换过充电头
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说换过线，其实是换过充电头"},
    ).json()
    assert corrected["old_fact"] == {"cable_model": "换过"}
    assert corrected["new_fact"] == {"charger_model": "换过"}
    assert attempt["attempt_id"] in corrected["withdrawn_attempt_ids"]
    assert corrected["new_revision"] is not None


def test_ma02_provider_timeout_during_risk_still_blocks(tmp_path) -> None:
    """MA02: provider 超时时风险规则仍要生效。"""
    import httpx
    from fastapi.testclient import TestClient
    from smart_service_agent.intent import IntentProvider
    from smart_service_agent.main import create_app
    from smart_service_agent.models import Intent, IntentResult
    from smart_service_agent.repository import MemoryRepository

    class TimeoutProvider(IntentProvider):
        def classify(self, message: str) -> IntentResult:
            raise httpx.TimeoutException("test timeout")

    client = TestClient(create_app(MemoryRepository(), TimeoutProvider()))
    body = client.post(
        "/v1/conversations", json={"message": "A1289 充电时冒烟"}
    ).json()
    assert body["state"] == "BLOCK"


def test_consumer_response_exposes_workflow_fields(tmp_path) -> None:
    """ConsumerResponse 暴露 next_attempt_id / confirmation_required 等字段。"""
    client = make_client(tmp_path)
    body = client.post(
        "/v1/conversations", json={"message": "A1289 接 C1 充不进去"}
    ).json()
    # 首次进入 A1289 流程，应返回 safety_precheck，confirmation_required=True
    assert body["confirmation_required"] is True
    assert body["state"] == "ASK"
    for key in (
        "next_attempt_id",
        "old_fact",
        "new_fact",
        "affected_attempt_ids",
        "new_revision",
        "withdrawn_attempt_ids",
        "preserved_fact_ids",
    ):
        assert key in body, key
