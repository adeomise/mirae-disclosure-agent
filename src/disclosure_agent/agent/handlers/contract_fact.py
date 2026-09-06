"""Q02 — 거래소공시 계약 단건 사실 조회 handler.

역할:
- 계약 조회 계획 하나를 받아 공시 1건을 확정하고, 질문이 요구한 값을 Fact/Evidence까지
  확보하는 흐름을 조정한다.

    QueryPlan
      -> 공시 후보 확정      (agent.event_retrieval.select_contract_filing)
      -> canonical 이벤트     (event.parser / event.extractor, 위 함수가 이미 읽어 둔 것)
      -> Fact/Evidence       (event.adapter.adapt_contract_facts)
      -> ContractFactResult
      -> AgentExecutionResult (agent.normalization)

Pattern A와 나누는 이유:
    저쪽은 정기공시에서 회계 지표 하나를 읽고, 이쪽은 거래소공시 1건에서 계약을 서술하는 여러
    값을 함께 읽는다. 문서군도 값의 성질도 다르다. 한 handler에 담으면 검증된 Pattern A의
    경로가 계약 조회 때문에 흔들린다.

문서를 고르지 않는다:
    후보를 1건으로 좁히는 판정은 agent.event_retrieval이 이미 내렸다. 여기서는 그 판정을
    실행 결과로 옮기기만 한다. 좁히지 못한 경우에 이 자리에서 하나를 고르면, retrieval이
    막으려고 만든 규칙이 handler에서 그대로 뚫린다.

값을 채우지 않는다:
    요구한 필드를 전부 확보하거나 하나도 담지 않는다. 그 규칙은 event.adapter가 가지고
    있으며(adapt_contract_facts), 여기서 예외를 잡아 부분 결과로 바꾸지 않는다.

입력:
- QueryPlan(intent=contract_fact), corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않고 실행 상태로 표현한다.

TODO:
- 상대방 없이도 후보가 유일하면 답하는 경로(현재는 상대방을 요구한다)
- 판본 정책(D5) 확정 후 정정본 반영
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_ROOT,
    REASON_NO_MATCHING_FILING,
    REASON_PARSE_FAILED,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    BlockedInfo,
)
from disclosure_agent.agent.event_retrieval import (
    ContractCandidateReadError,
    ContractSelectionStatus,
    select_contract_filing,
)
from disclosure_agent.agent.normalization import normalize_contract_fact_result
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.schemas import AgentExecutionResult, Evidence, Fact
from disclosure_agent.event.adapter import (
    MissingContractFieldError,
    UnsupportedContractMetricError,
    adapt_contract_facts,
)
from disclosure_agent.retrieval.metadata_filter import FilingNotFoundError, FilingRef

# 이 handler가 남기는 정지 사유.
#
# answer_builder에 두지 않는 이유는 아직 이 Pattern 하나만 쓰기 때문이다. 답변 가능성
# 대응표(agent.normalization.ANSWERABILITY_BY_STOP)가 같은 문자열을 참조한다. 그쪽이 이미
# no_matching_label_fallback을 같은 방식으로 참조하고 있다.
REASON_MISSING_COUNTERPARTY = "missing_counterparty"
REASON_MISSING_REQUESTED_FIELDS = "missing_requested_fields"
REASON_NO_MATCHING_COUNTERPARTY = "no_matching_counterparty"
REASON_MISSING_CONTRACT_FIELD = "missing_contract_field"
REASON_UNSUPPORTED_CONTRACT_METRIC = "unsupported_contract_metric"


@dataclass(frozen=True)
class ContractFactResult:
    """
    Q02의 구조화 결과.

    공통 Contract가 아니다. 다만 facts/evidence는 이미 Contract 타입이다. event.adapter가
    변환까지 끝낸 값을 받기 때문이며, 여기서 다시 변환하면 같은 변환이 두 벌이 된다.
    Pattern A가 같은 자리에서 반대 선택을 한 것은 그쪽 변환이 문서 metadata를 더 요구하기
    때문이고, 이쪽은 그럴 것이 없다.

    candidate_doc_ids는 확정하지 못했을 때의 후보 목록이다. 확정한 경우에는 비어 있다.
    어느 후보들 사이에서 막혔는지를 결과에 남기기 위한 자리다.

    blocked가 있으면 facts/evidence는 없다. 값을 추측해 채우지 않기 때문이다.
    """

    plan: QueryPlan
    filing: FilingRef | None = None
    facts: tuple[Fact, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    candidate_doc_ids: tuple[str, ...] = ()
    blocked: BlockedInfo | None = None


def handle_contract_fact(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_contract_fact_result를 직접 쓴다.
    """
    return normalize_contract_fact_result(
        build_contract_fact_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )


