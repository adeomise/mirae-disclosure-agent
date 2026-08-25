"""Pattern A (Single Fact Lookup) 통합 테스트.

질문 문자열에서 공통 실행 Contract(AgentExecutionResult)까지의 흐름을 본다. 합성 manifest와
합성 corpus만 사용하며 실제 공시 XML, 실제 기업명, 실제 수치는 쓰지 않는다.

Parser 경계에 대한 메모:
    현재 periodic parser는 cell.acontext를 보존하지 않는다. 실제 corpus에서는 이 흐름이
    extraction 단계에서 막히는 것이 정상이며, 그 상태를 우회하지 않는다. 여기서는 parser가
    보완된 뒤의 입력을 합성 fixture로 대신해 downstream을 검증한다.

실행 상태와 답변 가능성을 나눠 보는 이유:
    "실행이 끝났는가"와 "답할 수 있는가"는 다른 질문이다. 공시를 찾지 못한 것은 실행이 끝난
    뒤의 판정이고, 문서를 열지 못한 것은 판정에 이르지 못한 것이다. 두 경우가 같은 상태로
    보이면 검색 실패와 파이프라인 고장을 구분할 수 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import STATUS_BLOCKED, STATUS_OK
from disclosure_agent.agent.handlers import single_fact
from disclosure_agent.agent.handlers.single_fact import (
    build_single_fact_result,
    handle_single_fact,
)
from disclosure_agent.agent.query_analyzer import (
    SCOPE_CONSOLIDATED,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
    FactAccountingScope,
)

SYNTHETIC_QUESTION = "테스트반도체 2025년 연결 기준 매출액이 얼마야?"

SYNTHETIC_COMPANIES = ("테스트반도체",)


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(SYNTHETIC_QUESTION, known_companies=SYNTHETIC_COMPANIES)


@pytest.fixture
def parsed_document_stub(monkeypatch: pytest.MonkeyPatch):
    """
    parser 호출을 합성 문서로 대체한다.

    합성 corpus의 XML은 파싱 경계를 시험하기 위한 최소 파일이므로, 정상 흐름 검증에는
    parser가 보완된 뒤의 출력 형태를 전제한 fixture를 쓴다.
    """

    def _install(document: dict[str, Any]) -> None:
        monkeypatch.setattr(
            single_fact,
            "load_parsed_document",
            lambda document_path: document,
        )

    return _install


# --------------------------------------------------------------------------
# 1. handler 직접 호출
# --------------------------------------------------------------------------


def test_handle_single_fact_returns_ok_with_evidence(
    plan: QueryPlan,
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(synthetic_document)

    result = handle_single_fact(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None

    assert len(result.facts) == 1
    assert len(result.evidence) == 1

    fact = result.facts[0]

    assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert fact.fiscal_year == 2025
    assert result.evidence[0].doc_id == "periodic_SYNTHETIC0001"

    # Fact와 Evidence가 서로를 가리켜야 근거 추적이 끊기지 않는다.
    assert fact.evidence_ids == [result.evidence[0].evidence_id]


def test_single_fact_result_keeps_the_internal_view(
    plan: QueryPlan,
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    공통 Contract로 옮기기 전의 내부 결과는 그대로 남아 있다.

    공시 메타데이터처럼 Contract에 자리가 없는 값을 대조할 경로가 필요하다.
    """
    parsed_document_stub(synthetic_document)

    internal = build_single_fact_result(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    ).to_dict()

    assert internal["status"] == STATUS_OK
    assert internal["fact"]["accounting_scope"] == SCOPE_CONSOLIDATED
    assert internal["fact"]["fiscal_year"] == 2025
    assert internal["filing"]["doc_id"] == "periodic_SYNTHETIC0001"
    assert len(internal["evidence"]) == 1


