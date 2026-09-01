"""Pattern B handler 입구 가드 테스트.

Router는 intent 하나만 보고 handler를 고른다. document_summary로 분류된 계획이 전부 이번
구현 범위인 것은 아니므로, 다루지 못하는 계획을 handler 입구에서 막는다. Pattern C·D가 같은
자리에 같은 가드를 두고 있다.

여기서 막는 것과 query_analyzer가 막는 것은 다르다:
    query_analyzer는 질문에서 계획을 만들 수 없을 때 막고, 여기서는 만들어진 계획을 실행할
    수 없을 때 막는다. 계획을 직접 만들어 넘기는 호출자(테스트·후속 Pattern)가 있으므로 두
    자리가 모두 필요하다.

코퍼스를 열지 않는다. 입구에서 막히므로 manifest 경로도 corpus 경로도 쓰이지 않는다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.handlers.document_summary import (
    REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS,
    REASON_UNSUPPORTED_SUMMARY_PERIOD,
    handle_document_summary,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_DOCUMENT_SUMMARY,
    QueryPlan,
)
from disclosure_agent.common.enums import (
    ChainPolicy,
    ExecutionStatus,
    QueryAccountingScope,
    SectionScope,
)


def summary_plan(**overrides) -> QueryPlan:
    defaults = {
        "question": "테스트바이오 2025년 사업보고서 설비 투자 정리해줘",
        "companies": ("테스트바이오",),
        "years": (2025,),
        "intent": INTENT_DOCUMENT_SUMMARY,
        "document_class": "periodic",
        "document_subtype": "annual",
        "accounting_scope": QueryAccountingScope.NOT_APPLICABLE.value,
        "metric": None,
        "section_scope": SectionScope.FACILITY_INVESTMENT,
    }

    return QueryPlan(**{**defaults, **overrides})


def run(plan: QueryPlan):
    return handle_document_summary(plan, corpus_root="unused", manifest_path="unused")


def test_plan_without_a_section_scope_cannot_be_built() -> None:
    """
    범위가 빈 요약 계획은 만들 수 없다.

    handler 입구가 아니라 계획 생성 시점에서 막는다. 그 상태를 만들 수 있게 두면 계획을
    실행하는 쪽이 어디를 요약할지 골라야 하고, 무엇을 골랐는지는 어디에도 남지 않는다.
    """
    with pytest.raises(ValueError, match="section_scope"):
        summary_plan(section_scope=None)


def test_event_class_summary_is_blocked_at_the_entry() -> None:
    """
    거래소공시 단건 요약(Q06 계열)은 판본 선택 정책(D5)이 미정이라 열려 있지 않다.

    가까운 문서를 골라 요약하면 원공시와 정정본 중 무엇을 요약했는지가 결과에 남지 않는다.
    """
    result = run(
        summary_plan(
            document_class="event",
            document_subtype="신규시설투자등",
            chain_policy=ChainPolicy.LATEST_VALID,
        )
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS

    # 코퍼스를 보지 않았으므로 답이 있는지 없는지도 판정하지 않는다.
    assert result.answerability_status is None
    assert result.answerability_reason is None


def test_multi_period_summary_is_blocked_at_the_entry() -> None:
    """단일 문서 요약은 회계연도 하나를 요구한다. 한 기간을 골라 답하지 않는다."""
    result = run(summary_plan(years=(2024, 2025)))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_PERIOD


def test_entry_guard_does_not_invent_a_summary() -> None:
    """막힌 결과에는 요약도 근거도 없다."""
    result = run(summary_plan(document_class="holding"))

    assert result.document_summaries == []
    assert result.evidence == []
    assert result.facts == []
    assert result.calculations == []
    assert result.comparisons == []
