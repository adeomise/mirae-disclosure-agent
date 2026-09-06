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
    REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE,
    SUPPORTED_SECTION_SCOPE_BUNDLES,
    handle_document_summary,
)
from disclosure_agent.agent.query_analyzer import (
    BUSINESS_PROFILE_SECTION_SCOPES,
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


def test_non_annual_periodic_summary_is_blocked_at_the_entry() -> None:
    """
    정기공시라도 사업보고서가 아니면 실행하지 않는다.

    Section 규칙(periodic.sections)은 사업보고서 서식을 전제로 만들어져 있어, 반기·분기보고서에서
    무엇을 고를지 보증되지 않는다. 지금은 분기·반기 표현을 query_analyzer가 의도 분기보다 앞에서
    거절하지만, 계획을 직접 만드는 호출자에게는 그 방어가 없다.
    """
    result = run(summary_plan(document_subtype="half"))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS
    assert result.document_summaries == []
    assert result.evidence == []


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


# --------------------------------------------------------------------------
# 범위 묶음(section_scopes)의 계획 불변식
# --------------------------------------------------------------------------


def test_plan_with_only_a_scope_bundle_can_be_built() -> None:
    plan = summary_plan(
        section_scope=None,
        section_scopes=(SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS),
    )

    assert plan.section_scope is None
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)


def test_scope_and_scope_bundle_cannot_coexist() -> None:
    """범위 하나와 범위 묶음을 함께 담으면 어느 쪽이 정본인지 계획만 봐서는 알 수 없다."""
    with pytest.raises(ValueError, match="함께 담을 수 없습니다"):
        summary_plan(
            section_scope=SectionScope.FACILITY_INVESTMENT,
            section_scopes=(SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS),
        )


def test_scope_bundle_is_only_for_document_summary_plans() -> None:
    """다른 의도의 handler는 이 값을 읽지 않으므로 요청한 범위가 조용히 사라진다."""
    with pytest.raises(ValueError, match="단일 문서 요약 계획에서만"):
        QueryPlan(
            question="테스트반도체 2025년 연결 기준 매출액이 얼마야?",
            companies=("테스트반도체",),
            years=(2025,),
            section_scopes=(SectionScope.BUSINESS_OVERVIEW,),
        )


def test_scope_bundle_rejects_a_repeated_scope() -> None:
    with pytest.raises(ValueError, match="거듭"):
        summary_plan(
            section_scope=None,
            section_scopes=(SectionScope.PRODUCTS, SectionScope.PRODUCTS),
        )


def test_scope_bundle_rejects_a_bare_scope() -> None:
    """SectionScope은 StrEnum이라 낱개로 넘기면 글자 단위로 순회된다."""
    with pytest.raises(TypeError, match="튜플"):
        summary_plan(section_scope=None, section_scopes=SectionScope.PRODUCTS)


# --------------------------------------------------------------------------
# 범위 묶음 경로의 입구: Q05의 묶음 하나만 받는다
# --------------------------------------------------------------------------
#
# 범위 묶음 경로는 정정본을 후보에 포함하는 조회 규칙을 쓴다. 묶음이 비어 있지 않다는 것만으로
# 그 경로를 열면 다른 묶음이 그 규칙을 물려받는다. 그래서 Q05의 묶음과 정확히 같을 때만 열고,
# 다른 묶음은 입구에서 막는다. 범위 하나의 요약으로 바꿔 읽지도 않는다.


def test_canonical_scope_bundle_passes_the_entry_guard() -> None:
    """Q05의 묶음은 입구를 지나 공시 조회 단계까지 간다. 코퍼스가 없으므로 거기서 멈춘다."""
    result = run(summary_plan(section_scope=None, section_scopes=BUSINESS_PROFILE_SECTION_SCOPES))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "filing_discovery"


def test_single_element_bundle_is_blocked_at_the_entry() -> None:
    """범위 하나짜리 묶음은 Q05가 아니다. 범위 하나의 요약으로 바꿔 읽지 않고 막는다."""
    result = run(
        summary_plan(section_scope=None, section_scopes=(SectionScope.FACILITY_INVESTMENT,))
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE
    assert result.document_summaries == []
    assert result.evidence == []


def test_arbitrary_scope_bundle_is_blocked_at_the_entry() -> None:
    result = run(
        summary_plan(
            section_scope=None,
            section_scopes=(SectionScope.FACILITY_INVESTMENT, SectionScope.PRODUCTS),
        )
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE


def test_scalar_scope_plan_still_passes_the_entry_guard() -> None:
    """범위 하나의 계획(Q04)은 이전과 같이 입구를 지나 공시 조회 단계까지 간다."""
    result = run(summary_plan(section_scope=SectionScope.FACILITY_INVESTMENT))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "filing_discovery"


# --------------------------------------------------------------------------
# 실행하는 범위 묶음은 둘뿐이다
# --------------------------------------------------------------------------
#
# 부분집합으로 두면 (주요 제품,) 하나짜리 묶음이 통과한다. 사업 개요 없이 제품 절만 요약하는
# 모양은 이 구현에서 확인된 적이 없고, 질의 해석 계층도 그 계획을 만들지 않는다. 계획을 직접
# 만드는 호출자를 막는 것이 입구 가드의 몫이므로 받는 묶음을 열거한다.


def test_business_overview_only_bundle_passes_the_entry_guard() -> None:
    """사업만 물은 질문의 묶음은 입구를 지나 공시 조회 단계까지 간다."""
    result = run(
        summary_plan(section_scope=None, section_scopes=(SectionScope.BUSINESS_OVERVIEW,))
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "filing_discovery"


def test_products_only_bundle_is_blocked_at_the_entry() -> None:
    """
    제품 범위 하나짜리 묶음은 받지 않는다.

    Q05의 범위에 속한다는 것만으로 열면 이 묶음이 통과한다. 사업 개요 없이 제품 절만
    요약하는 모양은 확인된 적이 없으므로 fail-closed로 남긴다.
    """
    result = run(summary_plan(section_scope=None, section_scopes=(SectionScope.PRODUCTS,)))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE
    assert result.document_summaries == []
    assert result.evidence == []


@pytest.mark.parametrize(
    "section_scopes",
    [
        # 차례가 다르다. 이 경로가 만든 묶음이 아니다.
        (SectionScope.PRODUCTS, SectionScope.BUSINESS_OVERVIEW),
        # Q05의 범위가 아닌 것이 섞였다.
        (SectionScope.BUSINESS_OVERVIEW, SectionScope.FACILITY_INVESTMENT),
        (SectionScope.FACILITY_INVESTMENT, SectionScope.BUSINESS_OVERVIEW),
        (SectionScope.FINANCIAL_STATEMENT,),
    ],
)
def test_unrelated_or_mixed_bundles_stay_blocked(
    section_scopes: tuple[SectionScope, ...],
) -> None:
    result = run(summary_plan(section_scope=None, section_scopes=section_scopes))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE


def test_supported_bundles_are_exactly_the_two_shapes() -> None:
    """
    받는 묶음을 값으로 고정한다.

    부분집합 판정으로 되돌아가면 이 단언이 깨진다. 그 되돌림은 결과의 형식으로는 드러나지
    않으므로(제품 절만 실은 요약도 정상적인 DocumentSummary다) 여기서 값으로 못 박는다.
    """
    assert SUPPORTED_SECTION_SCOPE_BUNDLES == (
        (SectionScope.BUSINESS_OVERVIEW,),
        (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS),
    )
    assert BUSINESS_PROFILE_SECTION_SCOPES in SUPPORTED_SECTION_SCOPE_BUNDLES
