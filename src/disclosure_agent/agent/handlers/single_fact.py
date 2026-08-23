"""Pattern A — Single Fact Lookup handler.

역할:
- 문서 1건에서 값 1개를 찾아 근거와 함께 돌려주는 실행 흐름을 조정한다.

현재 구현 범위:
- 대표 질문 Q01 계열의 단일 수치 조회.

    QueryPlan
      -> 공시 선택/경로 해석   (retrieval.metadata_filter)
      -> ParsedDocument       (periodic.service)
      -> FinancialFact        (periodic.service)
      -> Evidence             (agent.evidence)
      -> 구조화 결과           (agent.answer_builder)

이 모듈이 agent 계층에 있는 이유:
- 위 흐름은 질문 해석 결과(QueryPlan)를 받아 문서군 모듈과 검색 모듈을 조합한다. 조합하는
  쪽이 조합되는 쪽에 들어가면 방향이 뒤집힌다. periodic.service는 "문서에서 무엇을 읽을 수
  있는가"만 답하고, 어느 문서를 볼지와 결과를 어떻게 조립할지는 여기서 정한다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- 직렬화 가능한 결과 dict. 실패해도 예외를 올리지 않고 status="blocked"로 표현한다.

TODO:
- Q02 계열의 다중 Fact 응답(금액·시작일·종료일) 확장
- 후보 문서가 복수일 때의 Document Disambiguation(C3) 연결
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    STAGE_EVIDENCE,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    build_blocked_result,
    build_structured_result,
)
from disclosure_agent.agent.evidence import MissingEvidenceError, build_evidence
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.periodic.extractor import ExtractionBlockedError
from disclosure_agent.periodic.service import extract_fact, load_parsed_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    locate_filing,
)


def handle_single_fact(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    단일 수치 조회 계획 하나를 끝까지 처리한다.

    실패 시 예외를 올리지 않고 status="blocked" 결과를 돌려준다.
    어느 단계에서 왜 막혔는지 호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    corpus_root = Path(corpus_root)

    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"

    try:
        # QueryPlan을 검색 조건으로 옮기는 일은 여기서 한다.
        # retrieval은 질문 해석 결과를 모르고 primitive만 받는다.
        filing = locate_filing(
            manifest_path=manifest_path,
            corpus_root=corpus_root,
            company=plan.company,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            year=plan.year,
        )

    except FilingNotFoundError as error:
        return build_blocked_result(
            plan,
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    try:
        document = load_parsed_document(filing.main_document)

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
        fact = extract_fact(
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
