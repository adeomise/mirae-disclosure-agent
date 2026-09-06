"""Pattern B 기업 소개 요약(Q05 계열) 통합 테스트.

"<기업>는 무슨 사업을 하고 주력 제품이 뭐야?" 모양의 질문이 사업보고서 1건의 두 범위(사업의
개요, 주요 제품)를 DocumentSummary 2건으로 싣는 흐름을 본다. 합성 manifest와 합성 corpus만
사용하며 실제 공시 XML, 실제 기업명, 실제 수치는 쓰지 않는다.

무엇을 고정하는가:
    - 문서 1건에서 범위 둘을 각각 확정하고, 항목마다 Evidence를 갖는가
    - 범위 하나라도 확정하지 못하면 부분 답을 내지 않고 전부 멈추는가
    - 이 경로는 정정본을 후보에 포함해 원본·정정본이 함께 있으면 어느 것도 고르지 않는가
    - 범위 하나의 경로(Q04)의 조회 규칙은 그대로인가
    - 실제로 선택된 FilingRef의 문서 metadata가 source_documents로 그대로 따라오는가

합성 요약 문서(summary_document)에는 주요 제품 Section만 있고 사업의 개요 Section이 없다.
성공 경로는 그 Section을 더한 복제본(profile_document)을 쓰고, 실패 경로는 원본을 그대로 쓴다.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date
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
    ExecutionStatus,
    SectionScope,
)

Q05_SHAPED_QUESTION = "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?"
Q04_SHAPED_QUESTION = "테스트반도체 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

SYNTHETIC_COMPANIES = ("테스트반도체",)
SYNTHETIC_ANCHORS = {"테스트반도체": 2025}

ORIGINAL_DOC_ID = "periodic_SYNTHETIC0001"
CORRECTION_DOC_ID = "periodic_SYNTHETIC0004"

OVERVIEW_SECTION_ID = "section_0005"
OVERVIEW_SECTION_PATH = ["II. 사업의 내용", "1. 사업의 개요"]
PRODUCTS_SECTION_PATH = ["II. 사업의 내용", "2. 주요 제품 및 서비스"]


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(
        Q05_SHAPED_QUESTION,
        known_companies=SYNTHETIC_COMPANIES,
        fiscal_year_anchors=SYNTHETIC_ANCHORS,
    )


@pytest.fixture
def parsed_document_stub(monkeypatch: pytest.MonkeyPatch):
    """parser 호출을 합성 문서로 대체한다. test_pattern_b와 같은 자리다."""

    def _install(document: dict[str, Any]) -> None:
        monkeypatch.setattr(
            pattern_b,
            "load_parsed_document",
            lambda document_path: document,
        )

    return _install


@pytest.fixture
def profile_document(summary_document: dict[str, Any]) -> dict[str, Any]:
    """합성 요약 문서에 '1. 사업의 개요' Section을 더한 복제본."""
    document = copy.deepcopy(summary_document)

    document["sections"].insert(
        1,
        {
            "section_id": OVERVIEW_SECTION_ID,
            "section_path": list(OVERVIEW_SECTION_PATH),
            "paragraphs": ["합성 사업 개요 첫 단락.", "합성 사업 개요 둘째 단락."],
        },
    )

    return document


def rewrite_manifest(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def manifest_without_correction(
    synthetic_manifest_path: Path,
    manifest_records: list[dict[str, Any]],
) -> Path:
    """FY2025 사업보고서가 원본 1건뿐인 manifest."""
    rewrite_manifest(
        synthetic_manifest_path,
        [record for record in manifest_records if not record.get("is_correction")],
    )

    return synthetic_manifest_path


# --------------------------------------------------------------------------
# 1. 정상 흐름
# --------------------------------------------------------------------------


def test_two_scopes_of_one_document_become_two_summaries(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None

    assert [summary.section_scope for summary in result.document_summaries] == [
        SectionScope.BUSINESS_OVERVIEW,
        SectionScope.PRODUCTS,
    ]
    assert {summary.doc_id for summary in result.document_summaries} == {ORIGINAL_DOC_ID}
    assert [summary.section_path for summary in result.document_summaries] == [
        OVERVIEW_SECTION_PATH,
        PRODUCTS_SECTION_PATH,
    ]

    # Pattern B는 값을 뽑지도 계산하지도 않는다.
    assert result.facts == []
    assert result.calculations == []
    assert result.comparisons == []
    assert result.events == []
    assert result.event_histories == []

    # 항목마다 Evidence가 있고, 두 범위의 Evidence가 한 결과에 함께 실린다.
    evidence_ids = [evidence.evidence_id for evidence in result.evidence]
    assert len(evidence_ids) == len(set(evidence_ids))

    items = [item for summary in result.document_summaries for item in summary.items]
    assert len(result.evidence) == len(items) == 3

    for item in items:
        assert set(item.evidence_ids) <= set(evidence_ids)

    assert {tuple(evidence.section_path) for evidence in result.evidence} == {
        tuple(OVERVIEW_SECTION_PATH),
        tuple(PRODUCTS_SECTION_PATH),
    }


def test_public_answer_names_both_scopes(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.facts == []
    assert len(response.evidence) == len(result.evidence)
    assert "1. 사업의 개요" in response.answer
    assert "2. 주요 제품 및 서비스" in response.answer


def test_router_resolves_the_anchor_from_the_manifest(
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    질문에 연도가 없어도 Router가 manifest에서 기준 연도를 유도해 넘긴다.

    합성 manifest의 테스트반도체 사업보고서는 FY2024·FY2025이므로 기준 연도는 2025다.
    """
    parsed_document_stub(profile_document)

    result = router.answer_question(
        Q05_SHAPED_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.document_summaries) == 2
    assert {summary.doc_id for summary in result.document_summaries} == {ORIGINAL_DOC_ID}
    assert any("FY2025" in warning for warning in result.warnings)


