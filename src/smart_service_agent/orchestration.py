from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from uuid import uuid4

from smart_service_agent.config import Settings
from smart_service_agent.intent import (
    FallbackIntentProvider,
    IntentProvider,
    RuleBasedIntentProvider,
)
from smart_service_agent.knowledge import InMemoryKnowledgeBase, KnowledgeProvider
from smart_service_agent.models import (
    AgentConversationView,
    AttemptCreateRequest,
    AttemptRecord,
    AttemptUpdateRequest,
    CaseRecord,
    CaseRevision,
    CaseRevisionRequest,
    ConsumerResponse,
    ConversationRequest,
    ConversationState,
    EmpathyCard,
    EventSummary,
    HandoffPackage,
    Intent,
    RiskLevel,
    StoredConversation,
    TicketRecord,
    TicketResultRequest,
)
from smart_service_agent.repository import StorageRepository, utc_now

HIGH_RISK_TERMS = (
    "冒烟",
    "异味",
    "起火",
    "漏液",
    "进液",
    "进水",
    "液体进入",
    "洒到",
    "洒进",
    "渗液",
    "鼓包",
    "异常发热",
    "烫得",
    "烫手",
    "发烫",
    "过热",
    "烤焦",
    "烧焦",
    "焦味",
    "塑料味",
)
HYPOTHETICAL_PREFIXES = ("会不会", "是否会", "会否", "怕", "担心")
RESOLVED_TERMS = ("已经好了", "已恢复", "现在好了", "已消退")


