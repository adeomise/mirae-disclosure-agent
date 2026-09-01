"""Pattern B (Section Summary) 통합 테스트.

질문 문자열에서 공통 실행 Contract(AgentExecutionResult)와 공개 응답(AnswerResponse)까지의
흐름을 본다. 합성 manifest와 합성 corpus만 사용하며 실제 공시 XML, 실제 기업명, 실제 수치는
쓰지 않는다.

Parser 경계에 대한 메모:
    합성 corpus의 XML은 경로 해석을 시험하기 위한 최소 파일이다. Section 구조를 담은 입력은
    합성 parsed document fixture로 대신하며, Pattern A 통합 테스트가 같은 방식을 쓴다.

무엇을 고정하는가:
    Pattern B의 결과 모양이다. 최소 Fact 0, 요약 항목 수만큼의 Evidence, 계산 없음이
    확정 요건이다(docs/agent_design/evidence_and_answerability.md 유형별 최소 요건).
    건수만 세지 않고 항목과 Evidence가 서로를 가리키는지도 본다. 연결이 끊긴 채 개수만 맞는
    결과가 나올 수 있기 때문이다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.handlers import document_summary as pattern_b
from disclosure_agent.agent.handlers.document_summary import handle_document_summary
from disclosure_agent.agent.query_analyzer import QueryPlan, build_query_plan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    SectionScope,
    SummaryItemType,
)

SYNTHETIC_QUESTION = "테스트반도체 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

SYNTHETIC_COMPANIES = ("테스트반도체",)


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(SYNTHETIC_QUESTION, known_companies=SYNTHETIC_COMPANIES)


@pytest.fixture
def parsed_document_stub(monkeypatch: pytest.MonkeyPatch):
    """
    parser 호출을 합성 문서로 대체한다.

    대체 지점은 파싱을 실제로 부르는 handler다. Pattern A가 agent.period_retrieval을 대체하는
    것과 같은 자리이며, Pattern B는 periodic.service를 직접 부른다.
    """

    def _install(document: dict[str, Any]) -> None:
        monkeypatch.setattr(
            pattern_b,
            "load_parsed_document",
            lambda document_path: document,
        )

    return _install


def rewrite_manifest(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 1. 정상 흐름
# --------------------------------------------------------------------------


def test_handle_document_summary_returns_answerable_with_evidence(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None

    assert len(result.document_summaries) == 1

    summary = result.document_summaries[0]

    assert summary.doc_id == "periodic_SYNTHETIC0001"
    assert summary.section_scope is SectionScope.FACILITY_INVESTMENT
    assert summary.section_path == ["II. 사업의 내용", "3. 원재료 및 생산설비"]


def test_pattern_b_result_shape_has_no_facts_or_calculations(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    Pattern B는 값을 뽑지도 계산하지도 않는다.

    Fact가 실리면 요약 결과 안에 Pattern A가 회계기준·기간을 확인해 고르는 값과 다른 수치가
    섞인다. Calculation이 실리면 원문에 적힌 비율을 다시 계산한 것이 된다
    (docs/agent_design/agent_patterns.md Pattern B 출력 요구사항).
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.facts == []
    assert result.calculations == []
    assert result.comparisons == []
    assert result.event_histories == []
    assert result.events == []


def test_every_summary_item_has_its_own_evidence(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    요약 항목마다 Evidence를 갖는다.

    Pattern B의 검증 조건 2이며, 건수만 세면 연결이 끊긴 결과도 통과한다.
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    summary = result.document_summaries[0]
    known = {evidence.evidence_id for evidence in result.evidence}

    assert len(result.evidence) == len(summary.items)

    for item in summary.items:
        assert item.evidence_ids
        assert set(item.evidence_ids) <= known


def test_summary_evidence_points_inside_the_requested_scope(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    요약이 지정 범위를 벗어나지 않았는지 본다(Pattern B 검증 조건 1).

    합성 문서에는 재무제표 주석 Section이 함께 있다. Q04의 주요 실패 모드가 그 Section의
    투자 항목까지 끌어오는 것이며, 그 확산은 건수만으로는 드러나지 않는다.
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    for evidence in result.evidence:
        assert evidence.section_path == ["II. 사업의 내용", "3. 원재료 및 생산설비"]
        assert evidence.raw_text
        assert "요약 범위 밖" not in evidence.raw_text


def test_paragraph_and_table_evidence_use_distinct_location_types(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """본문 근거와 표 근거를 한 종류로 뭉개지 않는다."""
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    summary = result.document_summaries[0]
    by_id = {evidence.evidence_id: evidence for evidence in result.evidence}

    for item in summary.items:
        evidence = by_id[item.evidence_ids[0]]

        if item.item_type is SummaryItemType.PARAGRAPH:
            assert evidence.location_type is EvidenceLocationType.TEXT
        else:
            assert evidence.location_type is EvidenceLocationType.TABLE
            assert evidence.provenance is not None
            assert evidence.provenance.table_id


def test_result_is_deterministic_across_runs(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    같은 입력은 같은 식별자를 만든다.

    식별자가 실행마다 달라지면 결과를 대조할 수 없고, 회귀 측정이 성립하지 않는다.
    """
    parsed_document_stub(summary_document)

    first = handle_document_summary(
        plan, corpus_root=nfd_corpus_root, manifest_path=synthetic_manifest_path
    )
    second = handle_document_summary(
        plan, corpus_root=nfd_corpus_root, manifest_path=synthetic_manifest_path
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --------------------------------------------------------------------------
# 2. 문서 선택
# --------------------------------------------------------------------------


def test_zero_candidates_are_reported_as_not_found(
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    조건에 맞는 공시가 없으면 다른 연도의 문서로 대신하지 않는다.

    manifest 전체와 대조한 판정이므로 실행은 끝났고 답이 없다고 확정할 수 있다
    (docs/agent_design/evidence_and_answerability.md의 [4]단계).
    """
    parsed_document_stub(summary_document)

    plan = build_query_plan(
        "테스트반도체 2019년 사업보고서에서 설비 투자 관련 내용 정리해줘.",
        known_companies=SYNTHETIC_COMPANIES,
    )

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.document_summaries == []
    assert result.evidence == []


def test_correction_filings_are_excluded_from_selection(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    같은 회계연도의 정정본이 있어도 후보는 원공시 1건이다.

    Pattern A가 이미 쓰고 있는 조회 규칙(filter_filings의 기본값)을 그대로 쓴다. 판본 선택
    정책(D5)은 미정이며 이 Pattern이 새로 정하지 않는다. 합성 manifest에는 FY2025 사업보고서의
    정정본이 들어 있으므로, 그것이 후보에 섞이면 ambiguous_filing으로 막혔을 것이다.
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.document_summaries[0].doc_id == "periodic_SYNTHETIC0001"


def test_multiple_candidates_are_not_narrowed_by_receipt_number(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    manifest_records: list[dict[str, Any]],
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    같은 조건의 원공시가 둘이면 접수번호가 큰 쪽을 고르지 않는다.

    locate_filing은 그런 경우 최신 접수번호를 고르지만, 요약 대상이 어느 문서였는지는 결과만
    봐서는 드러나지 않는다. 그래서 이 Pattern은 후보가 정확히 1건일 때만 진행한다.
    """
    parsed_document_stub(summary_document)

    duplicate = copy.deepcopy(
        next(
            record
            for record in manifest_records
            if record["doc_id"] == "periodic_SYNTHETIC0001"
        )
    )
    duplicate["doc_id"] = "periodic_SYNTHETIC0021"
    duplicate["rcept_no"] = "SYNTHETIC0021"

    rewrite_manifest(synthetic_manifest_path, [*manifest_records, duplicate])

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_detail.reason == "ambiguous_filing"
    assert result.document_summaries == []
    assert result.evidence == []


def test_missing_section_in_a_found_document_is_not_found(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    문서는 찾았는데 그 안에 범위가 없으면 NOT_FOUND다.

    문서 부재(OUT_OF_DATA_RANGE 계열)와 구분한다. 문서가 있는데 찾지 못한 것이므로 검색 품질
    문제일 수 있고, "그 내용을 보유하고 있지 않다"는 확정 진술로 말하지 않는다.
    """
    document = copy.deepcopy(summary_document)
    document["sections"] = [
        section
        for section in document["sections"]
        if section["section_id"] != "section_0003"
    ]

    parsed_document_stub(document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.answerability_detail.stage == "section_navigation"
    assert result.answerability_detail.reason == "no_matching_section"


def test_unreadable_document_is_blocked_not_unanswerable(
    plan: QueryPlan,
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    문서를 열지 못한 것은 답변 가능성 판정이 아니다.

    UNANSWERABLE로 실으면 "코퍼스에 답이 없다"는 뜻이 되어 사실과 달라진다.
    """

    def explode(document_path):
        raise ValueError(f"XML 파싱에 실패했습니다: {document_path}")

    monkeypatch.setattr(pattern_b, "load_parsed_document", explode)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "parsing"
    assert result.execution_issue.reason == "parse_failed"
    assert result.answerability_status is None


# --------------------------------------------------------------------------
# 3. Router 경유 + 공개 응답
# --------------------------------------------------------------------------


def test_router_reaches_pattern_b_from_the_question_string(
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(summary_document)

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.document_summaries) == 1


def test_answer_response_states_the_document_and_the_scope(
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    문장은 확정된 결과에 적힌 것만 말한다.

    어느 문서의 어느 범위였는지가 문장에 없으면, 받는 쪽이 요약의 경계를 알 수 없다.
    """
    parsed_document_stub(summary_document)

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None

    assert "사업보고서" in response.answer
    assert "3. 원재료 및 생산설비" in response.answer
    assert "설비 투자" in response.answer

    # 영문 enum 값이 한국어 문장에 섞이지 않는다.
    assert "facility_investment" not in response.answer
    assert "paragraph" not in response.answer

    # 근거는 공개 응답에도 실린다. Fact는 만들지 않았으므로 비어 있다.
    assert response.facts == []
    assert len(response.evidence) == len(result.evidence)
