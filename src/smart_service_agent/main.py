from __future__ import annotations

from typing import Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pymongo.errors import PyMongoError

from smart_service_agent import __version__
from smart_service_agent.config import get_settings
from smart_service_agent.intent import IntentProvider
from smart_service_agent.knowledge import KnowledgeProvider
from smart_service_agent.models import (
    AgentConversationView,
    AttemptCreateRequest,
    AttemptRecord,
    AttemptUpdateRequest,
    CaseRecord,
    CaseRevisionRequest,
    ConsumerResponse,
    ConversationRequest,
    EventSummary,
    FeedbackRequest,
    HandoffDecision,
    InsightMetric,
    InsightsResponse,
    RiskLockReleaseRequest,
    ServiceActionRequest,
    StoredConversation,
    TicketRecord,
    TicketResultRequest,
)
from smart_service_agent.orchestration import ConversationOrchestrator
from smart_service_agent.repository import MongoRepository, StorageRepository
from smart_service_agent.ui_assets import ui_file


def create_app(
    repository: Optional[StorageRepository] = None,
    intent_provider: Optional[IntentProvider] = None,
    knowledge_provider: Optional[KnowledgeProvider] = None,
) -> FastAPI:
    settings = get_settings()
    repository = repository or MongoRepository(
        settings.mongodb_uri,
        settings.mongodb_database,
        settings.mongodb_timeout_ms,
    )
    orchestrator = ConversationOrchestrator(
        repository, settings, intent_provider, knowledge_provider
    )
    application = FastAPI(title=settings.app_name, version=__version__)

    @application.exception_handler(PyMongoError)
    async def handle_database_error(_request: Request, _error: PyMongoError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "database temporarily unavailable"},
        )

    @application.get("/health", tags=["system"])
    def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    @application.post(
        "/v1/agent/conversations/{conversation_id}/risk-lock/release",
        response_model=StoredConversation,
        tags=["agent"],
    )
    def release_risk_lock(
        conversation_id: str, request: RiskLockReleaseRequest
    ) -> StoredConversation:
        conversation = orchestrator.release_risk_lock(
            conversation_id, request.reason, request.operator
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation

    @application.post(
        "/v1/conversations",
        response_model=ConsumerResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["consumer"],
    )
    def start_conversation(request: ConversationRequest) -> ConsumerResponse:
        return orchestrator.start(request)

    @application.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=ConsumerResponse,
        tags=["consumer"],
    )
    def continue_conversation(
        conversation_id: str, request: ConversationRequest
    ) -> ConsumerResponse:
        response = orchestrator.continue_conversation(conversation_id, request)
        if response is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return response

    @application.get(
        "/v1/conversations/{conversation_id}/case", response_model=CaseRecord, tags=["consumer"]
    )
    def get_case(conversation_id: str) -> CaseRecord:
        conversation = repository.get_conversation(conversation_id)
        if conversation is None or conversation.case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return conversation.case

    @application.post(
        "/v1/conversations/{conversation_id}/case/revisions",
        response_model=CaseRecord,
        tags=["consumer"],
    )
    def revise_case(conversation_id: str, request: CaseRevisionRequest) -> CaseRecord:
        case = orchestrator.revise_case(conversation_id, request)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    @application.get(
        "/v1/conversations/{conversation_id}/attempts",
        response_model=list[AttemptRecord],
        tags=["consumer"],
    )
    def list_attempts(conversation_id: str) -> list[AttemptRecord]:
        conversation = repository.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation.attempts

    @application.post(
        "/v1/conversations/{conversation_id}/attempts",
        response_model=AttemptRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["consumer"],
    )
    def create_attempt(conversation_id: str, request: AttemptCreateRequest) -> AttemptRecord:
        try:
            attempt = orchestrator.create_attempt(conversation_id, request)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if attempt is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return attempt

    @application.patch(
        "/v1/conversations/{conversation_id}/attempts/{attempt_id}",
        response_model=AttemptRecord,
        tags=["consumer"],
    )
    def update_attempt(
        conversation_id: str, attempt_id: str, request: AttemptUpdateRequest
    ) -> AttemptRecord:
        try:
            attempt = orchestrator.update_attempt(conversation_id, attempt_id, request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if attempt is None:
            raise HTTPException(status_code=404, detail="attempt not found")
        return attempt

    @application.post(
        "/v1/conversations/{conversation_id}/handoff",
        response_model=Union[EventSummary, ConsumerResponse],
        tags=["consumer"],
    )
    def decide_handoff(
        conversation_id: str, decision: HandoffDecision
    ) -> Union[EventSummary, ConsumerResponse]:
        conversation = repository.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if not decision.accepted:
            repository.add_audit(
                conversation_id, "handoff_declined", {"safety_reminder_retained": True}
            )
            return ConsumerResponse(
                conversation_id=conversation_id,
                result_id=conversation.last_result_id,
                state=conversation.state,
                message=(
                    "已记录你暂不转人工。请保持设备断电并停止使用，不要拆机或再次通电测试；"
                    "如出现起火、冒烟或漏液，请远离可燃物并联系人工客服处理。"
                    if conversation.empathy_card.risk_level.value == "high"
                    else (
                        "已记录你暂不转人工。由于当前缺少可靠依据，我不会猜测答案；"
                        "你可以补充更多信息后再试。"
                    )
                ),
            )
        event = orchestrator.create_handoff(conversation_id, decision.idempotency_key)
        if event is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return event

    @application.get("/v1/events/{event_id}", response_model=EventSummary, tags=["consumer"])
    def get_event(event_id: str) -> EventSummary:
        event = repository.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return orchestrator._event_summary(event)

    @application.post(
        "/v1/conversations/{conversation_id}/feedback",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["consumer"],
    )
    def submit_feedback(conversation_id: str, feedback: FeedbackRequest) -> None:
        conversation = repository.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if feedback.result_id != conversation.last_result_id:
            raise HTTPException(
                status_code=409, detail="result_id does not match the latest conversation result"
            )
        repository.record_feedback(
            conversation_id, feedback.result_id, feedback.resolved, feedback.comment
        )
        repository.add_audit(
            conversation_id,
            "feedback_recorded",
            {"resolved": feedback.resolved, "training_use": False},
        )

    @application.get("/v1/agent/events", response_model=list[EventSummary], tags=["agent"])
    def list_agent_events() -> list[EventSummary]:
        return [orchestrator._event_summary(event) for event in repository.list_events()]

    @application.get(
        "/v1/agent/conversations/{conversation_id}",
        response_model=AgentConversationView,
        tags=["agent"],
    )
    def get_agent_conversation(conversation_id: str) -> AgentConversationView:
        view = orchestrator.agent_view(conversation_id)
        if view is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return view

    @application.post(
        "/v1/agent/events/{event_id}/actions", response_model=EventSummary, tags=["agent"]
    )
    def execute_service_action(event_id: str, request: ServiceActionRequest) -> EventSummary:
        event = repository.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        repository.add_service_action(event_id, request.action, request.parameters)
        repository.add_audit(
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
            orchestrator.record_ticket_result(
                str(event["conversation_id"]),
                TicketResultRequest(event=ticket_event, note=request.parameters.get("note")),
            )
        updated = repository.get_event(event_id)
        assert updated is not None
        return orchestrator._event_summary(updated)

    @application.post(
        "/v1/agent/conversations/{conversation_id}/ticket/results",
        response_model=TicketRecord,
        tags=["agent"],
    )
    def record_ticket_result(conversation_id: str, request: TicketResultRequest) -> TicketRecord:
        ticket = orchestrator.record_ticket_result(conversation_id, request)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        return ticket

    @application.get("/ui", include_in_schema=False)
    @application.get("/ui/", include_in_schema=False)
    def ui_home() -> FileResponse:
        return ui_file("index.html")

    @application.get("/ui/{asset_name}", include_in_schema=False)
    def ui_asset(asset_name: str) -> FileResponse:
        return ui_file(asset_name)

    @application.get("/workspace/consumer", response_class=HTMLResponse, tags=["workspace"])
    def consumer_workspace() -> FileResponse:
        return ui_file("index.html")

    @application.get("/workspace/agent", response_class=HTMLResponse, tags=["workspace"])
    def agent_workspace() -> FileResponse:
        return ui_file("index.html")

    @application.get("/v1/insights/overview", response_model=InsightsResponse, tags=["brand"])
    def get_insights(
        data_classification: Literal["demo"] = Query(default="demo"),
    ) -> InsightsResponse:
        conversations = repository.list_conversations()
        feedback = repository.list_feedback()
        count = len(conversations)
        start = min((item.created_at for item in conversations), default=None)
        end = max((item.updated_at for item in conversations), default=None)
        resolved_count = sum(1 for item in feedback if item["resolved"])
        event_count = len(repository.list_events())
        repeat_count = sum(
            1 for item in conversations if len(item.messages) != len(set(item.messages))
        )

        def metric(value: float, sample_size: int) -> InsightMetric:
            return InsightMetric(
                value=value,
                sample_size=sample_size,
                period_start=start,
                period_end=end,
                data_classification=data_classification,
            )

        unresolved: dict[str, int] = {}
        for item in conversations:
            if item.state.value != "RESOLVE":
                key = item.empathy_card.surface_issue
                unresolved[key] = unresolved.get(key, 0) + 1
        return InsightsResponse(
            consultation_count=metric(float(count), count),
            resolution_rate=metric(
                resolved_count / len(feedback) if feedback else 0.0, len(feedback)
            ),
            handoff_rate=metric(event_count / count if count else 0.0, count),
            repeat_question_rate=metric(repeat_count / count if count else 0.0, count),
            top_unresolved_issues=[
                {"issue": key, "count": value}
                for key, value in sorted(
                    unresolved.items(), key=lambda item: item[1], reverse=True
                )[:5]
            ],
        )

    return application


app = create_app()