def test_handle_single_fact_propagates_extraction_blocked(
    plan: QueryPlan,
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    추출 단계에서 막히면 값을 지어내지 않고 그대로 올라온다.

    missing_acontext는 값이 없는 것이 아니라 parser가 판정 근거를 주지 않은 것이다.
    UNANSWERABLE로 보내면 '코퍼스에 답이 없다'가 되어 사실과 달라진다.
    """
    for table in synthetic_document["tables"]:
        for row in table["rows"]:
            for cell in row:
                cell.pop("acontext", None)

    parsed_document_stub(synthetic_document)

    result = handle_single_fact(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.execution_issue.stage == "extraction"
    assert result.execution_issue.reason == "missing_acontext"
    assert result.facts == []
    assert result.evidence == []


def test_handle_single_fact_reports_parse_failure_without_swallowing(
    plan: QueryPlan,
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    parser가 문서를 해석하지 못하면 parsing 단계에서 막힌다.

    합성 corpus의 XML에는 BODY가 없다. 원문 XML을 다시 열어 우회하지 않는지 함께 본다.
    """
    result = handle_single_fact(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.execution_issue.stage == "parsing"
    assert result.execution_issue.reason == "parse_failed"

    internal = build_single_fact_result(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    ).to_dict()

    assert internal["status"] == STATUS_BLOCKED
    assert internal["filing"]["doc_id"] == "periodic_SYNTHETIC0001"


def test_handle_single_fact_blocks_when_no_filing_matches(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    manifest에 없는 연도는 다른 연도로 대체하지 않는다.

    manifest 전체와 대조한 결과이므로 실행은 끝난 것이다. 답할 수 없다는 판정만 남는다.
    """
    plan = build_query_plan(
        "테스트반도체 2019년 연결 기준 매출액이 얼마야?",
        known_companies=SYNTHETIC_COMPANIES,
    )

    result = handle_single_fact(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.execution_issue is None
    assert result.facts == []

    # canonical reason은 서로 다른 정지 사유를 한 값으로 묶는다.
    # 어느 단계에서 무엇을 보고 판정했는지는 answerability_detail에 남는다.
    assert result.answerability_detail.stage == "filing_discovery"
    assert result.answerability_detail.reason == "no_matching_filing"
    assert result.answerability_detail.detail

    # 판정 근거는 손실이 아니므로 conversion_gaps에 남기지 않는다.
    assert result.conversion_gaps == []


# --------------------------------------------------------------------------
# 2. Router 진입점
# --------------------------------------------------------------------------


def test_answer_question_routes_through_pattern_a(
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """질문 문자열 하나로 Router를 통과해 결과까지 나온다."""
    parsed_document_stub(synthetic_document)

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.facts[0].metric.value == "revenue"

    # 표 위치는 Contract Evidence의 field_name에 남는다.
    assert "table_id=table_0002" in result.evidence[0].field_name


def test_answer_question_reads_company_universe_from_manifest(
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """
    기업명 목록을 호출자가 넘기지 않아도 manifest에서 읽는다.

    manifest에는 법인격 표기가 붙은 이름이 들어 있고 질문에는 없다.
    """
    parsed_document_stub(synthetic_document)

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.facts[0].company == "테스트반도체(주)"


def test_answer_question_blocks_unknown_company_before_reading_documents(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """manifest에 없는 기업은 추측하지 않는다."""
    result = router.answer_question(
        "존재하지않는회사 2025년 연결 기준 매출액이 얼마야?",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "query_plan"
    assert result.execution_issue.reason == "unknown_company"
    assert result.facts == []
    assert result.evidence == []


def test_answer_question_blocks_when_manifest_is_missing(
    tmp_path: Path,
    nfd_corpus_root: Path,
) -> None:
    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=tmp_path / "does_not_exist.jsonl",
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "filing_discovery"
    assert result.execution_issue.reason == "manifest_missing"


def test_answer_question_rejects_out_of_scope_period(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """분기 표현은 이번 범위 밖이며 연간으로 바꿔 답하지 않는다."""
    result = router.answer_question(
        "테스트반도체 2025년 3분기 연결 기준 매출액이 얼마야?",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "query_plan"
    assert result.execution_issue.reason == "unsupported_period"


def test_answer_question_result_is_json_serializable(
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    parsed_document_stub(synthetic_document)

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    dumped = result.model_dump(mode="json")

    assert json.loads(json.dumps(dumped, ensure_ascii=False)) == dumped
    assert dumped["execution_status"] == "COMPLETED"
    assert dumped["answerability_status"] == "ANSWERABLE"
