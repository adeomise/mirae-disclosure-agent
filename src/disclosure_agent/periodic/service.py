"""정기공시 처리 흐름 조정 모듈.

역할:
- Parser, Extractor, Chunker의 호출 순서와 결과 전달 경계를 관리한다.

현재 구현 범위:
- 대표 질문 Q01 계열의 단일 수치 조회 파이프라인을 연결한다.

    질문
      -> QueryPlan            (agent.query_analyzer)
      -> 공시 선택/경로 해석   (retrieval.metadata_filter)
      -> ParsedDocument       (periodic.parser)
      -> FinancialFact        (periodic.extractor)
      -> Evidence             (agent.evidence)
      -> 구조화 결과           (agent.answer_builder)

Parser 경계에 대한 메모:
- 이 모듈은 periodic.parser를 호출하기만 하고 수정하지 않는다.
- extractor가 필요로 하는 cell.acontext, 표 단위 귀속, 본표/주석 구분은 현재 parser가
  제공하지 않는다. 그 경우 예외를 삼키거나 원문 XML을 다시 읽어 우회하지 않고 BLOCKED
  결과로 보고한다.

입력:
- 질문 문자열, corpus 루트, manifest 경로

출력:
- 구조화된 결과 dict

TODO:
- Chunker 연결
- 다중 Fact/계산형 질의 흐름 확장
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import build_blocked_result, build_structured_result
from disclosure_agent.agent.evidence import MissingEvidenceError, build_evidence
from disclosure_agent.agent.query_analyzer import (
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.periodic.extractor import ExtractionBlockedError, extract_financial_fact
from disclosure_agent.periodic.parser import parse_periodic_xml
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    list_company_names,
    load_manifest,
    locate_filing,
)

STAGE_QUERY_PLAN = "query_plan"
STAGE_FILING_DISCOVERY = "filing_discovery"
STAGE_PARSING = "parsing"
STAGE_EXTRACTION = "extraction"
STAGE_EVIDENCE = "evidence"


def answer_single_fact_question(
    question: str,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    단일 수치 조회 질문을 처음부터 끝까지 처리한다.

    실패 시 예외를 올리지 않고 status="blocked" 결과를 돌려준다.
    어느 단계에서 막혔는지 호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    corpus_root = Path(corpus_root)

    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"

    plan: QueryPlan | None = None

    filing: FilingRef | None = None

    try:
        known_companies = list_company_names(load_manifest(manifest_path))

    except FilingNotFoundError as error:
        return build_blocked_result(
            None,
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    try:
        plan = build_query_plan(question, known_companies=known_companies)

    except UnsupportedQuestionError as error:
        return build_blocked_result(
            None,
            stage=STAGE_QUERY_PLAN,
            reason=error.reason,
            detail=error.detail,
        )

    try:
        filing = locate_filing(
            plan,
            manifest_path=manifest_path,
            corpus_root=corpus_root,
        )

    except FilingNotFoundError as error:
        return build_blocked_result(
            plan,
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    try:
        document = parse_periodic_xml(filing.main_document)

    except (FileNotFoundError, ValueError) as error:
        return build_blocked_result(
            plan,
            stage=STAGE_PARSING,
            reason="parse_failed",
            detail=str(error),
            filing=filing,
        )

    return build_result_from_document(document, plan=plan, filing=filing)


def build_result_from_document(
    document: dict[str, Any],
    *,
    plan: QueryPlan,
    filing: FilingRef,
) -> dict[str, Any]:
    """
    이미 파싱된 문서에서 Fact/Evidence/결과를 조립한다.

    parser 호출과 분리해 두어 synthetic parsed document로도 검증할 수 있다.
    """
    try:
        fact = extract_financial_fact(
            document,
            metric=plan.metric,
            fiscal_year=plan.year,
            accounting_scope=plan.accounting_scope,
        )

    except ExtractionBlockedError as error:
        return build_blocked_result(
            plan,
            stage=STAGE_EXTRACTION,
            reason=error.reason,
            detail=error.detail,
            filing=filing,
        )

    try:
        evidence = build_evidence(fact, filing=filing, document=document)

    except MissingEvidenceError as error:
        return build_blocked_result(
            plan,
            stage=STAGE_EVIDENCE,
            reason=error.reason,
            detail=error.detail,
            filing=filing,
        )

    return build_structured_result(
        plan,
        filing=filing,
        fact=fact,
        evidence=evidence,
    )
