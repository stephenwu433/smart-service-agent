from __future__ import annotations

from typing import Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
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

    @application.get("/workspace/consumer", response_class=HTMLResponse, tags=["workspace"])
    def consumer_workspace() -> str:
        return _consumer_workspace_html()

    @application.get("/workspace/agent", response_class=HTMLResponse, tags=["workspace"])
    def agent_workspace() -> str:
        return _agent_workspace_html()

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


def _consumer_workspace_html() -> str:
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>消费者服务工作区</title><style>
body{font:16px system-ui;max-width:760px;margin:3rem auto;padding:0 1rem}
textarea,input,button{font:inherit;padding:.7rem;margin:.35rem 0;width:100%}
button{width:auto}.result{white-space:pre-wrap;background:#f5f5f5;padding:1rem}</style>
<h1>安克智能服务助手（比赛演示）</h1><p>面向充电器、移动电源等设备，每次只补充一个必要信息，并只调整一个条件。你也可以随时转人工。</p>
<input id="product" placeholder="产品名称（可选）">
<textarea id="message" rows="5" placeholder="描述问题和已经尝试的方法"></textarea>
<button onclick="send()">开始排查</button><div id="result" class="result" aria-live="polite"></div>
<script>async function send(){
const payload={message:message.value,product:product.value||null};
const r=await fetch('/v1/conversations',{method:'POST',
headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
const b=await r.json();result.textContent=b.message||b.detail;}</script></html>"""


def _agent_workspace_html() -> str:
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>人工客服工作台</title>
<style>body{font:16px system-ui;max-width:900px;margin:3rem auto;padding:0 1rem}
li{padding:.6rem;border-bottom:1px solid #ddd}</style>
<h1>人工客服工作台</h1><p>队列按风险优先级和等待时间排列。回复、动作完成与用户确认解决分别记录。</p>
<button onclick="load()">刷新队列</button><ol id="events" aria-live="polite"></ol>
<script>async function load(){const r=await fetch('/v1/agent/events');
const b=await r.json();events.innerHTML=b.map(x=>
`<li><b>${x.status}</b> ${x.reason} <small>${x.event_id}</small></li>`
).join('')}</script></html>"""
