"""Pattern B — Section Summary handler.

역할:
- 문서 1건을 확정하고, 질문이 지정한 범위 안의 본문·표를 근거와 함께 모은다.

현재 구현 범위:
- 대표 질문 Q04 계열("<기업> <연도>년 사업보고서에서 <범위> 관련 내용 정리해줘").
- 대표 질문 Q05 계열("<기업>는 무슨 사업을 하고 주력 제품이 뭐야?"). 문서 1건에서 범위
  둘(사업의 개요, 주요 제품)을 각각 확정해 DocumentSummary 2건으로 싣는다. 계획의
  section_scopes가 이 경로를 가른다(_handle_multi_scope_summary).

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

    Q05 경로(section_scopes)는 정정본을 후보에 포함한다. 그 경로는 질문이 연도를 밝히지 않아
    코퍼스 기준 최신 회계연도로 옮긴 계획이며, 정정본을 빼면 원본과 정정본이 함께 있는 해가
    원본 1건으로 보여 "유일하게 확정됐다"고 진행하게 된다. 그것은 정책 없이 원본을 고른
    것이다. 그래서 Q09(temporal_compare)와 같은 규칙을 쓴다. 후보가 둘 이상이면 어느 것도
    고르지 않고 ambiguous_filing으로 멈춘다. Q04 경로의 규칙은 바꾸지 않는다.

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
- Q05 이외의 기간 미지정 요약의 기본 문서 선택. open_decisions.md D5-a가 정해진 뒤에 연다.
- Q06(거래소공시 단건 요약)의 판본 선택. open_decisions.md D5가 정해진 뒤에 연다.
- Section 내부의 항목 단위 분할. periodic.sections의 TODO 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from disclosure_agent.agent.query_analyzer import (
    BUSINESS_PROFILE_SECTION_SCOPES,
    DOCUMENT_SUBTYPE_ANNUAL,
    QueryPlan,
)
from disclosure_agent.agent.source_documents import merge_source_documents
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ExecutionStatus,
    SectionScope,
)
from disclosure_agent.common.schemas import AgentExecutionResult, SourceDocument
from disclosure_agent.periodic.sections import (
    REASON_AMBIGUOUS_SECTION,
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


# 이 경로가 실행하는 범위 묶음. 둘뿐이며, 부분집합이 아니라 열거다.
#
# 열거인 이유:
#     "Q05의 범위로만 이뤄져 있는가"로 두면 (주요 제품,) 하나짜리 묶음도 통과한다. 그 묶음은
#     사업 개요 없이 제품 절만 요약하라는 계획인데, 이 구현에서 확인된 적이 없는 모양이다.
#     질의 해석 계층은 그것을 만들지 않지만(사업 서술 표현을 늘 요구한다), 계획을 직접 만드는
#     호출자에게는 그 방어가 없다. 입구 가드의 몫은 그 호출자를 막는 것이므로, 여기서
#     "무엇이 아닌가"가 아니라 "무엇인가"를 적는다.
#
# 차례를 함께 고정하는 이유:
#     계획을 만드는 쪽이 BUSINESS_PROFILE_SECTION_SCOPES의 차례를 그대로 쓴다
#     (query_analyzer._business_profile_section_scopes). 그 차례는 사업보고서 서식의 Section
#     순서이고 결과가 실리는 순서이기도 하다. 차례가 다른 묶음은 이 경로가 만든 것이 아니므로
#     받지 않는다. 같은 범위가 거듭 담기는 것은 QueryPlan.__post_init__이 이미 막는다.
#
# 두 번째 값을 다시 적지 않고 잇는 이유:
#     Q05의 범위 묶음은 query_analyzer가 정하는 값이다. 여기에 같은 튜플을 다시 적으면 그쪽이
#     늘 때 한쪽만 늘어난다.
SUPPORTED_SECTION_SCOPE_BUNDLES: tuple[tuple[SectionScope, ...], ...] = (
    (SectionScope.BUSINESS_OVERVIEW,),
    BUSINESS_PROFILE_SECTION_SCOPES,
)


def _is_business_profile_scope_bundle(
    section_scopes: tuple[SectionScope, ...],
) -> bool:
    """이 범위 묶음이 이 경로가 실행하는 두 묶음 중 하나인가.

    빈 묶음은 거짓이다. 그 계획은 범위 하나(section_scope)로 실행되는 계획이며, 묶음 경로가
    아니다.
    """
    return section_scopes in SUPPORTED_SECTION_SCOPE_BUNDLES


def handle_document_summary(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다.

    계획의 section_scopes가 이 경로가 실행하는 두 묶음(SUPPORTED_SECTION_SCOPE_BUNDLES)
    중 하나일 때만 범위 묶음 경로다. 그 경로는 정정본을 후보에 포함하는 조회 규칙을 쓰므로,
    묶음이 비어 있지 않다는 것만으로 열면 다른 범위가 그 규칙을 물려받는다. 그 밖의 묶음은
    범위 하나의 경로로 보내 입구 가드(_reject_unsupported_plan)가 막게 한다. 범위 하나로
    바꿔 읽지 않는다.
    """
    if _is_business_profile_scope_bundle(plan.section_scopes):
        return _handle_multi_scope_summary(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    return normalize_document_summary_result(
        build_document_summary_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )


def _handle_multi_scope_summary(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> AgentExecutionResult:
    """
    문서 1건에서 범위 여럿을 각각 확정해 DocumentSummary 여러 건으로 싣는다(Q05).

    공시 확정과 파싱은 한 번만 한다. 범위마다 다시 찾으면 같은 질문 안에서 범위별로 다른
    문서를 볼 수 있고, 그 어긋남은 결과만 봐서는 드러나지 않는다.

    범위 하나라도 확정하지 못하면 전부 멈춘다. 확정한 범위만 실어 보내면 질문이 물은
    나머지 범위가 사라진 사실이 결과에 남지 않는다. 멈춘 사유는 그 범위를 단독으로
    요약했을 때와 같은 사유다(normalize_document_summary_result가 그대로 옮긴다).

    범위마다의 확정·수집·변환은 범위 하나의 경로(build_result_from_document,
    normalize_document_summary_result)를 그대로 쓴다. 여기서 같은 일을 다시 적으면 두 벌이
    된다. 범위별 계획은 원래 계획의 section_scope 자리에 그 범위 하나를 넣은 복제본이다.

    source_documents도 그 범위별 결과에서 그대로 이어 붙인다. 범위 하나의 경로가 실제로
    선택된 FilingRef에서 이미 만들어 두었으므로, 이 자리가 manifest를 다시 찾거나
    Evidence.doc_id에서 metadata를 지을 이유가 없다. 이어 붙이는 규칙은 아래 return에 있다.
    """
    entry_block = _reject_unsupported_plan(plan)

    if entry_block is not None:
        return normalize_document_summary_result(
            DocumentSummaryResult(plan=plan, blocked=entry_block)
        )

    filing, blocked = _select_filing(
        plan,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
        include_corrections=True,
    )

    if filing is None:
        return normalize_document_summary_result(
            DocumentSummaryResult(
                plan=plan,
                blocked=blocked
                or BlockedInfo(
                    stage=STAGE_FILING_DISCOVERY,
                    reason=REASON_NO_MATCHING_FILING,
                    detail="공시를 확정하지 못했는데 그 사유가 남지 않았습니다.",
                ),
            )
        )

    try:
        document = load_parsed_document(filing.main_document)

    except (OSError, ValueError) as error:
        return normalize_document_summary_result(
            DocumentSummaryResult(
                plan=plan,
                filing=filing,
                blocked=BlockedInfo(
                    stage=STAGE_PARSING,
                    reason=REASON_PARSE_FAILED,
                    detail=str(error),
                ),
            )
        )

    summaries = []
    evidence = []
    source_documents: list[SourceDocument] = []
    warnings: list[str] = []

    # 범위별로 확정한 Section과 실은 근거의 식별자.
    #
    # 두 범위가 같은 Section으로 확정되면 같은 근거가 두 번 실리고, 답은 서로 다른 두 범위를
    # 찾은 것처럼 말하게 된다. 규칙 둘이 한 제목에 함께 걸리는 서식("사업의 개요 및 주요
    # 제품")이 그 경우다. 그 Section이 어느 범위의 것인지 정할 근거가 없으므로, Section
    # 탐색이 후보를 하나로 좁히지 못했을 때와 같은 사유(ambiguous_section)로 멈춘다. 근거
    # 식별자는 Section 식별자에서 나오므로 두 검사는 같은 것을 보지만, 근거 쪽 검사가 있어야
    # 식별자 규칙이 바뀌어도 중복 근거가 그대로 나가지 않는다.
    section_scope_by_id: dict[str, SectionScope] = {}
    evidence_ids: set[str] = set()

    for section_scope in plan.section_scopes:
        scoped_plan = replace(plan, section_scope=section_scope, section_scopes=())

        part = build_result_from_document(document, plan=scoped_plan, filing=filing)

        if part.selection is not None:
            claimed_by = section_scope_by_id.get(part.selection.section_id)

            if claimed_by is not None:
                return _blocked_scope_collision(
                    part,
                    detail=(
                        f"'{claimed_by.value}'와 '{section_scope.value}' 범위가 같은 Section "
                        f"'{' > '.join(part.selection.section_path)}'으로 확정됐습니다. "
                        "한 Section을 두 범위로 나눠 요약하지 않습니다."
                    ),
                )

            section_scope_by_id[part.selection.section_id] = section_scope

        normalized = normalize_document_summary_result(part)

        if normalized.answerability_status is not AnswerabilityStatus.ANSWERABLE:
            return normalized

        repeated = sorted(
            {item.evidence_id for item in normalized.evidence if item.evidence_id in evidence_ids}
        )

        if repeated:
            return _blocked_scope_collision(
                part,
                detail=(
                    f"'{section_scope.value}' 범위의 근거가 앞서 확정한 범위의 근거와 겹칩니다: "
                    f"{', '.join(repeated)}. 같은 근거를 두 범위에 나눠 싣지 않습니다."
                ),
            )

        evidence_ids.update(item.evidence_id for item in normalized.evidence)

        summaries.extend(normalized.document_summaries)
        evidence.extend(normalized.evidence)
        source_documents.extend(normalized.source_documents)

        for warning in normalized.warnings:
            if warning not in warnings:
                warnings.append(warning)

    # 범위마다의 source_documents를 그대로 이어 붙인다.
    #
    # 범위별 결과는 이미 범위 하나의 경로(normalize_document_summary_result)가 실제로 선택된
    # FilingRef 하나에서 만든 것이다. 여기서 manifest를 다시 찾지도, Evidence.doc_id나 질문
    # 문자열에서 metadata를 짓지도 않는다 — 이 경로는 문서를 한 번만 확정하므로(위 _select_filing)
    # 모든 범위가 같은 FilingRef를 본다.
    #
    # merge_source_documents를 쓰는 이유:
    #     그 하나의 문서가 범위 수만큼 반복해 들어온다. AgentExecutionResult는 doc_id 중복을
    #     거절하므로(common.schemas._require_unique_source_document_doc_ids) 그대로 이으면
    #     실행이 멈춘다. 중복 제거 규칙을 여기서 다시 적는 대신 Pattern D가 쓰는 것과 같은
    #     함수를 쓴다 — 같은 doc_id가 서로 다른 metadata로 나오면 조용히 하나를 고르지 않고
    #     ConflictingSourceDocumentError로 멈추는 fail-close도 함께 물려받는다.
    #
    # 정렬하지 않는다. plan.section_scopes 차례가 곧 문서가 처음 등장한 차례이고, 그 차례는
    # summaries·evidence가 실리는 차례와 같다.
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        document_summaries=summaries,
        evidence=evidence,
        source_documents=merge_source_documents(source_documents),
        warnings=warnings,
    )


def _blocked_scope_collision(
    part: DocumentSummaryResult,
    *,
    detail: str,
) -> AgentExecutionResult:
    """
    범위 묶음 경로에서 두 범위가 한 Section이나 한 근거를 함께 가리킨 경우의 정지.

    사유는 Section 탐색의 ambiguous_section을 그대로 쓴다. 문서는 열었고 범위마다 Section을
    찾았지만 그 Section이 어느 범위의 것인지 하나로 정하지 못한 것이므로, 같은 단계의 같은
    판정이다. 새 사유를 만들면 같은 상황이 두 이름을 갖는다.
    """
    return normalize_document_summary_result(
        DocumentSummaryResult(
            plan=part.plan,
            filing=part.filing,
            document=part.document,
            blocked=BlockedInfo(
                stage=STAGE_SECTION_NAVIGATION,
                reason=REASON_AMBIGUOUS_SECTION,
                detail=detail,
            ),
        )
    )


def _reject_unsupported_plan(plan: QueryPlan) -> BlockedInfo | None:
    """
    이번 구현이 실행할 수 없는 계획을 입구에서 가려낸다.

    문서군과 세부유형을 함께 본다:
        이번 구현 범위는 정기공시 중에서도 사업보고서 한 종류다. 문서군만 확인하면 반기·분기
        보고서를 대상으로 한 계획이 그대로 통과하고, Section 규칙(periodic.sections)은
        사업보고서 서식을 전제로 만들어져 있으므로 다른 서식에서 무엇을 고를지 보증되지 않는다.
        지금은 분기·반기 표현을 query_analyzer가 의도 분기보다 앞에서 거절해 그런 계획이
        만들어지지 않지만, 계획을 직접 만드는 호출자에게는 그 방어가 없다.

    section_scope가 비어 있는 계획은 QueryPlan.__post_init__이 아예 만들지 못하게 막으므로
    여기서 다시 보지 않는다.

    범위 묶음은 Q05가 다루는 범위만 받는다:
        범위 묶음 경로는 정정본을 후보에 포함하는 조회 규칙과 범위 간 충돌 검사를 그 범위들에
        맞춰 두었다. 그 밖의 범위가 들어오면 그 규칙을 물려받거나, 범위 하나의 경로가
        section_scope 없이 진행하게 된다. 어느 쪽도 계획이 요청한 것과 다르므로 입구에서 막는다.

        받는 묶음을 열거하는 이유:
            질문이 사업만 물었으면 계획도 사업의 개요 하나만 담는다
            (query_analyzer._business_profile_section_scopes). 그 계획은 같은 질문 모양,
            같은 문서, 같은 조회 규칙이며 범위가 하나 적을 뿐이므로 받는다.

            그렇다고 "Q05의 범위로만 이뤄져 있으면 받는다"로 두지는 않는다. 그러면
            (주요 제품,) 하나짜리 묶음까지 통과하는데, 사업 개요 없이 제품 절만 요약하는
            모양은 이 구현에서 확인된 적이 없다. 받는 것을 열거해 두 묶음으로 못 박는다
            (SUPPORTED_SECTION_SCOPE_BUNDLES).
    """
    if plan.section_scopes and not _is_business_profile_scope_bundle(plan.section_scopes):
        supported = " / ".join(
            "(" + ", ".join(scope.value for scope in bundle) + ")"
            for bundle in SUPPORTED_SECTION_SCOPE_BUNDLES
        )
        requested = ", ".join(scope.value for scope in plan.section_scopes)

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_SUMMARY_SECTION_SCOPE,
            detail=(
                f"지원하지 않는 범위 묶음입니다: ({requested}). 실행하는 묶음은 "
                f"{supported}뿐입니다. 범위 하나의 요약으로 바꿔 읽지 않습니다."
            ),
        )

    if (
        plan.document_class != DOCUMENT_CLASS_PERIODIC
        or plan.document_subtype != DOCUMENT_SUBTYPE_ANNUAL
    ):
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_SUMMARY_DOCUMENT_CLASS,
            detail=(
                "이번 구현은 정기공시 사업보고서 단건 요약만 실행합니다: "
                f"document_class={plan.document_class!r}, "
                f"document_subtype={plan.document_subtype!r}. 거래소공시 단건 요약은 원공시와 "
                "정정본 중 무엇을 기준으로 할지 정하는 판본 선택 정책이 필요하며, 그 정책은 "
                "아직 정해지지 않았습니다(docs/agent_design/open_decisions.md D5). "
                "반기·분기보고서는 Section 규칙이 사업보고서 서식을 전제로 하고 있어 "
                "확인되지 않은 범위를 고르지 않습니다."
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
    include_corrections: bool = False,
) -> tuple[FilingRef | None, BlockedInfo | None]:
    """
    질문이 가리키는 공시를 1건으로 확정한다.

    후보를 전부 받아 개수로 판정한다. 0건이면 코퍼스에 그 문서가 없다는 확정 판정이고
    (docs/agent_design/evidence_and_answerability.md의 [4]단계), 2건 이상이면 어느 문서를
    요약할지 정할 근거가 없다는 판정이다. 둘은 후속 조치가 다르므로 사유를 나눈다.

    include_corrections의 기본값은 Q04 경로의 규칙(정정본 제외)이다. Q05 경로가 참을 넘긴다
    (모듈 docstring 참조).
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
            include_corrections=include_corrections,
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
        scope_note = "(정정본 포함) " if include_corrections else ""

        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_AMBIGUOUS_FILING,
            detail=(
                f"조건에 맞는 공시{scope_note}가 {len(candidates)}건입니다: {listed}. "
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