# --------------------------------------------------------------------------
# 2. 부분 답을 내지 않는다
# --------------------------------------------------------------------------


def test_one_missing_scope_fails_the_whole_answer(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    주요 제품 Section은 있지만 사업의 개요 Section이 없는 문서다.

    확정한 범위(주요 제품)만 실어 보내지 않는다. 그렇게 하면 질문이 물은 나머지 범위가
    사라진 사실이 결과에 남지 않는다. 사유는 그 범위를 단독으로 요약했을 때와 같다.
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.answerability_detail.stage == "section_navigation"
    assert result.answerability_detail.reason == "no_matching_section"
    assert result.document_summaries == []
    assert result.evidence == []


def test_two_scopes_resolving_to_one_section_fail_closed(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    한 제목이 두 규칙에 함께 걸리는 서식("사업의 개요 및 주요 제품")이다.

    두 범위가 같은 Section으로 확정되면 같은 근거가 두 번 실리고, 답은 서로 다른 두 범위를
    찾은 것처럼 말하게 된다. 그 Section이 어느 범위의 것인지 정할 근거가 없으므로 Section
    탐색의 ambiguous_section과 같은 판정으로 멈추고, 요약도 근거도 싣지 않는다.
    """
    document = copy.deepcopy(summary_document)

    for section in document["sections"]:
        if section["section_path"] == PRODUCTS_SECTION_PATH:
            section["section_path"] = ["II. 사업의 내용", "1. 사업의 개요 및 주요 제품"]

    parsed_document_stub(document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_detail.stage == "section_navigation"
    assert result.answerability_detail.reason == "ambiguous_section"
    assert "1. 사업의 개요 및 주요 제품" in result.answerability_detail.detail
    assert result.document_summaries == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 3. 공시 확정: 정정본을 후보에 포함한다
# --------------------------------------------------------------------------


def test_original_and_correction_are_not_narrowed_to_the_original(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    같은 회계연도에 원본과 정정본이 함께 있으면 어느 것도 고르지 않는다.

    합성 manifest에는 FY2025 사업보고서의 정정본이 들어 있다. 정정본을 조회에서 빼면 원본
    1건으로 보여 진행했을 것이고, 그것은 판본 정책(D5) 없이 원본을 고른 것이다.
    """
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_detail.reason == "ambiguous_filing"
    assert ORIGINAL_DOC_ID in result.answerability_detail.detail
    assert CORRECTION_DOC_ID in result.answerability_detail.detail
    assert result.document_summaries == []
    assert result.evidence == []


def test_zero_candidates_are_not_answered(
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """기준 연도의 사업보고서가 코퍼스에 없으면 다른 해로 대신하지 않는다."""
    parsed_document_stub(profile_document)

    plan = build_query_plan(
        Q05_SHAPED_QUESTION,
        known_companies=SYNTHETIC_COMPANIES,
        fiscal_year_anchors={"테스트반도체": 2023},
    )

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is not AnswerabilityStatus.ANSWERABLE
    assert result.answerability_detail.stage == "filing_discovery"
    assert result.answerability_detail.reason == "no_matching_filing"
    assert result.document_summaries == []
    assert result.evidence == []


def test_single_scope_path_still_excludes_corrections(
    summary_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    범위 하나의 경로(Q04)의 조회 규칙은 바꾸지 않았다.

    같은 manifest(정정본 포함)에서 Q04 모양은 여전히 원본 1건으로 답한다.
    """
    parsed_document_stub(summary_document)

    plan = build_query_plan(Q04_SHAPED_QUESTION, known_companies=SYNTHETIC_COMPANIES)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert [summary.doc_id for summary in result.document_summaries] == [ORIGINAL_DOC_ID]


@pytest.mark.parametrize(
    "section_scopes",
    [
        (SectionScope.FACILITY_INVESTMENT,),
        (SectionScope.FACILITY_INVESTMENT, SectionScope.PRODUCTS),
    ],
)
def test_non_canonical_bundles_do_not_inherit_the_q05_filing_policy(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
    section_scopes: tuple[SectionScope, ...],
) -> None:
    """
    Q05의 묶음이 아닌 범위 묶음은 입구에서 막힌다.

    정정본을 포함하는 조회 규칙은 Q05의 묶음에만 맞춰 둔 것이다. 이 manifest에는 FY2025
    정정본이 있으므로, 다른 묶음이 그 규칙을 물려받았다면 ambiguous_filing이 됐을 것이고,
    범위 하나로 바꿔 읽었다면 원본 1건으로 답했을 것이다. 어느 쪽도 아니어야 한다.
    """
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        replace(plan, section_scopes=section_scopes),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "pattern_entry"
    assert result.execution_issue.reason == "unsupported_summary_section_scope"
    assert result.document_summaries == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 5. source_documents — 실제로 선택된 FilingRef의 문서 metadata
#
# 범위 묶음 경로는 범위마다 결과를 만들어 이어 붙인다. 그 이어 붙이는 자리에서 문서
# metadata가 빠지면 Evidence.doc_id를 join할 상대가 없어지고, 화면은 문서 이름 대신 일반
# 문구로 물러난다(응답만 봐서는 정상과 구분되지 않는다).
# --------------------------------------------------------------------------

# 합성 manifest의 FY2025 사업보고서 레코드가 담고 있는 값(tests/conftest.py).
EXPECTED_CANONICAL_COMPANY = "테스트반도체"
EXPECTED_REPORT_NAME = "사업보고서 (2025.12)"
EXPECTED_FISCAL_YEAR = 2025
EXPECTED_DOCUMENT_SUBTYPE = "annual"
EXPECTED_BASE_MONTH = 12
EXPECTED_FILING_DATE = date(2026, 3, 12)
EXPECTED_RECEIPT_NO = "SYNTHETIC0001"


def _assert_selected_filing_metadata(document) -> None:
    """manifest 레코드가 담고 있는 값이 그대로 왔는지 본다.

    corp_name은 manifest 원 표기("테스트반도체(주)")가 아니라 질의 해석이 확정한 canonical
    표기여야 한다. DocumentSummary.company가 쓰는 값과 같아야 같은 응답 안에서 같은 회사가
    두 표기로 나뉘지 않는다.
    """
    assert document.company == EXPECTED_CANONICAL_COMPANY
    assert document.report_name == EXPECTED_REPORT_NAME
    assert document.fiscal_year == EXPECTED_FISCAL_YEAR
    assert document.document_subtype == EXPECTED_DOCUMENT_SUBTYPE
    assert document.base_month == EXPECTED_BASE_MONTH
    assert document.filing_date == EXPECTED_FILING_DATE
    assert document.receipt_no == EXPECTED_RECEIPT_NO


def test_two_scopes_carry_the_selected_filing_as_one_source_document(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    두 범위가 한 문서를 보므로 source_documents는 1건이고, 값은 manifest 레코드 그대로다.

    근거는 범위 수보다 많다(범위마다 항목이 여럿이다). 그 근거 전부가 같은 문서를 가리키므로
    doc_id 집합이 정확히 일치하고, 문서는 1건으로 접힌다 — 근거마다 한 건씩 쌓이면
    AgentExecutionResult의 doc_id 중복 검사가 거절한다.
    """
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE

    # 같은 문서를 가리키는 근거가 여럿인데 문서는 1건으로 접힌다.
    assert len(result.evidence) > 1
    assert len(result.source_documents) == 1

    evidence_doc_ids = {evidence.doc_id for evidence in result.evidence}
    source_doc_ids = {document.doc_id for document in result.source_documents}

    assert evidence_doc_ids == source_doc_ids == {ORIGINAL_DOC_ID}

    _assert_selected_filing_metadata(result.source_documents[0])


def test_public_answer_response_carries_the_source_document(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """공개 응답까지 같은 값이 그대로 간다 — 화면이 join하는 것은 이 값이다."""
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    response = build_answer_response(result)

    assert [document.doc_id for document in response.source_documents] == [ORIGINAL_DOC_ID]
    assert {evidence.doc_id for evidence in response.evidence} == {
        document.doc_id for document in response.source_documents
    }
    assert response.source_documents == result.source_documents


def test_single_scope_path_carries_the_selected_filing_as_a_source_document(
    summary_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    범위 하나의 경로(Q04)도 같은 값을 싣는다.

    이 경로는 원래부터 normalize_document_summary_result를 그대로 지나가므로 배선이 있었다.
    범위 묶음 경로를 고치면서 이쪽이 흔들리지 않았는지 함께 못박는다.
    """
    parsed_document_stub(summary_document)

    plan = build_query_plan(Q04_SHAPED_QUESTION, known_companies=SYNTHETIC_COMPANIES)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.source_documents) == 1

    evidence_doc_ids = {evidence.doc_id for evidence in result.evidence}
    source_doc_ids = {document.doc_id for document in result.source_documents}

    assert evidence_doc_ids == source_doc_ids == {ORIGINAL_DOC_ID}

    _assert_selected_filing_metadata(result.source_documents[0])


def test_a_missing_scope_leaves_no_source_document_behind(
    plan: QueryPlan,
    summary_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    범위 하나를 확정하지 못해 전부 멈춘 결과에는 문서 metadata도 남지 않는다.

    공시는 확정했고 문서도 열었으므로 FilingRef 자체는 있다. 그래도 싣지 않는다 — 근거가
    나가지 않는 응답에 문서 metadata만 실리면 join할 상대가 없는 문서가 화면에 남는다.
    """
    parsed_document_stub(summary_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.evidence == []
    assert result.source_documents == []


def test_unresolved_filing_leaves_no_source_document_behind(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """공시를 1건으로 좁히지 못하면 후보 어느 쪽도 문서 metadata로 싣지 않는다."""
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.evidence == []
    assert result.source_documents == []


@pytest.mark.parametrize(
    "section_scopes",
    [
        (SectionScope.FACILITY_INVESTMENT,),
        (SectionScope.FACILITY_INVESTMENT, SectionScope.PRODUCTS),
    ],
)
def test_unsupported_scope_bundles_leave_no_source_document_behind(
    plan: QueryPlan,
    profile_document: dict[str, Any],
    manifest_without_correction: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
    section_scopes: tuple[SectionScope, ...],
) -> None:
    """입구에서 막힌 계획은 공시를 찾기도 전에 멈추므로 실을 문서 metadata가 없다."""
    parsed_document_stub(profile_document)

    result = handle_document_summary(
        replace(plan, section_scopes=section_scopes),
        corpus_root=nfd_corpus_root,
        manifest_path=manifest_without_correction,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.evidence == []
    assert result.source_documents == []
