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

    # 用户明确更正 C1 → C2，系统直接生效，不要求二次确认
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    assert corrected["state"] == "GUIDE"
    assert "已更正为 C2" in corrected["message"]
    assert "改接 C1" in corrected["message"]
    assert corrected["confirmation_required"] is False
    assert corrected["old_fact"] == {"charging_port": "C1"}
    assert corrected["new_fact"] == {"charging_port": "C2"}

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


def test_a1289_accessories_none_goes_evidence_then_handoff(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "都没有"}).json()
    # R08：先请用户提供证据
    assert body["state"] == "ASK"
    assert "屏幕" in body["message"] or "照片" in body["message"] or "证据" in body["message"]
    final = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "屏幕一直 0W，C1 接口没有任何灯显"},
    ).json()
    assert final["state"] == "HANDOFF"


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


def test_a1289_r06_failed_goes_evidence_then_handoff(tmp_path) -> None:
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
    # R08：先请用户提供证据
    assert body["state"] == "ASK"
    final = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "C1 接口没有灯显，屏幕 0W"},
    ).json()
    assert final["state"] == "HANDOFF"


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


def test_ma01_cross_turn_clear_correction_applies_directly(tmp_path) -> None:
    """跨轮明确更正直接生效，不误判为 MA01 矛盾。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    assert corrected["state"] == "GUIDE"
    assert "已更正为 C2" in corrected["message"]
    assert corrected["confirmation_required"] is False
    assert "不一致" not in corrected["message"]


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
    """MS01: 用户把 C1 更正为 C2，系统指导改接 C1，用户确认恢复。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    # 用户先否认风险
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})

    # 用户明确更正接口，直接生效
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
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
    """MS03: 充电器和线材都测了仍失败，经 R08 证据收集后转人工。"""
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
    # R08：先请用户提供证据
    assert body["state"] == "ASK"
    final = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "C1 接口没有灯显，屏幕 0W，已换过插座和线材"},
    ).json()
    assert final["state"] == "HANDOFF"
    view = client.get(f"/v1/agent/conversations/{cid}").json()
    pkg = view["handoff_package"]
    assert "executed_attempts" in pkg
    assert "untested_items" in pkg


def test_story2_cable_to_charger_correction(tmp_path) -> None:
    """故事 2：用户先说换过线，后更正为换过充电头，系统修正事实。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
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

    # 用户明确更正：之前说换过线，其实是换过充电头。直接生效。
    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说换过线，其实是换过充电头"},
    ).json()
    assert corrected["state"] == "GUIDE"
    assert corrected["old_fact"] == {"cable_model": "换过"}
    assert corrected["new_fact"] == {"charger_model": "换过"}
    assert attempt["attempt_id"] in corrected["withdrawn_attempt_ids"]
    assert corrected["new_revision"] is not None


def test_ambiguous_fact_change_still_asks_for_clarification(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我记不清了，好像是 C2"},
    ).json()
    assert body["state"] == "ASK"
    assert "确认实际使用的接口" in body["message"] or "实际使用的接口" in body["message"]


def test_conversation_feedback_closes_attempts_without_duplicates(tmp_path) -> None:
    """完整闭环：明确更正局部撤回，新步骤随反馈更新，最终只保留真实历史。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    first_guide = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "屏幕一直 0W"}
    ).json()
    old_attempt_id = first_guide["next_attempt_id"]

    corrected = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "我之前说错了，我接的是 C2，不是 C1"},
    ).json()
    new_attempt_id = corrected["next_attempt_id"]
    assert corrected["state"] == "GUIDE"
    assert new_attempt_id != old_attempt_id

    recovered = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "改接 C1 后有输入了"},
    ).json()
    assert recovered["state"] == "GUIDE"
    assert recovered["confirmation_required"] is True

    resolved = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "持续有输入，没有中断"},
    ).json()
    assert resolved["state"] == "RESOLVE"

    attempts = client.get(f"/v1/conversations/{cid}/attempts").json()
    assert len(attempts) == 2
    by_id = {attempt["attempt_id"]: attempt for attempt in attempts}
    assert by_id[old_attempt_id]["status"] == "withdrawn"
    assert by_id[new_attempt_id]["execution_status"] == "executed"
    assert by_id[new_attempt_id]["outcome"] == "resolved"
    assert "稳定性确认" in by_id[new_attempt_id]["observation"]

    view = client.get(f"/v1/agent/conversations/{cid}").json()
    package = view["handoff_package"]
    assert len(package["withdrawn_attempts"]) == 1
    assert len(package["executed_attempts"]) == 1
    assert package["tested_items"] == ["改接 C1 测试"]
    assert package["unresolved_items"] == []


