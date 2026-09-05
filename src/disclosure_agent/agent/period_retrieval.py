"""기간별 원천 Fact 확보 모듈.

역할:
- 회계연도 하나와 지표 하나를 받아 공시 선택부터 Evidence까지 한 번에 확보한다.
- 여러 기간·여러 지표를 요청받으면 전부 확보하거나 하나도 쓰지 않는다.

왜 별도 모듈인가:
    Pattern A는 이 흐름을 한 번 쓰고, Pattern C는 요청받은 기간 수만큼 쓴다. 같은 흐름을
    Pattern마다 다시 조립하면 두 벌이 되고, 그때부터 공시 선택 정책과 정지 사유가 Pattern별로
    갈라진다. 갈라진 뒤에는 어느 쪽이 정본인지 알 수 없다.

    handlers/single_fact.py 안에 두지 않은 이유도 같다. 이 흐름은 Pattern A의 것이 아니라
    두 Pattern이 공유하는 것이며, 한쪽 handler 안에 두면 다른 쪽이 그 handler에 의존하게 된다.

QueryPlan을 받지 않는다:
    입력은 primitive다. 질문 해석 결과를 검색 조건으로 옮기는 일은 handler가 하고, 이 모듈은
    "무엇을 찾을지"만 받는다. handlers/single_fact.py가 이미 같은 경계를 선언해 두었고
    ("retrieval은 질문 해석 결과를 모르고 primitive만 받는다"), 그 경계를 함수로 실체화한다.

    그래서 다중 기간 계획을 다룰 때 QueryPlan.year를 읽을 일이 없다. 그 통로는 단일 기간
    전용이며 시계열 비교 계획에서는 막혀 있다.

Pattern C에 대하여:
    이 모듈은 원천 Fact를 확보하는 데까지만 관여한다. 무엇을 계산할지, 두 기간 값을 비교해도
    되는지, 확보한 결과로 답할 수 있는지는 정하지 않는다. 그 판정은 Pattern C handler와
    calculator의 몫이고 아직 구현되지 않았다.

입력:
- 기업명, 회계연도, 지표, 회계기준, 문서군, 문서 세부유형, corpus 루트, manifest 경로

출력:
- PeriodFact: 한 조합의 확보 결과. 실패해도 예외를 올리지 않고 blocked로 표현한다.
- PeriodFactCollection: 요청 전체의 확보 결과. 하나라도 실패하면 Fact를 담지 않는다.

TODO:
- 파생지표(영업이익률 등)를 원천 지표로 펼치는 대응표 연결
- Pattern C handler 등록
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_PARSE_FAILED,
    REASON_UNCONVERTIBLE_FACT,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_EVIDENCE,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    BlockedInfo,
)
from disclosure_agent.agent.evidence import (
    Evidence as InternalEvidence,
)
from disclosure_agent.agent.evidence import (
    MissingEvidenceError,
    build_evidence,
)
from disclosure_agent.common.schemas import Evidence, Fact
from disclosure_agent.periodic.adapter import adapt_financial_fact
from disclosure_agent.periodic.extractor import ExtractionBlockedError, FinancialFact
from disclosure_agent.periodic.service import extract_fact, load_parsed_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    locate_filing,
)

__all__ = [
    "PeriodFact",
    "PeriodFactCollection",
    "collect_period_facts",
    "extract_period_fact_from_document",
    "fetch_period_fact",
]


@dataclass(frozen=True)
class PeriodFact:
    """
    회계연도 하나와 지표 하나에 대한 확보 결과.

    공통 Contract가 아니다. Pattern이 확보한 것을 손실 없이 들고 있으며, 공통 타입으로 옮기는
    일은 호출자가 정한다.

    document를 들고 있는 이유:
        FinancialFact -> Contract Fact/Evidence 변환이 문서 metadata의 doc_id와 corp_name을
        요구한다. 그 값을 다시 읽으려고 문서를 두 번 파싱하지 않는다.

    blocked가 있으면 fact/evidence는 없다. 값을 추측해 채우지 않기 때문이다.
    """

    filing: FilingRef | None = None
    document: dict[str, Any] | None = None
    fact: FinancialFact | None = None
    # agent.evidence.Evidence. 공통 Contract의 Evidence와 다른 내부 표현이며 acode·ACONTEXT·
    # 단위 원문처럼 Contract에 자리가 없는 provenance를 담고 있다.
    evidence: InternalEvidence | None = None
    blocked: BlockedInfo | None = None

    @property
    def is_found(self) -> bool:
        """값과 근거를 모두 확보했는가."""
        return self.blocked is None and self.fact is not None and self.evidence is not None


@dataclass(frozen=True)
class PeriodFactCollection:
    """
    요청한 (회계연도 x 지표) 전부에 대한 확보 결과.

    전부 확보하거나 하나도 쓰지 않는다. 일부만 담은 결과를 돌려주지 않는 이유:
        "2024년 매출과 2025년 매출 비교해줘"에서 2025년을 확보하지 못했는데 2024년 Fact가
        남아 있으면, 받는 쪽이 그것으로 답을 조립할 수 있다. 그러면 두 기간을 물은 질문에 한
        기간만 답하는 결과가 되고, 그것이 F6-02에서 막은 동작이다. 담을 자리를 아예 비워 두면
        그 실수를 할 수 없다.

    blocked에는 처음 막힌 지점 하나가 들어간다. 어느 조합에서 막혔는지는 detail에 남는다.
    """

    facts: tuple[Fact, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    blocked: BlockedInfo | None = None
    # 확보에 성공한 조합의 내부 표현. Contract에 자리가 없는 provenance가 필요한 호출자를 위해
    # 남긴다. blocked가 있으면 비어 있다.
    period_facts: tuple[PeriodFact, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        """요청한 조합을 전부 확보했는가."""
        return self.blocked is None


def extract_period_fact_from_document(
    document: dict[str, Any],
    *,
    metric: str,
    fiscal_year: int,
    accounting_scope: str,
    filing: FilingRef,
) -> PeriodFact:
    """
    이미 파싱된 문서에서 Fact와 Evidence를 조립한다.

    parser 호출과 분리해 두어 synthetic parsed document로도 검증할 수 있다.
    """
    try:
        fact = extract_fact(
            document,
            metric=metric,
            fiscal_year=fiscal_year,
            accounting_scope=accounting_scope,
        )

    except ExtractionBlockedError as error:
        return PeriodFact(
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
        return PeriodFact(
            filing=filing,
            document=document,
            blocked=BlockedInfo(
                stage=STAGE_EVIDENCE,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return PeriodFact(
        filing=filing,
        document=document,
        fact=fact,
        evidence=evidence,
    )


def fetch_period_fact(
    *,
    company: str,
    fiscal_year: int,
    metric: str,
    accounting_scope: str,
    document_class: str,
    document_subtype: str | None,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> PeriodFact:
    """
    회계연도 하나와 지표 하나를 공시 선택부터 Evidence까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다. 어느 단계에서 왜 막혔는지
    호출자가 그대로 볼 수 있게 하기 위해서다.

    공시 선택 정책은 여기서 새로 정하지 않는다. 요청한 회계연도로 locate_filing을 부르며,
    최신 공시 선택과 정정본 제외는 retrieval 계층의 기존 기본값을 그대로 쓴다. 최신 공시의
    비교 열에서 과거 연도를 대신 읽는 경로는 두지 않는다. 그 선택은 미결정 사항(D4)이고,
    아무것도 하지 않으면 "각 연도의 원 보고서를 각각 본다"가 된다.
    """
    corpus_root = Path(corpus_root)

    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"

    try:
        filing = locate_filing(
            manifest_path=manifest_path,
            corpus_root=corpus_root,
            company=company,
            document_class=document_class,
            document_subtype=document_subtype,
            year=fiscal_year,
        )

    except FilingNotFoundError as error:
        return PeriodFact(
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    try:
        document = load_parsed_document(filing.main_document)

    except (FileNotFoundError, ValueError) as error:
        return PeriodFact(
            filing=filing,
            blocked=BlockedInfo(
                stage=STAGE_PARSING,
                reason=REASON_PARSE_FAILED,
                detail=str(error),
            ),
        )

    return extract_period_fact_from_document(
        document,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        filing=filing,
    )


def _blocked_for(
    found: PeriodFact,
    *,
    fiscal_year: int,
    metric: str,
) -> BlockedInfo:
    """
    조합 하나가 막힌 지점에 어느 연도·지표였는지를 덧붙인다.

    stage와 reason은 바꾸지 않는다. 그 값이 정지 사유의 정본이고, agent.normalization의
    대응표가 그것으로 답변 가능성을 판정한다. 어느 조합이었는지는 detail에만 남긴다.
    """
    blocked = found.blocked

    if blocked is None:  # pragma: no cover - is_found 검사 뒤에만 부른다
        raise ValueError("막히지 않은 결과에서 blocked를 만들 수 없습니다.")

    return BlockedInfo(
        stage=blocked.stage,
        reason=blocked.reason,
        detail=f"{fiscal_year} 회계연도 / {metric}: {blocked.detail}",
    )


def collect_period_facts(
    *,
    company: str,
    fiscal_years: Sequence[int],
    metrics: Sequence[str],
    accounting_scope: str,
    document_class: str,
    document_subtype: str | None,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> PeriodFactCollection:
    """
    요청한 회계연도 전부에서 요청한 지표 전부를 확보한다.

    확보 순서는 (연도, 지표)이며 요청 순서를 지킨다. 정렬하지 않는다. 받는 쪽이 요청과 결과를
    나란히 놓고 대조할 수 있어야 한다.

    하나라도 막히면 즉시 멈추고 Fact를 담지 않는다. 나머지를 마저 조회하지 않는 이유는, 그
    결과를 쓸 곳이 없기 때문이다. 어차피 부분 결과로는 답하지 않으므로 문서를 더 여는 것은
    비용만 늘린다.

    fiscal_years와 metrics는 호출자가 정한다. 이 함수는 "무엇을 계산해야 하는가"를 보지 않고
    받은 목록을 그대로 확보한다. 파생지표를 원천 지표로 펼치는 일은 아직 구현되지 않았으며,
    구현되더라도 이 함수의 바깥이다.
    """
    if not fiscal_years:
        raise ValueError("fiscal_years는 최소 한 개의 회계연도를 담아야 합니다.")

    if not metrics:
        raise ValueError("metrics는 최소 한 개의 지표를 담아야 합니다.")

    collected: list[PeriodFact] = []

    for fiscal_year in fiscal_years:
        for metric in metrics:
            found = fetch_period_fact(
                company=company,
                fiscal_year=fiscal_year,
                metric=metric,
                accounting_scope=accounting_scope,
                document_class=document_class,
                document_subtype=document_subtype,
                corpus_root=corpus_root,
                manifest_path=manifest_path,
            )

            if not found.is_found:
                return PeriodFactCollection(
                    blocked=_blocked_for(found, fiscal_year=fiscal_year, metric=metric)
                )

            collected.append(found)

    facts: list[Fact] = []
    evidence: list[Evidence] = []

    for found in collected:
        try:
            fact, item = adapt_financial_fact(
                found.fact,
                found.document or {},
                canonical_company=company,
            )

        except ValueError as error:
            # 값은 확정했지만 공통 타입으로 옮기지 못했다. 추측해 채우지 않고 전부 버린다.
            return PeriodFactCollection(
                blocked=BlockedInfo(
                    stage=STAGE_CONTRACT_ADAPTATION,
                    reason=REASON_UNCONVERTIBLE_FACT,
                    detail=str(error),
                )
            )

        facts.append(fact)
        evidence.append(item)

    return PeriodFactCollection(
        facts=tuple(facts),
        evidence=tuple(evidence),
        period_facts=tuple(collected),
    )
