"""Pattern A (Single Fact Lookup) 통합 테스트.

질문 문자열에서 결과 dict까지의 흐름을 본다. 합성 manifest와 합성 corpus만 사용하며
실제 공시 XML, 실제 기업명, 실제 수치는 쓰지 않는다.

Parser 경계에 대한 메모:
    현재 periodic parser는 cell.acontext를 보존하지 않는다. 실제 corpus에서는 이 흐름이
    extraction 단계에서 막히는 것이 정상이며, 그 상태를 우회하지 않는다. 여기서는 parser가
    보완된 뒤의 입력을 합성 fixture로 대신해 downstream을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import STATUS_BLOCKED, STATUS_OK
from disclosure_agent.agent.handlers import single_fact
from disclosure_agent.agent.handlers.single_fact import handle_single_fact
from disclosure_agent.agent.query_analyzer import (
    INTENT_SINGLE_FACT,
    SCOPE_CONSOLIDATED,
    QueryPlan,
    build_query_plan,
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

    assert result["status"] == STATUS_OK
    assert result["fact"]["accounting_scope"] == SCOPE_CONSOLIDATED
    assert result["fact"]["fiscal_year"] == 2025
    assert result["filing"]["doc_id"] == "periodic_SYNTHETIC0001"
    assert len(result["evidence"]) == 1


def test_handle_single_fact_propagates_extraction_blocked(
    plan: QueryPlan,
    synthetic_document: dict[str, Any],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    parsed_document_stub,
) -> None:
    """추출 단계에서 막히면 값을 지어내지 않고 그대로 올라온다."""
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

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "extraction"
    assert result["blocked"]["reason"] == "missing_acontext"
    assert result["fact"] is None
    assert result["evidence"] == []


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

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "parsing"
    assert result["blocked"]["reason"] == "parse_failed"
    assert result["filing"]["doc_id"] == "periodic_SYNTHETIC0001"


def test_handle_single_fact_blocks_when_no_filing_matches(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """manifest에 없는 연도는 다른 연도로 대체하지 않는다."""
    plan = build_query_plan(
        "테스트반도체 2019년 연결 기준 매출액이 얼마야?",
        known_companies=SYNTHETIC_COMPANIES,
    )

    result = handle_single_fact(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "filing_discovery"
    assert result["blocked"]["reason"] == "no_matching_filing"
    assert result["fact"] is None


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

    assert result["status"] == STATUS_OK
    assert result["query"]["intent"] == INTENT_SINGLE_FACT
    assert result["evidence"][0]["table_id"] == "table_0002"


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

    assert result["status"] == STATUS_OK
    assert result["query"]["company"] == "테스트반도체(주)"


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

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "query_plan"
    assert result["blocked"]["reason"] == "unknown_company"
    assert result["query"] is None
    assert result["filing"] is None


def test_answer_question_blocks_when_manifest_is_missing(
    tmp_path: Path,
    nfd_corpus_root: Path,
) -> None:
    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=tmp_path / "does_not_exist.jsonl",
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "filing_discovery"
    assert result["blocked"]["reason"] == "manifest_missing"


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

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "query_plan"
    assert result["blocked"]["reason"] == "unsupported_period"


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

    assert json.loads(json.dumps(result, ensure_ascii=False))["status"] == STATUS_OK