def test_conversation_feedback_records_unavailable_and_unknown(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    guide = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕一直 0W"}).json()
    attempt_id = guide["next_attempt_id"]

    client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "没有其他插座，没法执行"},
    )
    attempts = client.get(f"/v1/conversations/{cid}/attempts").json()
    attempt = next(item for item in attempts if item["attempt_id"] == attempt_id)
    assert attempt["execution_status"] == "skipped_unavailable"
    assert attempt["outcome"] == "unknown"

    # 新会话验证“已执行但暂无法判断”：保留当前阶段并补问观察，不误跳步。
    cid2 = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid2}/messages", json={"message": "没有"})
    guide2 = client.post(
        f"/v1/conversations/{cid2}/messages", json={"message": "屏幕一直 0W"}
    ).json()
    unknown = client.post(
        f"/v1/conversations/{cid2}/messages",
        json={"message": "已经换了，但暂时无法判断"},
    ).json()
    assert unknown["state"] == "ASK"
    attempts2 = client.get(f"/v1/conversations/{cid2}/attempts").json()
    attempt2 = next(item for item in attempts2 if item["attempt_id"] == guide2["next_attempt_id"])
    assert attempt2["execution_status"] == "executed"
    assert attempt2["outcome"] == "unknown"


def test_risk_interrupt_does_not_mark_attempt_as_completed(tmp_path) -> None:
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    guide = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕一直 0W"}).json()
    blocked = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "换插座后有输入了，但是设备开始冒烟"},
    ).json()
    assert blocked["state"] == "BLOCK"
    attempts = client.get(f"/v1/conversations/{cid}/attempts").json()
    attempt = next(item for item in attempts if item["attempt_id"] == guide["next_attempt_id"])
    assert attempt["execution_status"] == "proposed"
    assert attempt["outcome"] is None


def test_ma02_provider_timeout_during_risk_still_blocks(tmp_path) -> None:
    """MA02: provider 超时时风险规则仍要生效。"""
    import httpx
    from fastapi.testclient import TestClient

    from smart_service_agent.intent import IntentProvider
    from smart_service_agent.main import create_app
    from smart_service_agent.models import IntentResult
    from smart_service_agent.repository import MemoryRepository

    class TimeoutProvider(IntentProvider):
        def classify(self, message: str) -> IntentResult:
            raise httpx.TimeoutException("test timeout")

    client = TestClient(create_app(MemoryRepository(), TimeoutProvider()))
    body = client.post("/v1/conversations", json={"message": "A1289 充电时冒烟"}).json()
    assert body["state"] == "BLOCK"


def test_consumer_response_exposes_workflow_fields(tmp_path) -> None:
    """ConsumerResponse 暴露 next_attempt_id / confirmation_required 等字段。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()
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


def test_guide_auto_creates_attempt(tmp_path) -> None:
    """步骤闭环：GUIDE 状态自动建 Attempt，返回 next_attempt_id。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"}).json()
    assert body["state"] == "GUIDE"
    assert body["next_attempt_id"] is not None
    attempts = client.get(f"/v1/conversations/{cid}/attempts").json()
    assert any(a["attempt_id"] == body["next_attempt_id"] for a in attempts)


