"""Pattern B — Section Summary handler.

역할:
- 문서 1건을 확정하고, 질문이 지정한 범위 안의 본문·표를 근거와 함께 모은다.

현재 구현 범위:
- 대표 질문 Q04 계열("<기업> <연도>년 사업보고서에서 <범위> 관련 내용 정리해줘").

    QueryPlan
      -> 계획 모양 확인          (이 모듈)
      -> 공시 후보 조회/1건 확정   (retrieval.metadata_filter)
      -> ParsedDocument           (periodic.service)
      -> Section 확정 + 내용 수집  (periodic.service -> periodic.sections)
      -> DocumentSummaryResult
      -> AgentExecutionResult     (agent.normalization)

Fact를 만들지 않는 이유:
    Pattern B의 최소 Fact 요건은 0이고 그 값은 확정 사항이다
    (docs/agent_design/evidence_and_answerability.md 유형별 최소 요건). 요약 항목마다 Evidence를
    갖는 것이 이 Pattern의 요건이며, 값을 함께 뽑으면 요약 결과 안에 Pattern A가 회계기준·기간을
    확인해 고르는 값과 다른 수치가 섞인다.

계산하지 않는 이유:
    원문에 적힌 비율·구성비는 인용하고 재계산하지 않는다는 것이 Pattern B의 출력 요구사항이다
    (docs/agent_design/agent_patterns.md Pattern B). 그래서 calculations도 comparisons도 만들지
    않는다.

공시를 1건으로 확정하는 방법:
    locate_filing을 쓰지 않는다. 그 함수는 후보가 여럿이면 접수번호가 가장 큰 것을 고른다.
    요약 대상이 어느 판본이었는지는 결과만 봐서는 드러나지 않으므로, 고르는 대신 후보가 정확히
    1건일 때만 진행한다. 여럿이면 ambiguous_filing으로 막는다.

    정정본은 조회 대상에서 뺀다. filter_filings의 기본값이며 Pattern A가 이미 쓰고 있는 규칙이다.
    판본 선택 정책(open_decisions.md D5)은 미정이고, 이 handler가 그 정책을 새로 정하지 않는다.
    같은 규칙을 그대로 쓰면 두 Pattern이 같은 문서를 본다.

Vector Search를 쓰지 않는 이유:
    구조 기반 탐색이 1순위이고 의미 검색은 조건부 fallback 후보다
    (docs/agent_design/retrieval_strategy.md). 도입 여부는 open_decisions.md D7에서 미정이며,
    fallback을 지금 붙이면 미정인 경로가 기본 동작이 된다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않고 실행 상태로 표현한다.
  Pattern 내부 표현(DocumentSummaryResult)이 필요한 호출자는 build_document_summary_result를
  직접 쓴다.

TODO:
- Q05(기간 미지정)의 기본 문서 선택. open_decisions.md D5-a가 정해진 뒤에 연다.
- Q06(거래소공시 단건 요약)의 판본 선택. open_decisions.md D5가 정해진 뒤에 연다.
- Section 내부의 항목 단위 분할. periodic.sections의 TODO 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_FILING,
    REASON_NO_MATCHING_FILING,
    REASON_PARSE_FAILED,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    STAGE_PATTERN_ENTRY,
    STAGE_SECTION_NAVIGATION,
    BlockedInfo,
)
from disclosure_agent.agent.normalization import normalize_document_summary_result
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import SectionScope
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.periodic.sections import (
    SectionContentItem,
    SectionNavigationError,
    SectionSelection,
)
from disclosure_agent.periodic.service import collect_scope_content, load_parsed_document
from disclosure_agent.retrieval.metadata_filter import (
    DOCUMENT_CLASS_PERIODIC,
    FilingNotFoundError,
    FilingRef,
    list_filings,
)

__all__ = [
    "REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS",
    "REASON_UNSUPPORTED_SUMMARY_PERIOD",
    "REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE",
    "DocumentSummaryResult",
    "build_document_summary_result",
    "handle_document_summary",
]

# 이 handler가 계획 입구에서 만드는 사유.
#
# Router는 intent 하나만 보고 handler를 고르므로, document_summary로 분류된 계획이 전부 이번
# 구현 범위인 것은 아니다. 막는 자리를 입구에 두는 것은 Pattern C·D와 같은 규약이다.
REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS = "unsupported_summary_document_class"
REASON_UNSUPPORTED_SUMMARY_PERIOD = "unsupported_summary_period"
REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE = "unsupported_summary_section_scope"

# 이번 구현이 요약하는 문서 수. 문서 1건 -> 지정 범위가 Pattern B의 성격이다.
SUMMARIZED_DOCUMENT_COUNT = 1


@dataclass(frozen=True)
class DocumentSummaryResult:
    """
    Pattern B의 구조화 결과.

    공통 Contract가 아니다. 여기까지는 이 Pattern이 확보한 것을 그대로 들고, 공통 타입으로
    옮기는 일은 agent.normalization이 한다.

    document를 들고 있는 이유는 Pattern A와 같다. SectionContentItem -> Contract
    DocumentSummary/Evidence 변환(periodic.adapter)이 문서 metadata의 doc_id·corp_name·
    report_name을 요구하며, 그 값을 다시 읽으려고 문서를 두 번 파싱하지 않는다.

    blocked가 있으면 selection도 items도 없다. 범위를 확정하지 못한 채로 내용을 모으지 않기
    때문이다.
    """

    plan: QueryPlan
    filing: FilingRef | None = None
    document: dict[str, Any] | None = None
    selection: SectionSelection | None = None
    items: tuple[SectionContentItem, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None


def handle_document_summary(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다.
    """
    return normalize_document_summary_result(
        build_document_summary_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )


def _reject_unsupported_plan(plan: QueryPlan) -> BlockedInfo | None:
    """
    이번 구현이 실행할 수 없는 계획을 입구에서 가려낸다.

    두 가지만 본다. 나머지는 query_analyzer가 계획을 만들 때 이미 확정했다.
    section_scope가 비어 있는 계획은 QueryPlan.__post_init__이 아예 만들지 못하게 막으므로
    여기서 다시 보지 않는다.
    """
    if plan.document_class != DOCUMENT_CLASS_PERIODIC:
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS,
            detail=(
                f"이번 구현은 정기공시 단건 요약만 실행합니다: "
                f"document_class={plan.document_class!r}. 거래소공시 단건 요약은 원공시와 "
                "정정본 중 무엇을 기준으로 할지 정하는 판본 선택 정책이 필요하며, 그 정책은 "
                "아직 정해지지 않았습니다(docs/agent_design/open_decisions.md D5)."
            ),
        )

    if len(plan.years) != SUMMARIZED_DOCUMENT_COUNT:
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_SUMMARY_PERIOD,
            detail=(
                f"단일 문서 요약은 회계연도 하나를 요구합니다: years={plan.years!r}. "
                "기간이 비어 있으면 어떤 문서를 기본값으로 고를지 정하는 정책이 필요하고"
                "(docs/agent_design/open_decisions.md D5-a), 둘 이상이면 문서 1건으로 "
                "좁혀지지 않습니다. 한 기간을 골라 답하지 않습니다."
            ),
        )

    return None


def _select_filing(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> tuple[FilingRef | None, BlockedInfo | None]:
    """
    질문이 가리키는 공시를 1건으로 확정한다.

    후보를 전부 받아 개수로 판정한다. 0건이면 코퍼스에 그 문서가 없다는 확정 판정이고
    (docs/agent_design/evidence_and_answerability.md의 [4]단계), 2건 이상이면 어느 문서를
    요약할지 정할 근거가 없다는 판정이다. 둘은 후속 조치가 다르므로 사유를 나눈다.
    """
    resolved_manifest = (
        Path(manifest_path) if manifest_path else Path(corpus_root) / "manifest.jsonl"
    )

    try:
        candidates = list_filings(
            manifest_path=resolved_manifest,
            corpus_root=corpus_root,
            company=plan.company,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            year=plan.year,
        )

    except FilingNotFoundError as error:
        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    if not candidates:
        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_NO_MATCHING_FILING,
            detail=(
                "manifest에서 조건에 맞는 공시를 찾지 못했습니다: "
                f"{plan.company} / {plan.year} / {plan.document_class} / "
                f"{plan.document_subtype}"
            ),
        )

    if len(candidates) > 1:
        listed = ", ".join(candidate.doc_id for candidate in candidates)

        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_AMBIGUOUS_FILING,
            detail=(
                f"조건에 맞는 공시가 {len(candidates)}건입니다: {listed}. "
                "요약 대상 문서를 하나 골라 답하지 않습니다."
            ),
        )

    return candidates[0], None


def build_document_summary_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> DocumentSummaryResult:
    """
    단일 문서 요약 계획 하나를 구조화 결과까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다. 어느 단계에서 왜 막혔는지
    호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    entry_block = _reject_unsupported_plan(plan)

    if entry_block is not None:
        return DocumentSummaryResult(plan=plan, blocked=entry_block)

    filing, blocked = _select_filing(
        plan,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )

    if filing is None:
        # _select_filing은 공시를 확정하지 못하면 반드시 blocked를 함께 돌려준다.
        # 그래도 여기서 다시 보는 이유는, 둘 다 비어 있는 결과가 나오면 아래 경로가 문서
        # 없이 진행하기 때문이다.
        return DocumentSummaryResult(
            plan=plan,
            blocked=blocked
            or BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail="공시를 확정하지 못했는데 그 사유가 남지 않았습니다.",
            ),
        )

    try:
        document = load_parsed_document(filing.main_document)

    except (OSError, ValueError) as error:
        return DocumentSummaryResult(
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
) -> DocumentSummaryResult:
    """
    이미 파싱된 문서에서 범위 확정과 내용 수집까지 한다.

    parser 호출과 분리해 두어 synthetic parsed document로도 검증할 수 있다. Pattern A가
    같은 이유로 같은 자리를 두고 있다.
    """
    section_scope = plan.section_scope

    if not isinstance(section_scope, SectionScope):
        # QueryPlan.__post_init__이 document_summary 계획에서 이 값을 요구하므로 도달하지
        # 않는다. 다만 Contract 어휘 밖의 문자열이 들어오면 아래 탐색이 규칙을 찾지 못하고
        # 그 사실이 "범위에 해당하는 Section이 없다"로 보고된다. 원인이 다른 두 정지가 같은
        # 사유로 뭉개지지 않도록 여기서 나눈다.
        return DocumentSummaryResult(
            plan=plan,
            filing=filing,
            document=document,
            blocked=BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE,
                detail=(
                    "Contract 어휘가 아닌 section_scope 값입니다: "
                    f"{section_scope!r}. 가까운 범위로 대신 요약하지 않습니다."
                ),
            ),
        )

    try:
        selection, items = collect_scope_content(document, section_scope=section_scope)

    except SectionNavigationError as error:
        return DocumentSummaryResult(
            plan=plan,
            filing=filing,
            document=document,
            blocked=BlockedInfo(
                stage=STAGE_SECTION_NAVIGATION,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return DocumentSummaryResult(
        plan=plan,
        filing=filing,
        document=document,
        selection=selection,
        items=tuple(items),
    )
