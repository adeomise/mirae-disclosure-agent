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
      -> SingleFactResult
      -> AgentExecutionResult (agent.normalization)

이 모듈이 agent 계층에 있는 이유:
- 위 흐름은 질문 해석 결과(QueryPlan)를 받아 문서군 모듈과 검색 모듈을 조합한다. 조합하는
  쪽이 조합되는 쪽에 들어가면 방향이 뒤집힌다. periodic.service는 "문서에서 무엇을 읽을 수
  있는가"만 답하고, 어느 문서를 볼지와 결과를 어떻게 조립할지는 여기서 정한다.

내부 결과와 공통 Contract를 나누는 이유:
- SingleFactResult는 이 Pattern이 확보한 것을 손실 없이 담는다. FinancialFact의 acode와
  ACONTEXT, 공시 메타데이터처럼 공통 Contract에 자리가 없는 값도 여기에는 남는다.
- 공통 Contract로 옮기는 일은 agent.normalization이 맡는다. 추출 흐름과 변환 규칙을 한
  함수에 섞으면, Contract가 개정될 때 검증된 추출 경로까지 함께 흔들린다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않고 실행 상태로 표현한다.

TODO:
- Q02 계열의 다중 Fact 응답(금액·시작일·종료일) 확장
- 후보 문서가 복수일 때의 Document Disambiguation(C3) 연결
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_PARSE_FAILED,
    STAGE_EVIDENCE,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    BlockedInfo,
    build_blocked_result,
    build_structured_result,
)
from disclosure_agent.agent.evidence import Evidence, MissingEvidenceError, build_evidence
from disclosure_agent.agent.normalization import normalize_single_fact_result
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.periodic.extractor import ExtractionBlockedError, FinancialFact
from disclosure_agent.periodic.service import extract_fact, load_parsed_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    locate_filing,
)


@dataclass(frozen=True)
class SingleFactResult:
    """
    Pattern A의 구조화 결과.

    공통 Contract가 아니다. 여기까지는 이 Pattern이 확보한 것을 그대로 들고, 공통 타입으로
    옮기는 일은 agent.normalization이 한다.

    document를 들고 있는 이유:
        FinancialFact -> Contract Fact/Evidence 변환(periodic.adapter)이 문서 metadata의
        doc_id와 corp_name을 요구한다. 그 값을 다시 읽으려고 문서를 두 번 파싱하지 않는다.

    blocked가 있으면 fact/evidence는 없다. 값을 추측해 채우지 않기 때문이다.
    """

    plan: QueryPlan
    filing: FilingRef | None = None
    document: dict[str, Any] | None = None
    fact: FinancialFact | None = None
    # agent.evidence.Evidence. 공통 Contract의 Evidence와 다른 내부 표현이며 acode·ACONTEXT·
    # 단위 원문처럼 Contract에 자리가 없는 provenance를 담고 있다.
    evidence: Evidence | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        기존 결과 dict 표현.

        공통 Contract로 옮기기 전의 진단용 표현이며, 공시 메타데이터와 내부 Evidence를
        전부 담는다. 정규화 결과에서 무엇이 빠졌는지 대조할 때 쓴다.
        """
        if self.blocked is not None:
            return build_blocked_result(
                self.plan,
                stage=self.blocked.stage,
                reason=self.blocked.reason,
                detail=self.blocked.detail,
                filing=self.filing,
                warnings=list(self.warnings),
            )

        if self.filing is None or self.fact is None or self.evidence is None:
            raise ValueError(
                "blocked가 없는 SingleFactResult에는 filing/fact/evidence가 모두 있어야 합니다."
            )

        return build_structured_result(
            self.plan,
            filing=self.filing,
            fact=self.fact,
            evidence=self.evidence,
            warnings=list(self.warnings),
        )


def handle_single_fact(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_single_fact_result를 직접 쓴다.
    """
    return normalize_single_fact_result(
        build_single_fact_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )


def build_single_fact_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> SingleFactResult:
    """
    단일 수치 조회 계획 하나를 구조화 결과까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다.
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
        return SingleFactResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    try:
        document = load_parsed_document(filing.main_document)

    except (FileNotFoundError, ValueError) as error:
        return SingleFactResult(
            plan=plan,
            filing=filing,
            blocked=BlockedInfo(
                stage=STAGE_PARSING,
                reason=REASON_PARSE_FAILED,
                detail=str(error),
            ),
        )

    return build_result_from_document(document, plan=plan, filing=filing)


def build_result_from_document(
    document: dict[str, Any],
    *,
    plan: QueryPlan,
    filing: FilingRef,
) -> SingleFactResult:
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
        return SingleFactResult(
            plan=plan,
            filing=filing,
            document=document,
            blocked=BlockedInfo(
                stage=STAGE_EXTRACTION,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    try:
        evidence = build_evidence(fact, filing=filing, document=document)

    except MissingEvidenceError as error:
        return SingleFactResult(
            plan=plan,
            filing=filing,
            document=document,
            blocked=BlockedInfo(
                stage=STAGE_EVIDENCE,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return SingleFactResult(
        plan=plan,
        filing=filing,
        document=document,
        fact=fact,
        evidence=evidence,
    )