def test_d03_uncertain_compatibility_goes_handoff(tmp_path) -> None:
    """D03：配件兼容性未知时，不做通电测试，记录未测试转人工。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了插座还是不行"})
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "都有，但不确定是否兼容"},
    ).json()
    assert body["state"] == "HANDOFF"


def test_d09_manual_risk_lock_release(tmp_path) -> None:
    """D09：用户否认风险不解锁；只有人工解除后普通流程恢复。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "充电器冒烟"}).json()["conversation_id"]
    r1 = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "现在没有风险了，可以继续吗"},
    ).json()
    # D09: a denial does not release the lock
    assert r1["state"] == "BLOCK"

    # explicit agent release
    release = client.post(
        f"/v1/agent/conversations/{cid}/risk-lock/release",
        json={"reason": "已完成风险处理", "operator": "agent-001"},
    )
    assert release.status_code == 200
    assert release.json()["risk_lock"] is False

    # after release, the same denial is no longer blocked
    r2 = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "现在没有风险了，可以继续吗"},
    ).json()
    assert r2["state"] != "BLOCK"


def test_d05_attachment_with_sufficient_text_proceeds(tmp_path) -> None:
    """D05：附件可选，文字信息足够时正常建立工单。"""
    client = make_client(tmp_path)
    body = client.post(
        "/v1/conversations",
        json={
            "message": "A1289 接 C1 充不进去",
            "attachments": [{"kind": "product_image", "filename": "photo.jpg"}],
        },
    ).json()
    assert body["state"] == "ASK"
    assert body["confirmation_required"] is True


def test_skipped_unavailable_records_reason(tmp_path) -> None:
    """skipped_unavailable：记录用户无法执行的原因。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"}).json()
    attempt_id = body["next_attempt_id"]
    assert attempt_id is not None

    update = client.patch(
        f"/v1/conversations/{cid}/attempts/{attempt_id}",
        json={
            "execution_status": "skipped_unavailable",
            "skip_reason": "没有其他插座可换",
        },
    )
    assert update.status_code == 200
    data = update.json()
    assert data["execution_status"] == "skipped_unavailable"
    assert data["observation"] == "没有其他插座可换"


def test_r07_remind_charger_only_then_user_finds_cable(tmp_path) -> None:
    """R07：只有充电器时，未恢复后提醒找线材；用户找到则进 R06。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "只有充电器"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "换了充电器还是不行"}
    ).json()
    assert body["state"] == "ASK"
    assert "线" in body["message"]
    # 用户说找到线材 -> 进 R06
    next_body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "我找到另一根线了"}
    ).json()
    assert next_body["state"] == "GUIDE"
    assert "线" in next_body["message"]


def test_r07_remind_user_cannot_find_cable_goes_r08(tmp_path) -> None:
    """R07：只有充电器时，用户无法找到线材，记录未测试转 R08 证据收集。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "只有充电器"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了充电器还是不行"})
    body = client.post(
        f"/v1/conversations/{cid}/messages", json={"message": "我找不到其他线材"}
    ).json()
    assert body["state"] == "ASK"
    assert "证据" in body["message"] or "照片" in body["message"] or "屏幕" in body["message"]


def test_r08_evidence_collection_then_handoff(tmp_path) -> None:
    """R08：用户提供证据后转人工，证据进入 HandoffPackage。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换充电器还是不行"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了线还是不行"})
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "C1 接口无灯显，屏幕 0W，已换线换充电器"},
    ).json()
    assert body["state"] == "HANDOFF"

    view = client.get(f"/v1/agent/conversations/{cid}").json()
    card = view["empathy_card"]
    assert card["entities"].get("a1289_evidence")