def _blocked(
    plan: QueryPlan,
    stage: str,
    reason: str,
    detail: str,
    *,
    candidate_doc_ids: tuple[str, ...] = (),
) -> ContractFactResult:
    return ContractFactResult(
        plan=plan,
        candidate_doc_ids=candidate_doc_ids,
        blocked=BlockedInfo(stage=stage, reason=reason, detail=detail),
    )


def build_contract_fact_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> ContractFactResult:
    """
    계약 조회 계획 하나를 구조화 결과까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다. 어느 단계에서 왜 막혔는지
    호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    if not plan.requested_fields:
        # 무엇을 답해야 하는지가 계획에 없다. 기본 필드를 골라 채우면 질문이 요구하지 않은
        # 값을 답하게 된다.
        return _blocked(
            plan,
            STAGE_CONTRACT_ADAPTATION,
            REASON_MISSING_REQUESTED_FIELDS,
            "계획에 요구 필드가 없습니다. 답할 값을 임의로 정하지 않습니다.",
        )

    if not plan.counterparty:
        # 상대방 없이 같은 달의 여러 계약 중 하나를 고르지 않는다. 후보가 유일한 경우까지
        # 함께 막히는 것은 알려진 한계이며, 그 경로는 아직 열지 않았다.
        return _blocked(
            plan,
            STAGE_FILING_DISCOVERY,
            REASON_MISSING_COUNTERPARTY,
            (
                "질문에서 계약 상대방을 읽지 못했습니다. 상대방 없이 후보 중 하나를 "
                "고르지 않습니다."
            ),
        )

    # QueryPlan을 검색 조건으로 옮기는 일은 여기서 한다.
    # agent.event_retrieval은 질문 해석 결과를 모르고 primitive만 받는다.
    try:
        selection = select_contract_filing(
            company=plan.company,
            year=plan.year,
            month=plan.month,
            counterparty=plan.counterparty,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    except ContractCandidateReadError as error:
        # 후보를 읽지 못한 채로 세면 후보가 둘인 상황이 하나로 보인다. 조용히 넘기지 않는다.
        return _blocked(plan, STAGE_PARSING, REASON_PARSE_FAILED, str(error))

    except FilingNotFoundError as error:
        return _blocked(plan, STAGE_FILING_DISCOVERY, error.reason, error.detail)

    if selection.status is ContractSelectionStatus.AMBIGUOUS:
        # 하나를 고르지 않는다. 고르면 다른 계약의 금액을 그 계약의 금액이라고 답하게 된다.
        return _blocked(
            plan,
            STAGE_FILING_DISCOVERY,
            REASON_AMBIGUOUS_ROOT,
            selection.detail,
            candidate_doc_ids=selection.matched_doc_ids,
        )

    if selection.status is ContractSelectionStatus.NOT_FOUND:
        # 후보가 남아 있으면 "그런 공시가 없다"가 아니라 "그 상대방의 계약이 없다"이다.
        # 다른 상대방의 계약으로 대신하지 않는다.
        reason = (
            REASON_NO_MATCHING_COUNTERPARTY
            if selection.candidate_doc_ids
            else REASON_NO_MATCHING_FILING
        )

        return _blocked(
            plan,
            STAGE_FILING_DISCOVERY,
            reason,
            selection.detail,
            candidate_doc_ids=selection.candidate_doc_ids,
        )

    selected = selection.selected

    if selected is None:  # pragma: no cover - 위 분기가 이미 가른다
        raise AssertionError("selected 상태인데 문서가 없습니다.")

    try:
        adapted = adapt_contract_facts(
            selected.event,
            # 거래소공시 본문은 회사명을 담지 않는다. manifest가 확정한 값을 넘긴다.
            company=selected.filing.corp_name,
            metrics=plan.requested_fields,
        )

    except MissingContractFieldError as error:
        # 문서는 확정했는데 요구한 값이 그 문서에 없다. 다른 문서에서 채우지 않고,
        # 확보한 것만으로 부분 답을 만들지도 않는다.
        return _blocked(
            plan,
            STAGE_EXTRACTION,
            REASON_MISSING_CONTRACT_FIELD,
            f"{selection.matched_doc_ids[0]}: {error}",
        )

    except UnsupportedContractMetricError as error:
        return _blocked(
            plan,
            STAGE_CONTRACT_ADAPTATION,
            REASON_UNSUPPORTED_CONTRACT_METRIC,
            str(error),
        )

    return ContractFactResult(
        plan=plan,
        filing=selected.filing,
        facts=tuple(adapted.facts),
        evidence=tuple(adapted.evidence),
    )
