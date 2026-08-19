"""Routing for the implemented Q01 periodic vertical slice."""

from disclosure_agent.agent.query_analyzer import (
    analyze_q01_question,
    analyze_q02_question,
    analyze_q13_question,
)
from disclosure_agent.common.schemas import AnswerResponse, AskRequest
from disclosure_agent.event.service import EventQ02Service, EventQ13Service
from disclosure_agent.periodic.service import PeriodicQ01Service


class Q01VerticalSliceRouter:
    """Route only supported Q01 questions to the periodic service."""

    def __init__(self, periodic_service: PeriodicQ01Service) -> None:
        self.periodic_service = periodic_service

    def answer(self, request: AskRequest) -> AnswerResponse | None:
        """Return None when the question does not match the Q01 contract."""
        query_plan = analyze_q01_question(request.question)
        if query_plan is None:
            return None
        return self.periodic_service.answer(query_plan)


class Q02VerticalSliceRouter:
    """Route only supported Q02 questions to the event service."""

    def __init__(self, event_service: EventQ02Service) -> None:
        self.event_service = event_service

    def answer(self, request: AskRequest) -> AnswerResponse | None:
        query_plan = analyze_q02_question(request.question)
        if query_plan is None:
            return None
        return self.event_service.answer(query_plan)


class Q13VerticalSliceRouter:
    """Route only supported Q13 questions to the event-history service."""

    def __init__(self, event_history_service: EventQ13Service) -> None:
        self.event_history_service = event_history_service

    def answer(self, request: AskRequest) -> AnswerResponse | None:
        query_plan = analyze_q13_question(request.question)
        if query_plan is None:
            return None
        return self.event_history_service.answer(query_plan)
