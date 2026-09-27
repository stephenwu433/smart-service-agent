from __future__ import annotations

from contextlib import suppress
from typing import Literal, Optional, Union

from smart_service_agent.config import get_settings
from smart_service_agent.models import (
    AgentConversationView,
    AttemptRecord,
    AttemptUpdateRequest,
    CaseRecord,
    ConsumerResponse,
    ConversationRequest,
    EventSummary,
    ServiceActionRequest,
    TicketResultRequest,
)
from smart_service_agent.orchestration import ConversationOrchestrator
from smart_service_agent.repository import MemoryRepository

PRODUCT = "Anker 737 Power Bank A1289"
STORIES = {
    "故事1 接口更正": "A1289 接 C1 充不进去，屏幕一直 0W。",
    "故事2 配件交叉": "A1289 接 C1，插座正常，我有备用充电器和 USB-C to USB-C 线。",
    "故事3 途中风险": "A1289 充不进去，现在看起来有点鼓包。",
}


class DemoBackend:
    """In-process A1289 demo. Uses MemoryRepository so Streamlit Cloud does not need MongoDB."""

    def __init__(self) -> None:
        self.repository = MemoryRepository()
        self.orchestrator = ConversationOrchestrator(self.repository, get_settings())
        self.conversation_id: Optional[str] = None
        self.last: Optional[ConsumerResponse] = None
        self.transcript: list[dict[str, str]] = []
        self.selected_event_id: Optional[str] = None

    def send_message(self, text: str) -> ConsumerResponse:
        message = text.strip()
        if not message:
            raise ValueError("message must not be blank")
        request = ConversationRequest(message=message, product=PRODUCT)
        if self.conversation_id is None:
            response = self.orchestrator.start(request)
        else:
            response = self.orchestrator.continue_conversation(self.conversation_id, request)
            if response is None:
                raise ValueError("conversation not found")
        self.conversation_id = response.conversation_id
        self.last = response
        self.transcript.append({"role": "用户", "text": message})
        self.transcript.append({"role": "系统", "text": response.message})
        return response

    def report_attempt(self, kind: str, observation: str) -> ConsumerResponse:
        attempt = self.current_attempt()
        note = observation.strip()
        if attempt and self.conversation_id:
            executed = kind in {"executed", "improved"}
            if executed:
                status: Literal["executed", "skipped", "skipped_unavailable"] = "executed"
            elif kind == "skipped_unavailable":
                status = "skipped_unavailable"
            else:
                status = "skipped"
            outcome = "improved" if kind == "improved" else ("unchanged" if executed else "unknown")
            payload = AttemptUpdateRequest(
                execution_status=status,
                observation=note or ("已执行并观察" if executed else None),
                skip_reason=None if executed else (note or kind),
                outcome=outcome,
            )
            with suppress(ValueError):
                self.orchestrator.update_attempt(self.conversation_id, attempt.attempt_id, payload)
        defaults = {
            "executed": "已按当前一步执行并观察",
            "improved": "已经恢复充电",
            "skipped": "先跳过这一步",
            "skipped_unavailable": "没有其他充电器或线材",
        }
        return self.send_message(note or defaults.get(kind, kind))

    def decide_handoff(self, accepted: bool) -> Union[EventSummary, ConsumerResponse]:
        if self.conversation_id is None:
            raise ValueError("conversation not found")
        conversation = self.repository.get_conversation(self.conversation_id)
        if conversation is None:
            raise ValueError("conversation not found")
        if not accepted:
            self.repository.add_audit(
                self.conversation_id, "handoff_declined", {"safety_reminder_retained": True}
            )
            message = (
                "已记录你暂不转人工。请保持设备断电并停止使用，不要拆机或再次通电测试；"
                "如出现起火、冒烟或漏液，请远离可燃物并联系人工客服处理。"
                if conversation.empathy_card.risk_level.value == "high"
                else (
                    "已记录你暂不转人工。由于当前缺少可靠依据，我不会猜测答案；"
                    "你可以补充更多信息后再试。"
                )
            )
            response = ConsumerResponse(
                conversation_id=self.conversation_id,
                result_id=conversation.last_result_id,
                state=conversation.state,
                message=message,
            )
            self.last = response
            self.transcript.append({"role": "用户", "text": "暂不转人工"})
            self.transcript.append({"role": "系统", "text": message})
            return response
        event = self.orchestrator.create_handoff(self.conversation_id, f"ui-{self.conversation_id}")
        if event is None:
            raise ValueError("conversation not found")
        self.selected_event_id = event.event_id
        self.transcript.append({"role": "用户", "text": "同意模拟转人工"})
        self.transcript.append(
            {
                "role": "系统",
                "text": f"已创建模拟人工事件 {event.event_id}，不是真实安克工单。",
            }
        )
        return event

    def case(self) -> Optional[CaseRecord]:
        if not self.conversation_id:
            return None
        conversation = self.repository.get_conversation(self.conversation_id)
        return conversation.case if conversation else None

    def attempts(self) -> list[AttemptRecord]:
        if not self.conversation_id:
            return []
        conversation = self.repository.get_conversation(self.conversation_id)
        return list(conversation.attempts) if conversation else []

    def current_facts(self) -> list[str]:
        case = self.case()
        if case is None or not case.revisions:
            return []
        latest = case.revisions[-1]
        return [f"{key}：{value}" for key, value in latest.facts.items()]

    def current_attempt(self) -> Optional[AttemptRecord]:
        if self.last and self.last.next_attempt:
            return self.last.next_attempt
        attempt_id = self.last.next_attempt_id if self.last else None
        if not attempt_id:
            return None
        return next(
            (
                item
                for item in self.attempts()
                if item.attempt_id == attempt_id and item.status != "withdrawn"
            ),
            None,
        )

    def list_events(self) -> list[EventSummary]:
        return [self.orchestrator._event_summary(event) for event in self.repository.list_events()]

    def agent_view(self, conversation_id: str) -> Optional[AgentConversationView]:
        return self.orchestrator.agent_view(conversation_id)

    def execute_action(self, event_id: str, action: str, note: str) -> EventSummary:
        event = self.repository.get_event(event_id)
        if event is None:
            raise ValueError("event not found")
        request = ServiceActionRequest.model_validate(
            {"action": action, "parameters": {"note": note}}
        )
        self.repository.add_service_action(event_id, request.action, request.parameters)
        self.repository.add_audit(
            str(event["conversation_id"]),
            "service_action",
            {
                "event_id": event_id,
                "action": request.action,
                "parameters": request.parameters,
                "actor": "unauthenticated_agent_api",
            },
        )
        ticket_event = {
            "reply": "agent_replied",
            "create_after_sales": "action_completed",
            "close": "action_completed",
        }.get(request.action)
        if ticket_event:
            self.orchestrator.record_ticket_result(
                str(event["conversation_id"]),
                TicketResultRequest(event=ticket_event, note=request.parameters.get("note")),
            )
        updated = self.repository.get_event(event_id)
        assert updated is not None
        return self.orchestrator._event_summary(updated)

    def reset(self) -> None:
        self.repository = MemoryRepository()
        self.orchestrator = ConversationOrchestrator(self.repository, get_settings())
        self.conversation_id = None
        self.last = None
        self.transcript = []
        self.selected_event_id = None


def panel_for_state(api_state: Optional[str]) -> str:
    if api_state in {"ASK", "RESOLVE"}:
        return "ask"
    if api_state == "GUIDE":
        return "guide"
    if api_state in {"BLOCK", "HANDOFF"}:
        return "risk"
    return "start"