def test_handoff_package_includes_all_5_new_fields(tmp_path) -> None:
    """HandoffPackage 应包含 executed / skipped / withdrawn / observations / untested_items。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换充电器还是不行"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了线还是不行"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "C1 无灯显，屏幕 0W"})

    view = client.get(f"/v1/agent/conversations/{cid}").json()
    pkg = view["handoff_package"]
    for key in (
        "executed_attempts",
        "skipped_attempts",
        "withdrawn_attempts",
        "observations",
        "untested_items",
    ):
        assert key in pkg, key


def test_consumer_response_exposes_next_attempt_object(tmp_path) -> None:
    """普通决策：next_attempt 返回完整对象，不只是 ID。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    body = client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"}).json()
    assert body["state"] == "GUIDE"
    assert body["next_attempt"] is not None
    assert body["next_attempt"]["attempt_id"] == body["next_attempt_id"]
    assert body["next_attempt"]["recommendation"]
    assert body["next_attempt"]["instructions"]


def test_consumer_response_exposes_risk_lock_and_reasons_on_block(tmp_path) -> None:
    """风险中断：state=BLOCK + risk_lock=true + reasons + allowed_actions。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "充电器冒烟"}).json()
    assert body["state"] == "BLOCK"
    assert body["risk_lock"] is True
    assert body["reasons"]
    assert any("冒烟" in r for r in body["reasons"])
    assert "allowed_actions" in body
    assert "available_actions" in body
    assert body["allowed_actions"] == body["available_actions"]


def test_handoff_package_exposes_attempts_and_risks(tmp_path) -> None:
    """人工升级：HandoffPackage 含 attempts 和 risks 别名。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "充电器冒烟"}).json()
    cid = body["conversation_id"]
    client.post(
        f"/v1/conversations/{cid}/handoff",
        json={"accepted": True, "idempotency_key": "risk-alias-001"},
    )
    view = client.get(f"/v1/agent/conversations/{cid}").json()
    pkg = view["handoff_package"]
    assert "attempts" in pkg
    assert "risks" in pkg
    assert pkg["risks"]
    assert any("冒烟" in r for r in pkg["risks"])


def test_fb02_partial_improvement_does_not_close(tmp_path) -> None:
    """FB02: 部分改善不误结案、不误跳步。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    # R04 阶段：用户说“感觉好一点了”，但没说恢复
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "感觉好一点了，但还是不太行"},
    ).json()
    # 不结案，不跳到 RESOLVE
    assert body["state"] != "RESOLVE"


def test_fb02_unstable_recovery_does_not_close(tmp_path) -> None:
    """FB02: 用户报告恢复但不稳定，二次确认阶段要求继续观察。"""
    client = make_client(tmp_path)
    cid = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()[
        "conversation_id"
    ]
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "没有"})
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "屏幕 0W"})
    # 报告恢复
    client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "换插座后好了"},
    )
    # 恢复但不稳定 -> 不结案
    body = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "好像还是不太稳定，一会儿有输入一会儿 0W"},
    ).json()
    assert body["state"] != "RESOLVE"


def test_handoff_package_distinguishes_tested_and_unresolved(tmp_path) -> None:
    """Sheet1 R08: HandoffPackage 区分 tested_items 和 unresolved_items。"""
    client = make_client(tmp_path)
    body = client.post("/v1/conversations", json={"message": "A1289 接 C1 充不进去"}).json()
    cid = body["conversation_id"]
    _to_x(client, cid)
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "都有"})
    # R05 阶段创建 attempt 并执行（未恢复）
    r05 = client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "换充电器还是不行"},
    ).json()
    aid = r05.get("next_attempt_id")
    if aid:
        client.patch(
            f"/v1/conversations/{cid}/attempts/{aid}",
            json={"execution_status": "executed", "observation": "换了充电器仍然 0W"},
        )
    # 继续到 HANDOFF
    client.post(f"/v1/conversations/{cid}/messages", json={"message": "换了线还是不行"})
    client.post(
        f"/v1/conversations/{cid}/messages",
        json={"message": "C1 无灯显，屏幕 0W"},
    )
    view = client.get(f"/v1/agent/conversations/{cid}").json()
    pkg = view["handoff_package"]
    assert "tested_items" in pkg
    assert "unresolved_items" in pkg
    assert "untested_items" in pkg
    # 至少有一条已测记录
    assert pkg["tested_items"]
    # 已测但未 resolved -> unresolved
    assert pkg["unresolved_items"]