class ConversationOrchestrator:
    def __init__(
        self,
        repository: StorageRepository,
        settings: Settings,
        intent_provider: IntentProvider | None = None,
        knowledge_provider: KnowledgeProvider | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.knowledge = knowledge_provider or InMemoryKnowledgeBase(settings.knowledge_version)
        self.intent_provider = FallbackIntentProvider(
            intent_provider,
            RuleBasedIntentProvider(),
            settings.intent_minimum_confidence,
        )

    def start(self, request: ConversationRequest) -> ConsumerResponse:
        conversation_id = f"conv_{uuid4().hex}"
        return self._process(conversation_id, request, None)

    def continue_conversation(
        self, conversation_id: str, request: ConversationRequest
    ) -> ConsumerResponse | None:
        existing = self.repository.get_conversation(conversation_id)
        if existing is None:
            return None
        return self._process(conversation_id, request, existing)

    def _process(
        self,
        conversation_id: str,
        request: ConversationRequest,
        existing: StoredConversation | None,
    ) -> ConsumerResponse:
        now = utc_now()
        messages = [*existing.messages, request.message] if existing else [request.message]
        card = self._build_card(conversation_id, request, existing)
        result_id = f"result_{uuid4().hex}"
        response_text, actions = self._consumer_copy(card)
        stored = StoredConversation(
            conversation_id=conversation_id,
            state=card.next_state,
            messages=messages,
            empathy_card=card,
            last_result_id=result_id,
            unresolved_attempts=(existing.unresolved_attempts if existing else 0)
            + int(card.next_state not in {ConversationState.RESOLVE, ConversationState.GUIDE}),
            case=existing.case if existing else self._initial_case(conversation_id, request),
            attempts=list(existing.attempts) if existing else [],
            ticket=existing.ticket if existing else None,
            risk_lock=(existing.risk_lock if existing else False)
            or card.next_state == ConversationState.BLOCK,
            risk_lock_reason=(
                (existing.risk_lock_reason if existing and existing.risk_lock else None)
                or (card.risk_reasons[0] if card.risk_reasons else None)
            ),
            risk_lock_source=(
                (existing.risk_lock_source if existing and existing.risk_lock else None)
                or ("auto_safety_rules" if card.next_state == ConversationState.BLOCK else None)
            ),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.repository.save_conversation(stored)
        self.repository.add_audit(
            conversation_id,
            "state_transition",
            {
                "from_state": existing.state.value if existing else None,
                "to_state": card.next_state.value,
                "trigger": card.risk_reasons or card.missing_information or ["knowledge_available"],
                "rule_version": self.settings.rule_version,
                "knowledge_version": self.settings.knowledge_version,
                "result_id": result_id,
            },
        )
        return ConsumerResponse(
            conversation_id=conversation_id,
            result_id=result_id,
            state=card.next_state,
            message=response_text,
            evidence=(
                card.knowledge_refs
                if card.next_state in {ConversationState.RESOLVE, ConversationState.GUIDE}
                else []
            ),
            available_actions=actions,
        )

    def _build_card(
        self,
        conversation_id: str,
        request: ConversationRequest,
        existing: StoredConversation | None,
    ) -> EmpathyCard:
        text = request.message
        risk_terms = self._active_risk_terms(text)
        if existing and existing.risk_lock and not risk_terms:
            after_sales_terms = ("订单", "退款", "退货", "物流", "发票", "保修")
            if not any(term in text for term in after_sales_terms):
                risk_terms = [existing.risk_lock_reason or "risk_lock_active"]
        confirmed = list(
            dict.fromkeys(
                [*(existing.empathy_card.confirmed_facts if existing else []), request.message]
            )
        )
        entities = dict(existing.empathy_card.entities) if existing else {}
        if request.product:
            entities["product"] = request.product
        if request.order_reference:
            entities["order_reference"] = request.order_reference

        if request.attachments:
            state = ConversationState.HANDOFF
            risk = RiskLevel.LOW
            missing = ["可读取的附件内容"]
            refs = []
            intent_result = self.intent_provider.classify(text)
            intent = intent_result.intent
            intent_confidence = intent_result.confidence
            intent_source = intent_result.source
        elif risk_terms:
            state = ConversationState.BLOCK
            risk = RiskLevel.HIGH
            missing: list[str] = []
            refs = []
            intent = Intent.COMPLAINT
            intent_confidence = 1.0
            intent_source = "safety_rules"
        elif self._needs_charging_details(text, existing):
            state = ConversationState.ASK
            risk = RiskLevel.LOW
            missing = ["设备型号、供电指示状态或已经尝试过的方法"]
            refs = []
            intent = Intent.USAGE
            intent_confidence = 1.0
            intent_source = "charging_clarification_rules"
        elif self._needs_model_details(text, existing):
            state = ConversationState.ASK
            risk = RiskLevel.LOW
            missing = ["设备型号或期望的充电功率"]
            refs = []
            intent = Intent.PURCHASE
            intent_confidence = 1.0
            intent_source = "clarification_rules"
        else:
            intent_result = self.intent_provider.classify(text)
            if (
                existing
                and intent_result.intent == Intent.CONSULT
                and intent_result.confidence < self.settings.intent_minimum_confidence
            ):
                intent = existing.empathy_card.intent
                intent_confidence = existing.empathy_card.intent_confidence
                intent_source = f"context:{existing.empathy_card.intent_source}"
            else:
                intent = intent_result.intent
                intent_confidence = intent_result.confidence
                intent_source = intent_result.source
            if self._is_charging_issue_context(text, existing) and existing:
                refs = self.knowledge.search(
                    f"{existing.case.original_statement} {text}", Intent.USAGE
                )
            else:
                refs = self.knowledge.search(text, intent)
            if not refs and existing and existing.empathy_card.intent == Intent.PURCHASE:
                context_query = f"{existing.empathy_card.surface_issue} {text}"
                refs = self.knowledge.search(context_query, Intent.PURCHASE)
            missing = []
            risk = RiskLevel.LOW
            state = (
                ConversationState.GUIDE
                if refs and self._is_charging_issue_context(text, existing)
                else (ConversationState.RESOLVE if refs else ConversationState.HANDOFF)
            )

        return EmpathyCard(
            conversation_id=conversation_id,
            surface_issue=request.message,
            intent=intent,
            intent_confidence=intent_confidence,
            intent_source=intent_source,
            emotion="concerned" if risk_terms else None,
            scenario=self._scenario(intent),
            entities=entities,
            confirmed_facts=confirmed,
            inferences=[],
            missing_information=missing,
            risk_level=risk,
            risk_reasons=[f"命中设备安全风险词：{term}" for term in risk_terms],
            knowledge_refs=refs,
            next_state=state,
            schema_version=self.settings.schema_version,
        )

    @staticmethod
    def _consumer_copy(card: EmpathyCard) -> tuple[str, list[str]]:
        if card.next_state == ConversationState.BLOCK:
            return (
                "你描述的情况可能涉及设备安全风险。请立即停止使用并断开电源，不要拆机、"
                "再次通电或继续充电；将设备移离可燃物，并在确保人身安全的前提下等待处理。"
                "是否同意我将设备信息和当前风险提交给人工客服跟进？",
                ["confirm_handoff", "decline_handoff"],
            )
        if card.next_state == ConversationState.ASK:
            if "设备型号、供电指示状态或已经尝试过的方法" in card.missing_information:
                return (
                    "为了只调整一个条件，请告诉我设备型号、接通电源后的指示状态，以及已经试过的方法；不确定也可以跳过。",
                    [
                        "reply",
                        "skip",
                        "confirm_handoff",
                    ],
                )
            return "为了更准确地给出建议，请告诉我设备型号、接口类型或期望的充电功率。", ["reply"]
        if card.next_state == ConversationState.HANDOFF:
            if "可读取的附件内容" in card.missing_information:
                message = (
                    "我目前无法读取你上传的附件内容，因此不会根据文件名猜测。"
                    "是否同意转人工客服查看并跟进？"
                )
                return message, [
                    "confirm_handoff",
                    "decline_handoff",
                ]
            return "当前没有足够的已审核依据来安全回答。是否同意我将现有信息提交给人工客服跟进？", [
                "confirm_handoff",
                "decline_handoff",
            ]
        labels = {
            Intent.USAGE: "使用指引",
            Intent.PURCHASE: "选购指引",
            Intent.CONSULT: "产品指引",
        }
        label = labels.get(card.intent, "服务指引")
        evidence_text = " ".join(ref.excerpt for ref in card.knowledge_refs)
        return f"根据已审核的{label}：{evidence_text}", [
            "feedback",
            "new_question",
        ]

    @staticmethod
    def _initial_case(conversation_id: str, request: ConversationRequest) -> CaseRecord:
        now = utc_now()
        facts = {"product": request.product} if request.product else {}
        revision = CaseRevision(
            revision=1,
            facts=facts,
            unknown_fields=["product"] if not request.product else [],
            reason="initial_statement",
            created_at=now,
        )
        return CaseRecord(
            case_id=f"case_{uuid4().hex}",
            conversation_id=conversation_id,
            original_statement=request.message,
            revisions=[revision],
        )

    @staticmethod
    def _is_charging_issue_context(text: str, existing: StoredConversation | None) -> bool:
        charging_terms = ("无法充电", "充不上电", "没反应", "充电中断", "充电不稳定")
        return any(term in text for term in charging_terms) or bool(
            existing
            and any(term in existing.case.original_statement for term in charging_terms)
        )

    def _needs_charging_details(self, text: str, existing: StoredConversation | None) -> bool:
        if not self._is_charging_issue_context(text, existing) or existing is not None:
            return False
        detail_terms = (
            "接入电源后",
            "更换线材后",
            "连接设备后",
            "试过",
            "指示灯",
            "型号",
            "发生在",
        )
        return not any(term in text for term in detail_terms)

    def revise_case(self, conversation_id: str, request: CaseRevisionRequest) -> CaseRecord | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None or conversation.case is None:
            return None
        previous = conversation.case.revisions[-1]
        revision = CaseRevision(
            revision=conversation.case.current_revision + 1,
            facts={**previous.facts, **request.facts},
            unknown_fields=request.unknown_fields,
            reason=request.reason,
            created_at=utc_now(),
        )
        conversation.case.revisions.append(revision)
        conversation.case.current_revision = revision.revision
        changed_fields = set(request.facts.keys())
        withdrawn_ids: list[str] = []
        now = utc_now()
        for attempt in conversation.attempts:
            if attempt.status != "active":
                continue
            hit = [
                f
                for f in changed_fields
                if f in attempt.depends_on
                and attempt.depends_on[f].get("revision", revision.revision) < revision.revision
            ]
            if not hit:
                continue
            attempt.status = "withdrawn"
            details = []
            for f in hit:
                old_dep = attempt.depends_on[f]
                details.append(f"{f} {old_dep.get('value')} -> {request.facts[f]}")
            attempt.withdrawn_reason = "fact_changed: " + "; ".join(details)
            attempt.updated_at = now
            withdrawn_ids.append(attempt.attempt_id)
        conversation.updated_at = now
        self.repository.save_conversation(conversation)
        self.repository.add_audit(
            conversation_id,
            "case_revised",
            {
                "revision": revision.revision,
                "reason": request.reason,
                "withdrawn_attempt_ids": withdrawn_ids,
            },
        )
        return conversation.case

    @staticmethod
    def _infer_depends_on(
        text: str, conversation: StoredConversation
    ) -> dict[str, dict[str, Any]]:
        """从 attempt 文本推断它依赖的 case 事实字段与当前版本。"""
        if not conversation.case or not conversation.case.revisions:
            return {}
        current = conversation.case.revisions[-1]
        facts = current.facts
        revision = conversation.case.current_revision
        field_patterns = {
            "charging_port": ("C1", "C2", "USB-A", "USB A", "接口", "端口"),
            "charger_model": ("充电器", "充电头", "适配器"),
            "cable_model": ("线材", "充电线", "数据线", "USB-C to USB-C"),
            "socket_state": ("插座", "墙插", "插排", "供电"),
            "product": ("A1289", "737"),
        }
        result: dict[str, dict[str, Any]] = {}
        for field, patterns in field_patterns.items():
            if field not in facts:
                continue
            if any(p in text for p in patterns):
                result[field] = {"value": facts[field], "revision": revision}
        return result

    def create_attempt(
        self, conversation_id: str, request: AttemptCreateRequest
    ) -> AttemptRecord | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            return None
        normalized = "".join(request.recommendation.lower().split())
        if any(
            "".join(item.recommendation.lower().split()) == normalized
            for item in conversation.attempts
        ):
            raise ValueError("duplicate attempt")
        now = utc_now()
        attempt_text = (
            request.recommendation + " " + request.purpose + " " + request.instructions
        )
        depends_on = self._infer_depends_on(attempt_text, conversation)
        attempt = AttemptRecord(
            attempt_id=f"attempt_{uuid4().hex}",
            conversation_id=conversation_id,
            depends_on=depends_on,
            created_at=now,
            updated_at=now,
            **request.model_dump(),
        )
        conversation.attempts.append(attempt)
        conversation.updated_at = now
        self.repository.save_conversation(conversation)
        self.repository.add_audit(
            conversation_id, "attempt_proposed", {"attempt_id": attempt.attempt_id}
        )
        return attempt

    def update_attempt(
        self, conversation_id: str, attempt_id: str, request: AttemptUpdateRequest
    ) -> AttemptRecord | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            return None
        attempt = next(
            (item for item in conversation.attempts if item.attempt_id == attempt_id), None
        )
        if attempt is None:
            return None
        if request.execution_status == "executed" and not request.observation:
            raise ValueError("observation is required when an attempt was executed")
        attempt.execution_status = request.execution_status
        attempt.observation = request.observation
        attempt.outcome = request.outcome or (
            "unknown" if request.execution_status == "skipped" else None
        )
        attempt.updated_at = utc_now()
        conversation.updated_at = attempt.updated_at
        self.repository.save_conversation(conversation)
        self.repository.add_audit(
            conversation_id,
            "attempt_updated",
            {"attempt_id": attempt_id, "execution_status": request.execution_status},
        )
        return attempt

    def record_ticket_result(
        self, conversation_id: str, request: TicketResultRequest
    ) -> TicketRecord | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None or conversation.ticket is None:
            return None
        status_map = {
            "agent_replied": "agent_replied",
            "action_completed": "action_completed",
            "user_confirmed_resolved": "resolved",
            "reopened": "reopened",
        }
        now = utc_now()
        conversation.ticket.status = status_map[request.event]
        conversation.ticket.version += 1
        conversation.ticket.updated_at = now
        conversation.ticket.result_events.append(
            {"event": request.event, "note": request.note, "created_at": now.isoformat()}
        )
        conversation.updated_at = now
        self.repository.save_conversation(conversation)
        self.repository.add_audit(
            conversation_id,
            "ticket_result",
            {"event": request.event, "ticket_version": conversation.ticket.version},
        )
        return conversation.ticket

    @staticmethod
    def _active_risk_terms(text: str) -> list[str]:
        resolved_history = any(term in text for term in RESOLVED_TERMS)
        renewed_risk = any(term in text for term in ("但是", "但", "不过", "又", "仍", "现在还"))
        if resolved_history and not renewed_risk:
            return []
        found: list[str] = []
        for term in HIGH_RISK_TERMS:
            index = text.find(term)
            if index < 0:
                continue
            prefix = text[max(0, index - 8) : index]
            negated = re.search(
                r"(?:没有|没|不|未|无)(?:闻到|出现|发生|发现|感到|检测到)?$",
                prefix,
            )
            hypothetical = any(prefix.endswith(marker) for marker in HYPOTHETICAL_PREFIXES)
            if negated or hypothetical:
                continue
            context = text[max(0, index - 12) : index]
            third_party = re.search(
                r"(?:朋友|同事|家人)(?:说|有|出现|使用后|用了以后)?[^，。！？]*$"
                r"|(?:^|[，。！？])(?:他|她)(?:说|有|出现|使用后|用了以后)[^，。！？]*$",
                context,
            )
            if third_party:
                continue
            found.append(term)
        return found

    @staticmethod
    def _needs_model_details(text: str, existing: StoredConversation | None) -> bool:
        model_selection_context = "型号" in text or bool(
            existing and existing.empathy_card.intent == Intent.PURCHASE
        )
        if not model_selection_context:
            return False
        informative = (
            "iPhone",
            "Android",
            "USB-C",
            "Lightning",
            "Type-C",
            "快充",
            "功率",
            "PD",
            "PPS",
        )
        unknown = ("不知道", "不清楚", "不会判断")
        has_details = any(term in text for term in informative)
        explicitly_unknown = any(term in text for term in unknown)
        return not has_details or explicitly_unknown

    @staticmethod
    def _scenario(intent: Intent) -> str:
        return {
            Intent.AFTER_SALES: "after_sales_service",
            Intent.PURCHASE: "product_selection",
            Intent.USAGE: "product_usage",
            Intent.COMPLAINT: "customer_complaint",
        }.get(intent, "product_consultation")

    def create_handoff(self, conversation_id: str, idempotency_key: str) -> EventSummary | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            return None
        eta = utc_now() + timedelta(minutes=self.settings.handoff_eta_minutes)
        event = self.repository.create_event(
            {
                "event_id": f"evt_{uuid4().hex}",
                "conversation_id": conversation_id,
                "status": "waiting_for_agent",
                "priority": 100 if conversation.empathy_card.risk_level == RiskLevel.HIGH else 50,
                "reason": "; ".join(conversation.empathy_card.risk_reasons)
                or "依据不足或用户请求人工",
                "estimated_response_at": eta.isoformat(),
            },
            idempotency_key,
        )
        conversation.state = ConversationState.HANDOFF
        conversation.empathy_card.next_state = ConversationState.HANDOFF
        conversation.updated_at = utc_now()
        if conversation.ticket is None:
            now = utc_now()
            conversation.ticket = TicketRecord(
                ticket_id=str(event["event_id"]),
                conversation_id=conversation_id,
                status="waiting_for_agent",
                created_at=now,
                updated_at=now,
            )
        self.repository.save_conversation(conversation)
        self.repository.add_audit(
            conversation_id,
            "handoff_created",
            {"event_id": event["event_id"], "rule_version": self.settings.rule_version},
        )
        return self._event_summary(event)

    @staticmethod
    def _event_summary(event: dict[str, object]) -> EventSummary:
        return EventSummary(
            event_id=str(event["event_id"]),
            conversation_id=str(event["conversation_id"]),
            status=str(event["status"]),
            priority=int(event["priority"]),
            reason=str(event["reason"]),
            created_at=str(event["created_at"]),
            estimated_response_at=str(event["estimated_response_at"]),
        )

    def agent_view(self, conversation_id: str) -> AgentConversationView | None:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            return None
        event_row = self.repository.get_event_for_conversation(conversation_id)
        event = self._event_summary(event_row) if event_row else None
        actions = self.repository.get_service_actions(event.event_id) if event else []
        card = conversation.empathy_card
        package = HandoffPackage(
            conversation_id=conversation_id,
            original_messages=conversation.messages,
            summary=card.surface_issue,
            confirmed_facts=card.confirmed_facts,
            inferences=card.inferences,
            missing_information=card.missing_information,
            risk_level=card.risk_level,
            risk_reasons=card.risk_reasons,
            knowledge_refs=card.knowledge_refs,
            executed_actions=actions,
            suggested_next_step="优先核实设备型号、供电状态与安全风险，并按授权范围跟进"
            if card.risk_level == RiskLevel.HIGH
            else "核实诉求并补充可靠依据",
            event=event,
            current_state=conversation.state,
            schema_version=self.settings.schema_version,
            rule_version=self.settings.rule_version,
            knowledge_version=self.settings.knowledge_version,
            ticket=conversation.ticket,
        )
        return AgentConversationView(
            handoff_package=package,
            empathy_card=card,
            audit_trail=self.repository.get_audit(conversation_id),
        )
