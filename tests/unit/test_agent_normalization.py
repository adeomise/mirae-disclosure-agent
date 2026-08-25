"""Pattern 결과를 공통 실행 Contract로 옮기는 정규화 계층 테스트.

여기서 확인하려는 것:
- 실행이 끝난 뒤의 판정(COMPLETED)과 판정에 이르지 못한 중단(BLOCKED)을 가르는가
- 옮기지 못한 정보를 조용히 버리지 않고 conversion_gaps에 남기는가
- FinancialFact 변환에 기존 periodic.adapter를 그대로 쓰는가

정규화는 문서를 읽지 않는 순수 함수다. Pattern A는 합성 parsed document로, 대응표는 값
하나로 시험한다.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_CANDIDATES,
    REASON_NO_MATCHING_FILING,
    REASON_UNCONVERTIBLE_FACT,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    BlockedInfo,
)
from disclosure_agent.agent.handlers.single_fact import (
    SingleFactResult,
    build_result_from_document,
)
from disclosure_agent.agent.normalization import (
    ANSWERABILITY_BY_STOP,
    blocked_execution,
    normalize_single_fact_result,
    to_conversion_gap,
)
from disclosure_agent.agent.query_analyzer import QueryPlan, build_query_plan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)
from disclosure_agent.common.schemas import AnswerabilityDetail, ConversionGap
from disclosure_agent.event.contract_adapter import ContractGap
from disclosure_agent.periodic.adapter import adapt_financial_fact
from disclosure_agent.retrieval.metadata_filter import locate_filing

QUESTION = "테스트반도체 2025년 연결 기준 매출액이 얼마야?"

KNOWN_COMPANIES = ("테스트반도체",)


@pytest.fixture
def resolved_filing(synthetic_manifest_path: Path, nfd_corpus_root: Path):
    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    return plan, locate_filing(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=plan.company,
        document_class=plan.document_class,
        document_subtype=plan.document_subtype,
        year=plan.year,
    )


def _normalized(document: dict[str, Any], resolved_filing):
    plan, filing = resolved_filing

    return normalize_single_fact_result(
        build_result_from_document(document, plan=plan, filing=filing)
    )


# --------------------------------------------------------------------------
# 1. 정상 결과
# --------------------------------------------------------------------------


def test_single_fact_success_is_answerable(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    result = _normalized(synthetic_document, resolved_filing)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None
    assert len(result.facts) == 1
    assert len(result.evidence) == 1


def test_success_reuses_the_periodic_adapter(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """
    변환을 다시 구현하지 않는다.

    같은 변환이 두 벌이 되면 그때부터 어느 쪽이 정본인지 알 수 없고, 식별자가 갈리면
    Fact와 Evidence의 연결도 함께 끊긴다.
    """
    plan, filing = resolved_filing

    internal = build_result_from_document(synthetic_document, plan=plan, filing=filing)

    expected_fact, expected_evidence = adapt_financial_fact(
        internal.fact, synthetic_document
    )

    result = normalize_single_fact_result(internal)

    assert result.facts == [expected_fact]
    assert result.evidence == [expected_evidence]


def test_normalization_does_not_invent_evidence_provenance(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """
    EvidenceProvenance를 채우는 것은 periodic.adapter의 책임이다.

    정규화가 대신 채우면 같은 변환이 두 벌이 되고, 어느 쪽이 정본인지 알 수 없게 된다.
    adapter가 채우기 전까지 provenance는 None이며 그대로 전달한다.
    """
    result = _normalized(synthetic_document, resolved_filing)

    assert result.evidence[0].provenance is None


def test_fact_and_evidence_stay_linked(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """출처를 확인할 수 없는 Fact는 계산에 쓸 수 없다."""
    result = _normalized(synthetic_document, resolved_filing)

    assert result.facts[0].evidence_ids == [result.evidence[0].evidence_id]


def test_result_is_json_serializable(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    result = _normalized(synthetic_document, resolved_filing)

    dumped = result.model_dump(mode="json")

    assert json.loads(json.dumps(dumped, ensure_ascii=False)) == dumped
    assert dumped["execution_status"] == "COMPLETED"


def test_plan_warnings_are_preserved(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    plan, filing = resolved_filing

    warned = dataclasses.replace(plan, warnings=("합성 경고",))

    result = normalize_single_fact_result(
        build_result_from_document(synthetic_document, plan=warned, filing=filing)
    )

    assert result.warnings == ["합성 경고"]


# --------------------------------------------------------------------------
# 2. 실행은 끝났고 답을 못 찾은 경우
# --------------------------------------------------------------------------


def test_no_metric_evidence_is_insufficient_evidence(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """문서를 끝까지 읽었고 해당 지표의 셀이 없었다. 실행이 멈춘 것이 아니다."""
    for table in synthetic_document["tables"]:
        for row in table["rows"]:
            has_revenue_acode = any(
                cell.get("acode") == "ifrs-full_Revenue"
                for cell in row
            )
            for cell in row:
                cell.pop("acode", None)
            if has_revenue_acode:
                row[0]["text"] = "기타항목"

    result = _normalized(synthetic_document, resolved_filing)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE
    assert result.execution_issue is None
    assert result.facts == []

    assert result.answerability_detail.stage == STAGE_EXTRACTION
    assert result.answerability_detail.reason == "no_matching_label_fallback"
    assert result.answerability_detail.detail


def test_ambiguous_candidates_is_an_ambiguous_match(
    ambiguous_document: dict[str, Any],
    resolved_filing,
) -> None:
    """
    후보를 좁힐 근거가 없다는 판정까지는 끝났다.

    AMBIGUOUS_LINK가 아니라 AMBIGUOUS_MATCH다. 전자는 문서 사이의 '연결'이 모호하다는
    뜻이고, 여기서 모호한 것은 문서 하나 안의 셀 후보다.
    """
    for table in ambiguous_document["tables"]:
        table.pop("table_group_class", None)

    result = _normalized(ambiguous_document, resolved_filing)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_reason is not AnswerabilityReason.AMBIGUOUS_LINK

    assert result.answerability_detail.stage == STAGE_EXTRACTION
    assert result.answerability_detail.reason == REASON_AMBIGUOUS_CANDIDATES

    # 판정 근거는 손실이 아니므로 gap에 남기지 않는다.
    assert result.conversion_gaps == []


def test_answerability_judgement_keeps_its_diagnosis() -> None:
    """
    canonical reason은 서로 다른 정지 사유를 한 값으로 묶는다.

    NOT_FOUND만 보면 no_matching_filing인지 no_original_filing인지 알 수 없고, 후속 조치는
    서로 다르다. 원문 진단을 요약하지 않고 남긴다.
    """
    result = normalize_single_fact_result(
        SingleFactResult(
            plan=build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES),
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail="합성 사유",
            ),
        )
    )

    assert result.answerability_detail == AnswerabilityDetail(
        stage=STAGE_FILING_DISCOVERY,
        reason=REASON_NO_MATCHING_FILING,
        detail="합성 사유",
    )
    assert result.conversion_gaps == []


# --------------------------------------------------------------------------
# 3. 판정에 이르지 못하고 멈춘 경우
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        (STAGE_PARSING, "parse_failed"),
        (STAGE_EXTRACTION, "missing_acontext"),
        (STAGE_EXTRACTION, "missing_table_unit"),
        (STAGE_EXTRACTION, "untrusted_table_unit"),
        (STAGE_EXTRACTION, "unparsable_value"),
        (STAGE_EXTRACTION, "unsupported_metric"),
        (STAGE_FILING_DISCOVERY, "manifest_missing"),
        (STAGE_FILING_DISCOVERY, "path_unresolved"),
        (STAGE_FILING_DISCOVERY, "main_document_missing"),
        ("evidence", "missing_doc_id"),
    ],
)
def test_pipeline_stops_are_blocked(stage: str, reason: str) -> None:
    """
    값이 문서 안에 있는데 읽어내지 못한 것은 '코퍼스에 답이 없다'가 아니다.

    UNANSWERABLE로 보내면 검색 품질 문제와 파이프라인 고장이 한 지표로 묶인다.
    """
    result = normalize_single_fact_result(
        SingleFactResult(
            plan=build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES),
            blocked=BlockedInfo(stage=stage, reason=reason, detail="합성 사유"),
        )
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.execution_issue.stage == stage
    assert result.execution_issue.reason == reason
    assert result.execution_issue.detail == "합성 사유"

    # 답변 가능성을 판정하지 못했으므로 그 진단도 만들지 않는다. 멈춘 지점은
    # execution_issue가 담는다.
    assert result.answerability_detail is None


def test_unconvertible_fact_is_blocked_not_answerable(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """
    값은 확정했지만 공통 타입으로 옮기지 못한 경우다.

    추측해 채우면 어느 문서에서 나온 값인지 알 수 없는 Fact가 만들어진다.
    """
    plan, filing = resolved_filing

    internal = build_result_from_document(synthetic_document, plan=plan, filing=filing)

    without_doc_id = dataclasses.replace(
        internal,
        document={**internal.document, "metadata": {"corp_name": "테스트반도체(주)"}},
    )

    result = normalize_single_fact_result(without_doc_id)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == STAGE_CONTRACT_ADAPTATION
    assert result.execution_issue.reason == REASON_UNCONVERTIBLE_FACT
    assert result.facts == []


def test_blocked_execution_does_not_judge_answerability() -> None:
    result = blocked_execution("routing", "unimplemented_intent", "합성 사유")

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.execution_issue.reason == "unimplemented_intent"


def test_normalization_never_produces_failed(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """
    FAILED는 이 계층이 만들지 않는다.

    Pattern은 예상한 실패를 전부 blocked로 돌려주고, 예상하지 못한 예외는 삼키지 않는다.
    그 예외를 FAILED로 바꿀지는 API/Runtime 경계의 결정이다.
    """
    plan, filing = resolved_filing

    stops = [
        SingleFactResult(
            plan=plan,
            blocked=BlockedInfo(stage=stage, reason=reason, detail="합성 사유"),
        )
        for stage, reason in [
            *ANSWERABILITY_BY_STOP,
            (STAGE_PARSING, "parse_failed"),
        ]
    ]

    results = [
        normalize_single_fact_result(
            build_result_from_document(synthetic_document, plan=plan, filing=filing)
        ),
        *(normalize_single_fact_result(stop) for stop in stops),
    ]

    assert all(
        result.execution_status is not ExecutionStatus.FAILED for result in results
    )


# --------------------------------------------------------------------------
# 4. conversion_gaps
# --------------------------------------------------------------------------


def test_contract_gap_maps_to_conversion_gap_without_loss() -> None:
    """문자열로 합치지 않고 필드를 그대로 옮긴다."""
    gap = ContractGap(
        kind="raw_field_name",
        subject="contract_amount",
        dropped={"raw_field": "계약금액(원)"},
        detail="합성 사유",
    )

    converted = to_conversion_gap(gap)

    assert converted == ConversionGap(
        kind="raw_field_name",
        subject="contract_amount",
        dropped={"raw_field": "계약금액(원)"},
        detail="합성 사유",
        pending_decision=False,
        planned_contract_field="FieldChange.raw_field",
        is_temporary=True,
        contract_version="0.1",
    )


def test_gap_field_sets_stay_aligned() -> None:
    """
    두 타입의 필드가 어긋나면 새 필드가 조용히 사라진다.

    한쪽만 늘어난 상태로 통과하면 '옮기지 못한 것을 남긴다'는 전제가 깨진다.
    """
    contract_gap_fields = set(
        ContractGap(kind="k", subject="s").to_dict(),
    )

    assert contract_gap_fields == set(ConversionGap.model_fields)


# --------------------------------------------------------------------------
# 5. 대응표
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("stage", "reason"), list(ANSWERABILITY_BY_STOP))
def test_every_mapped_stop_completes_execution(stage: str, reason: str) -> None:
    """대응표에 있는 정지 사유는 전부 '실행은 끝났다'로 옮겨진다."""
    outcome = ANSWERABILITY_BY_STOP[(stage, reason)]

    result = normalize_single_fact_result(
        SingleFactResult(
            plan=QueryPlan(question="합성", companies=("합성",), year=2025),
            blocked=BlockedInfo(stage=stage, reason=reason, detail="합성 사유"),
        )
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is outcome.status
    assert result.answerability_reason is outcome.reason
    assert result.execution_issue is None

    # 대응표에 있는 사유는 전부 원문 진단을 남긴다.
    assert result.answerability_detail.stage == stage
    assert result.answerability_detail.reason == reason
    assert result.answerability_detail.detail == "합성 사유"


def test_every_mapped_stop_declares_a_canonical_reason() -> None:
    """
    UNANSWERABLE/UNRESOLVED에는 평가가 대조할 canonical reason이 있어야 한다.

    Contract에 AMBIGUOUS_MATCH가 생기기 전에는 ambiguous 판정만 reason이 비어 있었다.
    """
    assert all(
        outcome.reason is not None for outcome in ANSWERABILITY_BY_STOP.values()
    )
